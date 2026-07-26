"""W2-09 证据定位指标自动计算。

对应总规划 v4.1 第十章 10.5 证据质量指标 + 第二周任务清单 W2-09。

三个核心指标:
1. 证据检出率 (recall): 系统找到证据的字段数 / 金标中 present 状态字段总数
2. 证据精确率 (precision): 正确证据数 / 系统输出证据总数
3. 证据边界字符区间 IoU: 系统证据区间与金标 acceptable_evidence_spans 的交并比

判定规则 (Sol 要求):
- 人工标注允许多个合法证据区间 (acceptable_evidence_spans[])
- 系统证据只要与任一合法区间满足要求即判正确
- IoU 阈值: >=0.5 视为命中 (稍长上下文仍视为有效证据)
- IoU 基于清洗后原始文本坐标 (project_memory 约束)

用法:
    python scripts/eval_evidence.py [--docs 7] [--skip-llm]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORK_DIR = Path(r"C:\Users\Lenovo\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\work-mode-projects\6a57291a0778ce48bfe693d2")
RAW_DIR = WORK_DIR / "_w2_raw"
ANNOT_DIR = WORK_DIR / "_w2_annotations"

from app.llm.extractor import call_extraction_llm, compute_prompt_hash
from app.processors.evidence_locator import EvidenceLocator


DEFAULT_DOCS = [
    "tender_06", "tender_07",
    "award_05", "award_06",
    "correction_04", "correction_05",
    "multi_lot_02",
]

IOU_THRESHOLD = 0.5  # Sol: 稍长上下文仍视为有效证据


@dataclass
class GoldEvidenceSpan:
    start: int
    end: int
    text: str
    role: str


@dataclass
class GoldField:
    field_name: str
    gold_status: str
    evidences: list  # [GoldEvidenceSpan]


@dataclass
class GoldDoc:
    document_id: str
    file: str
    fields: list  # [GoldField]


@dataclass
class FieldMetric:
    doc_id: str
    field_name: str
    gold_status: str
    gold_evidence_count: int
    pred_evidence_count: int
    matched_evidence_count: int  # 与金标匹配的预测证据数
    best_iou: float  # 最佳 IoU (0.0 if no match)
    found: bool  # 系统是否找到至少 1 个匹配证据
    iou_passed: bool  # best_iou >= IOU_THRESHOLD


@dataclass
class DocMetric:
    doc_id: str
    fields_total: int
    fields_present: int  # gold_status == present/multi_value
    fields_found: int  # 系统找到证据的字段数
    evidences_pred: int
    evidences_matched: int
    iou_list: list  # 所有匹配证据的 IoU
    recall: float  # fields_found / fields_present
    precision: float  # evidences_matched / evidences_pred
    iou_avg: float  # mean(iou_list)


@dataclass
class OverallMetric:
    docs_count: int
    fields_total: int
    fields_present: int
    fields_found: int
    evidences_pred: int
    evidences_matched: int
    recall: float  # 证据检出率
    precision: float  # 证据精确率
    iou_avg: float  # 平均 IoU
    iou_p50: float
    iou_p95: float
    model_id: str
    prompt_hash: str
    total_tokens: int


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


async def evaluate_doc(gd: GoldDoc, raw_text: str) -> tuple[DocMetric, dict]:
    """评测单篇。"""
    # 调 LLM 抽取
    result = await call_extraction_llm(raw_text)
    meta = {
        "model_id": result.model_id,
        "prompt_hash": result.prompt_hash,
        "total_tokens": result.total_tokens,
        "latency_ms": result.latency_ms,
        "error": result.error,
    }

    locator = EvidenceLocator(raw_text)
    pred_by_name = {f.field_name: f for f in result.fields}

    field_metrics: list[FieldMetric] = []
    evidences_pred = 0
    evidences_matched = 0
    iou_list = []

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
                    matched, iou = match_evidence(
                        loc.location.start, loc.location.end,
                        gf.evidences,
                    )
                    if iou > best_iou:
                        best_iou = iou
                    if matched:
                        matched_count += 1
                        iou_list.append(iou)
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

    return DocMetric(
        doc_id=gd.document_id,
        fields_total=len(field_metrics),
        fields_present=fields_present,
        fields_found=fields_found,
        evidences_pred=evidences_pred,
        evidences_matched=evidences_matched,
        iou_list=[round(x, 4) for x in iou_list],
        recall=round(fields_found / max(fields_present, 1), 4),
        precision=round(evidences_matched / max(evidences_pred, 1), 4),
        iou_avg=round(sum(iou_list) / max(len(iou_list), 1), 4) if iou_list else 0.0,
    ), meta


def percentile(sorted_list: list, p: float) -> float:
    if not sorted_list:
        return 0.0
    idx = int(len(sorted_list) * p)
    if idx >= len(sorted_list):
        idx = len(sorted_list) - 1
    return sorted_list[idx]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--docs", nargs="*", default=DEFAULT_DOCS)
    parser.add_argument("--skip-llm", action="store_true")
    parser.add_argument("--out", default=str(WORK_DIR / "_w2_d4_evidence_metric_result.json"))
    args = parser.parse_args()

    print("=" * 70)
    print("W2-09 证据定位指标自动计算")
    print("=" * 70)
    print(f"公告数: {len(args.docs)}")
    print(f"模型: deepseek-v4-flash")
    print(f"prompt_hash: {compute_prompt_hash()}")
    print(f"IoU 阈值: {IOU_THRESHOLD}")
    print()

    docs: list[tuple[GoldDoc, str]] = []
    for prefix in args.docs:
        gd = load_gold_doc(prefix)
        rt = load_raw_text(prefix)
        if gd is None or rt is None:
            print(f"[WARN] 跳过 {prefix}")
            continue
        docs.append((gd, rt))
    print(f"实际加载: {len(docs)} 篇\n")

    if args.skip_llm:
        print("[SKIP-LLM]")
        return

    doc_metrics: list[DocMetric] = []
    metas: list[dict] = []
    for gd, rt in docs:
        print(f"--- {gd.document_id} ({len(rt)} 字符) ---")
        dm, meta = await evaluate_doc(gd, rt)
        print(f"  字段 present: {dm.fields_present}, found: {dm.fields_found}, "
              f"recall={dm.recall:.2%}, precision={dm.precision:.2%}, "
              f"iou_avg={dm.iou_avg:.4f}, tokens={meta['total_tokens']}, "
              f"latency={meta['latency_ms']}ms")
        doc_metrics.append(dm); metas.append(meta)

    # 汇总
    total_fields = sum(dm.fields_total for dm in doc_metrics)
    total_present = sum(dm.fields_present for dm in doc_metrics)
    total_found = sum(dm.fields_found for dm in doc_metrics)
    total_pred = sum(dm.evidences_pred for dm in doc_metrics)
    total_matched = sum(dm.evidences_matched for dm in doc_metrics)
    all_ious = sorted([x for dm in doc_metrics for x in dm.iou_list])

    overall = OverallMetric(
        docs_count=len(doc_metrics),
        fields_total=total_fields,
        fields_present=total_present,
        fields_found=total_found,
        evidences_pred=total_pred,
        evidences_matched=total_matched,
        recall=round(total_found / max(total_present, 1), 4),
        precision=round(total_matched / max(total_pred, 1), 4),
        iou_avg=round(sum(all_ious) / max(len(all_ious), 1), 4) if all_ious else 0.0,
        iou_p50=round(percentile(all_ious, 0.5), 4),
        iou_p95=round(percentile(all_ious, 0.95), 4),
        model_id=metas[0].get("model_id", "unknown") if metas else "unknown",
        prompt_hash=metas[0].get("prompt_hash", "") if metas else "",
        total_tokens=sum(m.get("total_tokens", 0) for m in metas),
    )

    print("\n" + "=" * 70)
    print("W2-09 汇总")
    print("=" * 70)
    print(f"公告数:         {overall.docs_count}")
    print(f"字段总数:       {overall.fields_total}")
    print(f"present 字段:   {overall.fields_present}")
    print(f"找到证据字段:   {overall.fields_found}")
    print(f"系统输出证据:   {overall.evidences_pred}")
    print(f"匹配证据数:     {overall.evidences_matched}")
    print(f"---")
    print(f"证据检出率:     {overall.recall:.2%}  (found/present)")
    print(f"证据精确率:     {overall.precision:.2%}  (matched/pred)")
    print(f"IoU 平均:       {overall.iou_avg:.4f}")
    print(f"IoU P50:        {overall.iou_p50:.4f}")
    print(f"IoU P95:        {overall.iou_p95:.4f}")
    print(f"---")
    print(f"模型:           {overall.model_id}")
    print(f"prompt_hash:    {overall.prompt_hash}")
    print(f"总 tokens:      {overall.total_tokens}")

    # 每篇明细
    print(f"\n--- 每篇明细 ---")
    print(f"{'doc_id':<40} {'present':>8} {'found':>6} {'recall':>8} {'precision':>10} {'iou_avg':>8}")
    for dm in doc_metrics:
        print(f"{dm.doc_id:<40} {dm.fields_present:>8} {dm.fields_found:>6} "
              f"{dm.recall:>8.2%} {dm.precision:>10.2%} {dm.iou_avg:>8.4f}")

    # 保存
    out = {
        "overall": asdict(overall),
        "docs": [asdict(dm) for dm in doc_metrics],
    }
    out_path = Path(args.out)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
