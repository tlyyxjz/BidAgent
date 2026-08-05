"""W3-03 证据定位指标计算（IoU 匹配 + 评测 + 聚合）。"""
from __future__ import annotations

from typing import Optional

from app.llm.extractor import call_extraction_llm
from app.processors.evidence_locator import EvidenceLocator

from scripts.eval_w3_evidence_types import (
    DocMetric,
    FieldMetric,
    GoldDoc,
    GoldEvidenceSpan,
    IOU_THRESHOLD,
    OverallMetric,
)
from scripts.eval_w3_evidence_data import get_body_offset


def compute_iou(a_start: int, a_end: int, b_start: int, b_end: int) -> float:
    inter_start = max(a_start, b_start)
    inter_end = min(a_end, b_end)
    inter = max(0, inter_end - inter_start)
    if inter == 0:
        return 0.0
    union = max(a_end, b_end) - min(a_start, b_start)
    if union <= 0:
        return 0.0
    return inter / union


def match_evidence(
    pred_start: int, pred_end: int,
    gold_spans: list[GoldEvidenceSpan],
    body_offset: int,
) -> tuple[bool, float]:
    """系统证据(绝对坐标)与金标spans(相对## marker)匹配。

    关键：将pred坐标减去body_offset转为相对坐标，再与gold比较。
    """
    rel_pred_start = pred_start - body_offset
    rel_pred_end = pred_end - body_offset
    best_iou = 0.0
    matched = False
    for g in gold_spans:
        iou = compute_iou(rel_pred_start, rel_pred_end, g.start, g.end)
        if iou > best_iou:
            best_iou = iou
        if iou >= IOU_THRESHOLD:
            matched = True
    return matched, best_iou


async def evaluate_doc(gd: GoldDoc, raw_text: str) -> tuple[Optional[DocMetric], dict]:
    """评测单篇。"""
    body_offset = get_body_offset(raw_text)
    if body_offset < 0:
        body_offset = 0

    result = await call_extraction_llm(raw_text)
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
        return None, meta

    locator = EvidenceLocator(raw_text)
    pred_by_name = {f.field_name: f for f in result.fields}

    # 提取 project_id 用于 Bootstrap CI 分组 (v4.1 10.10)
    pred_pid = pred_by_name.get("project_identifier")
    project_id = pred_pid.raw_value if pred_pid and pred_pid.raw_value else "__no_project_id__"
    # 归一化:去除空格和常见前缀符号
    project_id = project_id.strip() if project_id else "__no_project_id__"

    field_metrics: list[FieldMetric] = []
    evidences_pred = 0
    evidences_located = 0
    evidences_matched = 0
    iou_list = []
    iou_list_matched = []

    for gf in gd.fields:
        gold_ev_count = len(gf.evidences)
        is_present = gf.gold_status == "present"

        pred = pred_by_name.get(gf.field_name)
        pred_ev_count = len(pred.candidate_evidences) if pred else 0
        evidences_pred += pred_ev_count

        matched_count = 0
        best_iou = 0.0
        if pred and pred_ev_count > 0 and gold_ev_count > 0:
            for ce in pred.candidate_evidences:
                # W3-03 优化: 用 locate_all_occurrences 取所有出现位置,
                # 与金标任一 span 匹配即算正确(避免 locator 取首次但金标是另一次)
                all_locs = locator.locate_all_occurrences(ce.evidence_text, max_count=20)
                if all_locs:
                    evidences_located += 1
                    best_iou_for_ce = 0.0
                    matched_for_ce = False
                    for loc in all_locs:
                        m, iou = match_evidence(
                            loc.start, loc.end,
                            gf.evidences,
                            body_offset,
                        )
                        if iou > best_iou_for_ce:
                            best_iou_for_ce = iou
                        if m:
                            matched_for_ce = True
                    if best_iou_for_ce > best_iou:
                        best_iou = best_iou_for_ce
                    iou_list.append(best_iou_for_ce)
                    if matched_for_ce:
                        matched_count += 1
                        iou_list_matched.append(best_iou_for_ce)
            evidences_matched += matched_count

        found = matched_count > 0
        field_metrics.append(FieldMetric(
            doc_id=gd.document_id,
            field_name=gf.field_name,
            gold_status=gf.gold_status,
            gold_evidence_count=gold_ev_count,
            pred_evidence_count=pred_ev_count,
            matched_evidence_count=matched_count,
            best_iou=round(best_iou, 4),
            found=found,
            iou_passed=best_iou >= IOU_THRESHOLD,
        ))

    fields_present = sum(1 for fm in field_metrics if fm.gold_status == "present")
    fields_found = sum(1 for fm in field_metrics if fm.found)

    iou_avg_overall = round(sum(iou_list_matched) / max(evidences_pred, 1), 4) if evidences_pred else 0.0
    iou_avg_matched = round(sum(iou_list_matched) / max(len(iou_list_matched), 1), 4) if iou_list_matched else 0.0

    return DocMetric(
        doc_id=gd.document_id,
        notice_type=gd.notice_type,
        project_id=project_id,
        fields_total=len(field_metrics),
        fields_present=fields_present,
        fields_found=fields_found,
        evidences_pred=evidences_pred,
        evidences_located=evidences_located,
        evidences_matched=evidences_matched,
        iou_list=[round(x, 4) for x in iou_list],
        iou_list_matched=[round(x, 4) for x in iou_list_matched],
        recall=round(fields_found / max(fields_present, 1), 4),
        precision=round(evidences_matched / max(evidences_pred, 1), 4),
        iou_avg=iou_avg_overall,
        iou_avg_matched=iou_avg_matched,
    ), meta


def percentile(sorted_list: list, p: float) -> float:
    if not sorted_list:
        return 0.0
    idx = int(len(sorted_list) * p)
    if idx >= len(sorted_list):
        idx = len(sorted_list) - 1
    return sorted_list[idx]


def aggregate(doc_metrics: list[DocMetric], metas: list[dict]) -> OverallMetric:
    fields_total = sum(d.fields_total for d in doc_metrics)
    fields_present = sum(d.fields_present for d in doc_metrics)
    fields_found = sum(d.fields_found for d in doc_metrics)
    evidences_pred = sum(d.evidences_pred for d in doc_metrics)
    evidences_located = sum(d.evidences_located for d in doc_metrics)
    evidences_matched = sum(d.evidences_matched for d in doc_metrics)
    all_ious_matched = [x for d in doc_metrics for x in d.iou_list_matched]

    # 按公告类型细分
    by_type = {}
    for d in doc_metrics:
        t = d.notice_type
        if t not in by_type:
            by_type[t] = {
                "docs": 0, "fields_present": 0, "fields_found": 0,
                "evidences_pred": 0, "evidences_matched": 0,
                "recall": 0.0, "precision": 0.0,
            }
        b = by_type[t]
        b["docs"] += 1
        b["fields_present"] += d.fields_present
        b["fields_found"] += d.fields_found
        b["evidences_pred"] += d.evidences_pred
        b["evidences_matched"] += d.evidences_matched
    for t, b in by_type.items():
        b["recall"] = round(b["fields_found"] / max(b["fields_present"], 1), 4)
        b["precision"] = round(b["evidences_matched"] / max(b["evidences_pred"], 1), 4)

    invalid_docs = [m.get("error", "") for m in metas if m.get("invalid")]
    first_meta = metas[0] if metas else {}

    return OverallMetric(
        docs_count=len(doc_metrics),
        fields_total=fields_total,
        fields_present=fields_present,
        fields_found=fields_found,
        evidences_pred=evidences_pred,
        evidences_located=evidences_located,
        evidences_matched=evidences_matched,
        recall=round(fields_found / max(fields_present, 1), 4),
        precision=round(evidences_matched / max(evidences_pred, 1), 4),
        iou_avg=round(sum(all_ious_matched) / max(evidences_pred, 1), 4) if evidences_pred else 0.0,
        iou_avg_matched=round(sum(all_ious_matched) / max(len(all_ious_matched), 1), 4) if all_ious_matched else 0.0,
        iou_p50=round(percentile(sorted(all_ious_matched), 0.5), 4),
        iou_p95=round(percentile(sorted(all_ious_matched), 0.95), 4),
        model_id=first_meta.get("model_id", ""),
        prompt_hash=first_meta.get("prompt_hash", ""),
        total_tokens=sum(m.get("total_tokens", 0) for m in metas),
        invalid_docs=invalid_docs,
        by_type=by_type,
    )
