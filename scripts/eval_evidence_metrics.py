"""W2-09 证据定位指标计算（数据加载 + IoU 匹配 + 评测 + 聚合）。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

# 通过模块属性引用以支持测试 patch (patch("scripts.eval_evidence.call_extraction_llm"))
# 兼容 __main__ (直接运行) 和 scripts.eval_evidence (被 import) 两种执行模式
_evidence = sys.modules.get('scripts.eval_evidence') or sys.modules.get('__main__')

from app.processors.evidence_locator import EvidenceLocator

from scripts.eval_evidence_types import (
    DEFAULT_DOCS,
    DocMetric,
    FieldMetric,
    GoldDoc,
    GoldEvidenceSpan,
    GoldField,
    IOU_THRESHOLD,
)

ROOT = Path(__file__).resolve().parent.parent
WORK_DIR = Path(os.environ.get("GOLD_WORK_DIR", str(ROOT)))  # 金标数据目录（本地评测用，不入包）
RAW_DIR = WORK_DIR / "_w2_raw"
ANNOT_DIR = WORK_DIR / "_w2_annotations"


def load_gold_doc(doc_prefix: str) -> Optional[GoldDoc]:
    matches = list(ANNOT_DIR.glob(f"annotation_{doc_prefix}*.json"))
    if not matches:
        return None
    with open(matches[0], encoding="utf-8") as f:
        data = json.load(f)
    fields = []
    for f in data["fields"]:
        evidences = []
        for v in f.get("values", []):
            for span in v.get("acceptable_evidence_spans", []):
                evidences.append(GoldEvidenceSpan(
                    start=span["start"], end=span["end"],
                    text=span["text"], role=span.get("role", "primary"),
                ))
        fields.append(GoldField(
            field_name=f["field_name"],
            gold_status=f["gold_status"],
            evidences=evidences,
        ))
    return GoldDoc(document_id=data["document_id"], file=matches[0].name, fields=fields)


def load_raw_text(doc_prefix: str) -> Optional[str]:
    p = RAW_DIR / f"{doc_prefix}.txt"
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


def compute_iou(a_start: int, a_end: int, b_start: int, b_end: int) -> float:
    """计算两个区间的 IoU。"""
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
) -> tuple[bool, float]:
    """系统证据与金标 spans 匹配，返回 (是否匹配, 最佳 IoU)。"""
    best_iou = 0.0
    matched = False
    for g in gold_spans:
        iou = compute_iou(pred_start, pred_end, g.start, g.end)
        if iou > best_iou:
            best_iou = iou
        if iou >= IOU_THRESHOLD:
            matched = True
    return matched, best_iou


async def evaluate_doc(gd: GoldDoc, raw_text: str) -> tuple[Optional[DocMetric], dict]:
    """评测单篇。

    P0-2 修复: 检测 LLM 失败 (result.error / total_tokens==0 / fields 为空)，
    标记 meta["invalid"]=True 并返回 None，main() 据此跳过该 doc 不计入指标。
    对齐 eval_ablation.py 的 P0-1 修复做法。
    """
    # 调 LLM 抽取
    result = await _evidence.call_extraction_llm(raw_text)
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

    field_metrics: list[FieldMetric] = []
    evidences_pred = 0
    evidences_located = 0  # P3: 被 locator 定位到原文的证据数
    evidences_matched = 0
    iou_list = []  # P3: 所有被定位证据的 IoU (含 IoU<0.5)
    iou_list_matched = []  # P3: 仅 matched=True 的 IoU

    for gf in gd.fields:
        gold_ev_count = len(gf.evidences)
        is_present = gf.gold_status in ("present", "multi_value")

        pred = pred_by_name.get(gf.field_name)
        pred_ev_count = len(pred.candidate_evidences) if pred else 0
        evidences_pred += pred_ev_count

        matched_count = 0
        best_iou = 0.0
        if pred and pred_ev_count > 0 and gold_ev_count > 0:
            for ce in pred.candidate_evidences:
                loc = locator.locate(ce.evidence_text, search_from=0)
                if loc.found and loc.location is not None:
                    evidences_located += 1  # P3: 定位成功
                    matched, iou = match_evidence(
                        loc.location.start, loc.location.end,
                        gf.evidences,
                    )
                    if iou > best_iou:
                        best_iou = iou
                    # P3: 所有被定位的证据都计入 iou_list (含 IoU<0.5)
                    iou_list.append(iou)
                    if matched:
                        matched_count += 1
                        iou_list_matched.append(iou)
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

    fields_present = sum(1 for fm in field_metrics if fm.gold_status in ("present", "multi_value"))
    fields_found = sum(1 for fm in field_metrics if fm.found)

    # P1-18: iou_avg 用 iou_list_matched 求和 (未匹配算0) / evidences_pred 做分母 (未定位算0)
    # iou_avg_matched 用 iou_list_matched 做分母 (原口径，仅匹配证据)
    iou_avg_overall = round(sum(iou_list_matched) / max(evidences_pred, 1), 4) if evidences_pred else 0.0
    iou_avg_matched = round(sum(iou_list_matched) / max(len(iou_list_matched), 1), 4) if iou_list_matched else 0.0

    return DocMetric(
        doc_id=gd.document_id,
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
