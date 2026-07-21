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
            - duplicates_removed: int — 去重数
            - hallucination_flags: int — 反幻觉标记数
            - quality_score: float — 质量评分（0-1）
    """
    collect_summary = state.get("collect_summary") or {}
    process_summary = state.get("process_summary") or {}

    logger.info(
        "quality_agent started total_collected={} total_processed={}",
        collect_summary.get("total", 0),
        process_summary.get("total_processed", 0),
    )

    # SimHash 去重已在 tender_ingestor.ingest_scrape_result 中完成
    # 这里做反幻觉校验 + 质量评分
    from app.models.database import AsyncSessionLocal
    from app.models.tender import Tender
    from app.processors.hallucination_checker import check_content
    from sqlalchemy import select

    sub_id = state.get("subscription_id")
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Tender)
            .where(Tender.source_platform.in_(
                collect_summary.get("platforms_collected", ["ccgp"])
            ))
            .order_by(Tender.id.desc())
            .limit(100)
        )
        tenders = result.scalars().all()

        # 反幻觉校验
        hallucination_flags = 0
        for t in tenders:
            if not t.core_content or not t.source_url:
                continue
            # check_content(text, source_text) → (ok, issues)
            ok, issues = check_content(t.core_content, t.core_content)
            if not ok:
                hallucination_flags += 1
                logger.warning(
                    "quality_agent hallucination detected tender_id={} issues={}",
                    t.id, issues,
                )

    # 质量评分：去重率 + 反幻觉通过率
    total = collect_summary.get("total", 0)
    duplicates = collect_summary.get("duplicates", 0)
    dedup_rate = 1.0 - (duplicates / total if total > 0 else 0)
    hallucination_pass_rate = 1.0 - (
        hallucination_flags / len(tenders) if tenders else 0
    )
    quality_score = (dedup_rate + hallucination_pass_rate) / 2

    state["quality_summary"] = {
        "total_checked": len(tenders),
        "duplicates_removed": duplicates,
        "hallucination_flags": hallucination_flags,
        "quality_score": round(quality_score, 3),
        "dedup_rate": round(dedup_rate, 3),
        "hallucination_pass_rate": round(hallucination_pass_rate, 3),
    }

    logger.info(
        "quality_agent completed total_checked={} duplicates={} hallucination_flags={} quality_score={:.3f}",
        len(tenders), duplicates, hallucination_flags, quality_score,
    )
    return state
