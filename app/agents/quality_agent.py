"""Agent 4: 质量保障 Agent。

职责：SimHash 去重 + 反幻觉校验。

核心能力：
- SimHash 64 位去重（三阶段批量 + SAVEPOINT）
- 反幻觉校验（金额归一化 + 日期归一化 + 事实比对）
- 溯源引用（每条数据标注来源 URL + 提取片段）

复用：app/processors/simhash.py + app/processors/hallucination_checker.py
"""

from __future__ import annotations

from typing import Any

from app.utils.logger import get_logger

logger = get_logger("agent.quality")


async def quality_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Agent 4: 质量保障（SimHash 去重 + 反幻觉校验）。

    输入 state:
        - process_summary: dict — 加工结果（来自 processor_agent）
        - subscription_id: int — 订阅 ID
        - collect_summary: dict — 采集结果

    输出 state（新增）:
        - quality_summary: dict — 质检结果摘要
            - total_checked: int — 质检总数
            - total_verified: int — 实际与原文比对核验数（无原文不计入）
            - duplicates_removed: int — 去重数
            - hallucination_flags: int — 反幻觉标记数
            - quality_score: float — 质量评分（0-1）
    """
    collect_summary = state.get("collect_summary") or {}
    process_summary = state.get("process_summary") or {}
    processed_tenders = state.get("processed_tenders") or []

    logger.info(
        "quality_agent started total_collected={} total_processed={} extracted={}",
        collect_summary.get("total", 0),
        process_summary.get("total_processed", 0),
        len(processed_tenders),
    )

    # P1 修复：SimHash 64 位内容级去重（核心卖点，原实现完全未接入）
    # 对每条公告的 source_raw_text 计算 SimHash，汉明距离<=3 视为近似重复
    from app.processors.simhash import compute_simhash, find_duplicate_in_iter
    import asyncio as _asyncio

    def _compute_simhash_batch(texts: list[str]) -> list[int]:
        """批量计算 SimHash（在线程池中执行，避免阻塞事件循环）。"""
        return [compute_simhash(t) for t in texts]

    simhash_duplicates = 0
    if processed_tenders:
        texts = [pt.get("source_raw_text") or "" for pt in processed_tenders]
        # CPU 密集任务 offload 到线程池（project_memory 硬约束）
        simhashes = await _asyncio.to_thread(_compute_simhash_batch, texts)

        _simhash_seen: list[tuple[int, int]] = []  # (原始索引, simhash)
        _deduped: list[dict[str, Any]] = []
        for idx, (pt, sh) in enumerate(zip(processed_tenders, simhashes)):
            # E4 修复：不直接修改原始 pt（避免 _simhash 泄入 quality_tenders/finance）
            if sh == 0:
                # 空文本无法计算指纹，保留待证据验证阶段处理
                _deduped.append(pt)
                continue
            dup = find_duplicate_in_iter(sh, _simhash_seen, threshold=3)
            if dup is not None:
                simhash_duplicates += 1
                logger.debug(
                    "simhash duplicate tender_idx={} matched_idx={} hamming<=3",
                    idx, dup[0],
                )
                continue
            _simhash_seen.append((idx, sh))
            _deduped.append(pt)

        if simhash_duplicates > 0:
            logger.info(
                "quality_agent simhash dedup removed={} remaining={}",
                simhash_duplicates, len(_deduped),
            )
        processed_tenders = _deduped
        # A2 修复：去重后同步更新 state["tender_ids"]，让 delivery 不再查到已去重的重复公告
        _deduped_ids = [pt.get("id") for pt in _deduped if pt.get("id") is not None]
        if _deduped_ids and simhash_duplicates > 0:
            state["tender_ids"] = _deduped_ids
            logger.info(
                "quality_agent updated tender_ids after dedup: {} -> {}",
                len(state.get("tender_ids", [])), len(_deduped_ids),
            )

    # 真实证据定位 + 程序验证（接 evidence_locator + field_validator）
    # 修复：原实现只拿 core_content 与 source_raw_text 自比（恒通过），
    # 核心卖点"证据定位 + 反幻觉校验"未接入
    from app.processors.evidence_locator._locator import EvidenceLocator
    from app.processors.evidence_locator._verify import verify_evidence
    from app.processors.field_validator import validate_field

    total_fields = 0
    verified_fields = 0
    unjustified_fields = 0
    hallucination_flags = 0
    verified_tenders: list[dict[str, Any]] = []

    for pt in processed_tenders:
        raw_text = pt.get("source_raw_text") or ""
        fields = pt.get("fields") or []
        if not raw_text or not fields:
            continue

        locator = EvidenceLocator(raw_text)
        tender_verified: list[dict[str, Any]] = []
        for f in fields:
            total_fields += 1
            field_name = f.get("field_name", "")
            raw_value = f.get("raw_value") or ""
            candidate_evidences = f.get("candidate_evidences") or []

            # 证据定位：在原文中定位候选证据
            located = False
            verified_evidence_text = ""
            for ce in candidate_evidences:
                ce_text = ce.get("evidence_text") or ""
                if not ce_text:
                    continue
                loc_result = locator.locate(ce_text)
                if loc_result.found and loc_result.location:
                    # 验证偏移量正确性
                    valid, _msg = verify_evidence(
                        raw_text, ce_text,
                        loc_result.location.start,
                        loc_result.location.end,
                    )
                    if valid:
                        located = True
                        verified_evidence_text = ce_text
                        break

            if located:
                verified_fields += 1
                f["evidence_verified"] = True
                f["verified_evidence"] = verified_evidence_text
            else:
                # 证据未定位到：标记为无依据（unjustified）
                unjustified_fields += 1
                f["evidence_verified"] = False

            # 确定性校验（金额/日期/编号三类字段）
            if raw_value and field_name in ("amount", "publish_date", "bid_deadline", "project_identifier"):
                try:
                    field_type = "amount" if field_name == "amount" else (
                        "date" if "date" in field_name or field_name == "bid_deadline" else "project_identifier"
                    )
                    vr = validate_field(field_type, raw_value)
                    f["deterministic_valid"] = vr.valid
                    if not vr.valid:
                        hallucination_flags += 1
                except Exception:  # noqa: BLE001
                    f["deterministic_valid"] = None

            tender_verified.append(f)

        verified_tenders.append({
            **pt,
            "fields": tender_verified,
        })

    # 质量评分：去重率 + 证据验证通过率（P2: 纳入幻觉惩罚）
    # dedup_rate 只反映内容去重（SimHash），不用采集层 URL 去重
    # （collect_summary.duplicates 是 URL 重复，与内容质量无关）
    total_checked = len(processed_tenders)
    if total_checked <= 1:
        dedup_rate = 1.0  # 单条或无数据不可能有内容重复
    else:
        dedup_rate = max(0.0, 1.0 - simhash_duplicates / total_checked)
    # P0 修复：0字段时通过率应为0.0而非1.0（无数据≠100%通过）
    evidence_pass_rate = (
        verified_fields / total_fields if total_fields > 0 else 0.0
    )
    # P2 修复：幻觉标记应拉低质量分（有幻觉≠高质量）
    hallucination_rate = (
        hallucination_flags / total_fields if total_fields > 0 else 0.0
    )
    base_score = (dedup_rate + evidence_pass_rate) / 2
    quality_score = base_score * (1.0 - hallucination_rate)

    # A3 修复：组织画像覆盖率写进 quality_summary（前端可见"本次分析覆盖 N/100 条"）
    _total_input = len(state.get("processed_tenders") or [])
    _llm_covered = sum(1 for pt in (state.get("processed_tenders") or [])
                       if pt.get("fields"))
    state["quality_summary"] = {
        "total_checked": len(processed_tenders),
        "total_fields": total_fields,
        "verified_fields": verified_fields,
        "unjustified_fields": unjustified_fields,
        "duplicates_removed": collect_summary.get("duplicates", 0),
        "simhash_duplicates": simhash_duplicates,
        "hallucination_flags": hallucination_flags,
        "hallucination_rate": round(hallucination_rate, 3),
        "quality_score": round(quality_score, 3),
        "dedup_rate": round(dedup_rate, 3),
        "evidence_pass_rate": round(evidence_pass_rate, 3),
        # A3: 覆盖率指标
        "llm_coverage_total": _total_input,
        "llm_coverage_extracted": _llm_covered,
        "llm_coverage_rate": round(_llm_covered / _total_input, 3) if _total_input > 0 else 0.0,
    }

    # 传递给 finance_agent 做六维观察信号计算
    state["quality_tenders"] = verified_tenders

    logger.info(
        "quality_agent completed total_checked={} fields={} verified={} unjustified={} simhash_dups={} halluc={} quality_score={:.3f}",
        len(processed_tenders), total_fields, verified_fields, unjustified_fields,
        simhash_duplicates, hallucination_flags, quality_score,
    )
    return state
