"""W2-08/W4 消融实验 A/B/C/D 四组运行器。"""
from __future__ import annotations

import sys

# 通过模块属性引用以支持测试 patch (patch("scripts.eval_ablation.call_extraction_llm"))
# 兼容 __main__ (直接运行) 和 scripts.eval_ablation (被 import) 两种执行模式
# 必须使用模块属性引用而非 from ... import，否则 patch 无法拦截
_ablation = sys.modules.get('scripts.eval_ablation') or sys.modules.get('__main__')

from app.processors.field_validator import (
    validate_amount, validate_date, validate_project_identifier,
    ValidationResult,
)
from app.processors.display_grade import compute_display_grade

from scripts.eval_ablation_types import GoldDoc, GroupResult
from scripts.eval_ablation_helpers import (
    _classify_gold_status,
    _value_correct,
    _collect_pred_values_by_name,
    _compute_mv_f1,
)


# ========== A 组：Direct LLM（直接输出字段，无证据要求）==========

async def run_group_a(doc: GoldDoc, raw_text: str) -> tuple[list[GroupResult], dict]:
    """A 组：直接调用 LLM，不要求证据（Sol 要求 W2-08 A 组用独立无证据 prompt）。

    修复 (P1)：原实现复用 call_extraction_llm (有证据 prompt) 仅评测时忽略证据，
    导致 LLM 仍被要求输出证据，不符合 "Direct LLM 无证据要求" 的实验目的。
    现改用 call_extraction_llm_no_evidence，使用独立的无证据 prompt + few-shot。
    """
    result = await _ablation.call_extraction_llm_no_evidence(raw_text)
    # A 组 LLM 失败检测（与 B 组一致）
    is_invalid = bool(result.error) or result.total_tokens == 0 or len(result.fields) == 0
    meta = {
        "model_id": result.model_id,
        "prompt_hash": result.prompt_hash,
        "total_tokens": result.total_tokens,
        "latency_ms": result.latency_ms,
        "error": result.error,
        "invalid": is_invalid,
    }
    if is_invalid:
        return [], meta

    pred_by_name = {f.field_name: f for f in result.fields}
    pred_values_by_name = _collect_pred_values_by_name(result.fields)
    rows: list[GroupResult] = []
    for gf in doc.fields:
        pred = pred_by_name.get(gf.field_name)
        pred_status = pred.field_status if pred else "missing"
        has_value = bool(pred and pred.raw_value)
        # A 组不评证据 (无证据 prompt，candidate_evidences 必为空)
        pred_value = pred.raw_value if pred else ""
        # v4.1 §10.3: 金标状态分类判定
        gold_category = _classify_gold_status(gf.gold_status)
        if gold_category == "should_not_have_value":
            # absent/not_applicable: 系统不应输出值
            correct = (pred_status == "absent") if pred else True
        elif gold_category == "attachment_only":
            # attachment_only: 字段在附件中，正文抽取算无依据但不算值错误
            correct = None  # 无法判定，correct=None
        elif gold_category == "unreadable":
            # unreadable: 无法判定
            correct = None
        else:
            # should_have_value: 走原有值匹配逻辑
            correct = _value_correct(gf.field_name, pred_value, gf) if has_value else False
        # v4.1 sec 7.4: 多值字段集合级 F1
        mv_f1 = _compute_mv_f1(gf.field_name, pred_values_by_name.get(gf.field_name, []), gf)
        rows.append(GroupResult(
            group="A", doc_id=doc.document_id, field_name=gf.field_name,
            gold_status=gf.gold_status, pred_status=pred_status,
            has_value=has_value, has_evidence=False, evidence_verified=False,
            field_validated=False,
            # A 组无证据要求：有值即算无依据 (对比 B/C 组通过证据降低无依据率)
            unjustified=has_value,
            correct=correct,
            multi_value_f1=mv_f1,
        ))
    return rows, meta


# ========== B 组：LLM + 候选证据（不验证）==========

async def run_group_b(doc: GoldDoc, raw_text: str) -> tuple[list[GroupResult], dict]:
    """B 组：LLM 输出字段 + 候选证据，但不做程序验证。

    修复 (P2)：添加 LLM 失败错误检测。
    原实现 multi_lot_02 LLM 调用失败 (tokens=0) 被静默吞掉，6 字段全 missing
    被算入评测，导致 B 组 fields_evaluable=37 (A/C 都是 42) 数据失真。
    现检测 result.error / total_tokens==0 / fields 为空，标记 invalid 跳过评测。
    """
    result = await _ablation.call_extraction_llm(raw_text)
    is_invalid = bool(result.error) or result.total_tokens == 0 or len(result.fields) == 0
    meta = {
        "model_id": result.model_id,
        "prompt_hash": result.prompt_hash,
        "total_tokens": result.total_tokens,
        "latency_ms": result.latency_ms,
        "error": result.error,
        "invalid": is_invalid,
    }
    if is_invalid:
        return [], meta

    pred_by_name = {f.field_name: f for f in result.fields}
    pred_values_by_name = _collect_pred_values_by_name(result.fields)
    locator = _ablation.EvidenceLocator(raw_text)
    rows: list[GroupResult] = []
    for gf in doc.fields:
        pred = pred_by_name.get(gf.field_name)
        pred_status = pred.field_status if pred else "missing"
        has_value = bool(pred and pred.raw_value)
        has_evidence = bool(pred and len(pred.candidate_evidences) > 0)
        # B 组不验证证据，evidence_verified=False
        pred_value = pred.raw_value if pred else ""
        # v4.1 §10.3: 金标状态分类判定
        gold_category = _classify_gold_status(gf.gold_status)
        if gold_category == "should_not_have_value":
            # absent/not_applicable: 系统不应输出值
            correct = (pred_status == "absent") if pred else True
        elif gold_category == "attachment_only":
            # attachment_only: 字段在附件中，正文抽取算无依据但不算值错误
            correct = None  # 无法判定，correct=None
        elif gold_category == "unreadable":
            # unreadable: 无法判定
            correct = None
        else:
            # should_have_value: 走原有值匹配逻辑
            correct = _value_correct(gf.field_name, pred_value, gf) if has_value else False
        # v4.1 sec 7.4: 多值字段集合级 F1
        mv_f1 = _compute_mv_f1(gf.field_name, pred_values_by_name.get(gf.field_name, []), gf)
        rows.append(GroupResult(
            group="B", doc_id=doc.document_id, field_name=gf.field_name,
            gold_status=gf.gold_status, pred_status=pred_status,
            has_value=has_value, has_evidence=has_evidence, evidence_verified=False,
            field_validated=False,
            unjustified=has_value and not has_evidence,
            correct=correct,
            multi_value_f1=mv_f1,
        ))
    return rows, meta


# ========== C 组：LLM + 程序证据验证 + 确定性字段校验 ==========

async def run_group_c(doc: GoldDoc, raw_text: str) -> tuple[list[GroupResult], dict]:
    """C 组：LLM 输出 + EvidenceLocator 验证 + FieldValidator 校验。

    修复 (P0-1)：原实现缺少 LLM 失败检测 (无 invalid 标志)，
    导致 main() 中 `if meta_c.get("invalid")` 分支为 dead path，
    即使 LLM 调用失败 (tokens=0/error) 仍按空字段参与评测。
    现对齐 run_group_a / run_group_b 的 invalid 检测逻辑：
    result.error / total_tokens==0 / fields 为空 时标记 invalid 跳过评测。
    """
    result = await _ablation.call_extraction_llm(raw_text)
    # C 组 LLM 失败检测（与 A/B 组一致）
    is_invalid = bool(result.error) or result.total_tokens == 0 or len(result.fields) == 0
    meta = {
        "model_id": result.model_id,
        "prompt_hash": result.prompt_hash,
        "total_tokens": result.total_tokens,
        "latency_ms": result.latency_ms,
        "error": result.error,
        "invalid": is_invalid,
    }
    if is_invalid:
        return [], meta

    pred_by_name = {f.field_name: f for f in result.fields}
    pred_values_by_name = _collect_pred_values_by_name(result.fields)
    locator = _ablation.EvidenceLocator(raw_text)
    rows: list[GroupResult] = []
    for gf in doc.fields:
        pred = pred_by_name.get(gf.field_name)
        pred_status = pred.field_status if pred else "missing"
        has_value = bool(pred and pred.raw_value)
        has_evidence = bool(pred and len(pred.candidate_evidences) > 0)

        # 证据验证：候选证据能否在原文中定位
        evidence_verified = False
        if has_evidence and pred:
            for ce in pred.candidate_evidences:
                loc = locator.locate(ce.evidence_text, search_from=0)
                if loc.found and loc.location is not None:
                    evidence_verified = True
                    break

        # 字段校验：amount/date/project_identifier
        field_validated = False
        if has_value and pred:
            try:
                vr: ValidationResult = None
                if gf.field_name == "amount":
                    vr = validate_amount(pred.raw_value, None)
                elif gf.field_name == "publish_date" or gf.field_name == "bid_deadline":
                    vr = validate_date(pred.raw_value)
                elif gf.field_name == "project_identifier":
                    vr = validate_project_identifier(pred.raw_value)
                if vr is not None:
                    field_validated = vr.valid
                else:
                    field_validated = True  # 无校验规则的字段默认通过
            except Exception:
                field_validated = False

        pred_value = pred.raw_value if pred else ""
        # v4.1 §10.3: 金标状态分类判定
        gold_category = _classify_gold_status(gf.gold_status)
        if gold_category == "should_not_have_value":
            # absent/not_applicable: 系统不应输出值
            correct = (pred_status == "absent") if pred else True
        elif gold_category == "attachment_only":
            # attachment_only: 字段在附件中，正文抽取算无依据但不算值错误
            correct = None  # 无法判定，correct=None
        elif gold_category == "unreadable":
            # unreadable: 无法判定
            correct = None
        else:
            # should_have_value: 走原有值匹配逻辑
            correct = _value_correct(gf.field_name, pred_value, gf) if has_value else False
        # v4.1 sec 7.4: 多值字段集合级 F1
        mv_f1 = _compute_mv_f1(gf.field_name, pred_values_by_name.get(gf.field_name, []), gf)
        rows.append(GroupResult(
            group="C", doc_id=doc.document_id, field_name=gf.field_name,
            gold_status=gf.gold_status, pred_status=pred_status,
            has_value=has_value, has_evidence=has_evidence,
            evidence_verified=evidence_verified, field_validated=field_validated,
            # C 组：有值但 (无证据 OR 证据未验证 OR 字段未通过校验) 算无依据
            unjustified=has_value and (not evidence_verified or not field_validated),
            correct=correct,
            multi_value_f1=mv_f1,
        ))
    return rows, meta


# ========== D 组：完整 BidAgent（C 组验证 + display_grade 选择性输出）==========

async def run_group_d(rows_c: list[GroupResult], meta_c: dict) -> tuple[list[GroupResult], dict]:
    """D 组：完整 BidAgent = C 组验证 + display_grade 选择性输出。

    直接复用 C 组的 LLM 调用与证据验证结果 (不重新调用 LLM)，
    在 C 组结果基础上计算 display_grade (v4.1 第八章)，
    并按选择性输出策略 (v4.1 第十章 10.7) 拒绝 grade="low" 的字段。

    Baseline 公平性 (v4.1 10.11): D 组与 C 组使用相同 LLM 调用结果，
    仅在 C 组验证后增加 display_grade 计算和选择性输出，
    确保 "相同重试次数" 和 "相同 LLM 调用" 的公平性约束。

    与 C 组的差异:
    - 计算 display_grade: support_level 基于 evidence_verified + field_validated
      (direct=STRONG / inferred=MEDIUM / unsupported=WEAK)
    - 选择性输出: grade="low" 的字段被拒绝 (不输出)
    - unjustified = 有值但被拒绝 (has_value and grade=="low")
    - correct 只统计输出字段 (grade != "low")，被拒绝字段 correct=None

    单源评测默认 source_role="official_original"，cross_verified=False (W3 无多源)。

    meta 中额外记录 display_grade 分布 (high/review/low 计数)。
    D 组 tokens/latency 复用 C 组 (不重新调用 LLM)。
    """
    if meta_c.get("invalid"):
        return [], meta_c

    rows_d: list[GroupResult] = []
    grade_dist = {"high": 0, "review": 0, "low": 0}
    for r in rows_c:
        # 基于 C 组结果计算 display_grade
        if r.evidence_verified and r.field_validated:
            support_level = "direct"  # STRONG
        elif r.has_value:
            support_level = "inferred"  # MEDIUM
        else:
            support_level = "unsupported"  # WEAK
        source_role = "official_original"  # 单源评测默认
        cross_verified = False  # W3 无多源交叉验证
        field_status = r.pred_status  # 已是 pred_status if pred else "missing"
        grade = compute_display_grade(support_level, source_role, cross_verified, field_status)
        grade_dist[grade] += 1

        # D 组选择性输出: grade="low" 被拒绝 (不输出)
        output = grade != "low"
        # unjustified: 有值但被拒绝
        unjustified = r.has_value and not output
        # correct: 只统计输出字段 (被拒绝字段不计入 evaluable)
        correct = r.correct if output else None

        rows_d.append(GroupResult(
            group="D", doc_id=r.doc_id, field_name=r.field_name,
            gold_status=r.gold_status, pred_status=r.pred_status,
            has_value=r.has_value, has_evidence=r.has_evidence,
            evidence_verified=r.evidence_verified, field_validated=r.field_validated,
            unjustified=unjustified,
            correct=correct,
            multi_value_f1=r.multi_value_f1,  # v4.1 sec 7.4: 透传 C 组多值 F1
        ))

    # 独立记录 meta (添加 display_grade 分布)
    meta_d = dict(meta_c)
    meta_d["display_grade_dist"] = grade_dist
    return rows_d, meta_d
