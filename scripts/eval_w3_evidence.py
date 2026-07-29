"""W3-03 证据定位指标评测（90篇扩充金标）。

对应 v4.1 第十章 10.5 证据质量指标 + W3-03 任务。

关键修复（vs eval_evidence.py）:
1. 数据源切换：_w2_raw/_w2_annotations → _w3_raw/tests/fixtures/gold/k3_annotations_batch2.json
2. 基准对齐：K3金标spans以"## "marker为基准(相对坐标)，EvidenceLocator返回绝对坐标
   match_evidence() 前需将locator坐标减去marker偏移量
3. 批量评测：支持90篇全量评测，输出OverallMetric + 逐篇明细

三个核心指标:
1. 证据检出率 (recall): 系统找到证据的字段数 / 金标中 present 状态字段总数
2. 证据精确率 (precision): 正确证据数 / 系统输出证据总数
3. 证据边界字符区间 IoU: 系统证据区间与金标 acceptable_evidence_spans 的交并比

用法:
    # 冒烟测试(1篇)
    python scripts/eval_w3_evidence.py --docs w3_tender_001

    # 批量评测(全部90篇)
    python scripts/eval_w3_evidence.py --all

    # 指定篇数
    python scripts/eval_w3_evidence.py --limit 10
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RAW_DIR = ROOT / "_w3_raw"
GOLD_PATH = ROOT / "tests" / "fixtures" / "gold" / "k3_annotations_batch2.json"
OUTPUT_DIR = ROOT / "_w3_outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

from app.llm.extractor import call_extraction_llm, compute_prompt_hash
from app.processors.evidence_locator import EvidenceLocator

IOU_THRESHOLD = 0.5  # Sol: 稍长上下文仍视为有效证据


@dataclass
class GoldEvidenceSpan:
    start: int  # 相对## marker的偏移
    end: int
    text: str


@dataclass
class GoldField:
    field_name: str
    gold_status: str
    evidences: list  # [GoldEvidenceSpan]


@dataclass
class GoldDoc:
    document_id: str
    file: str
    notice_type: str
    fields: list  # [GoldField]


@dataclass
class FieldMetric:
    doc_id: str
    field_name: str
    gold_status: str
    gold_evidence_count: int
    pred_evidence_count: int
    matched_evidence_count: int
    best_iou: float
    found: bool
    iou_passed: bool


@dataclass
class DocMetric:
    doc_id: str
    notice_type: str
    fields_total: int
    fields_present: int
    fields_found: int
    evidences_pred: int
    evidences_located: int
    evidences_matched: int
    iou_list: list
    iou_list_matched: list
    recall: float
    precision: float
    iou_avg: float
    iou_avg_matched: float


@dataclass
class OverallMetric:
    docs_count: int
    fields_total: int
    fields_present: int
    fields_found: int
    evidences_pred: int
    evidences_located: int
    evidences_matched: int
    recall: float
    precision: float
    iou_avg: float
    iou_avg_matched: float
    iou_p50: float
    iou_p95: float
    model_id: str
    prompt_hash: str
    total_tokens: int
    invalid_docs: list
    # 按公告类型细分
    by_type: dict


def load_gold_all() -> list[GoldDoc]:
    """加载K3标注的全部90篇金标。"""
    with open(GOLD_PATH, encoding="utf-8") as f:
        data = json.load(f)
    docs = []
    for item in data:
        if not isinstance(item, dict) or item.get("_is_meta"):
            continue
        fields = []
        for f in item.get("fields", []):
            evidences = []
            for v in f.get("values", []):
                for span in v.get("acceptable_evidence_spans", []):
                    evidences.append(GoldEvidenceSpan(
                        start=span["start"], end=span["end"],
                        text=span["text"],
                    ))
            fields.append(GoldField(
                field_name=f["field_name"],
                gold_status=f["gold_status"],
                evidences=evidences,
            ))
        docs.append(GoldDoc(
            document_id=item["document_id"],
            file=item.get("file", ""),
            notice_type=item.get("notice_type", "unknown"),
            fields=fields,
        ))
    return docs


def load_raw_text(doc_id: str) -> Optional[str]:
    p = RAW_DIR / f"{doc_id}.txt"
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


def get_body_offset(raw_text: str) -> int:
    """获取"## "标记位置（金标spans基准）。"""
    return raw_text.find("## ")


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


async def main():
    parser = argparse.ArgumentParser(description="W3-03 证据定位指标评测")
    parser.add_argument("--docs", nargs="*", help="指定评测的doc_id列表")
    parser.add_argument("--all", action="store_true", help="评测全部90篇")
    parser.add_argument("--limit", type=int, help="评测前N篇")
    parser.add_argument("--delay", type=float, default=1.0, help="LLM调用间隔秒数")
    parser.add_argument("--output", default=str(OUTPUT_DIR / "w3_03_evidence_report.json"))
    args = parser.parse_args()

    print("=" * 60, flush=True)
    print("W3-03 证据定位指标评测", flush=True)
    print("=" * 60, flush=True)

    all_gold = load_gold_all()
    print(f"加载金标: {len(all_gold)} 篇", flush=True)

    if args.all:
        target_docs = all_gold
    elif args.limit:
        target_docs = all_gold[:args.limit]
    elif args.docs:
        wanted = set(args.docs)
        target_docs = [g for g in all_gold if g.document_id in wanted]
    else:
        # 默认冒烟测试1篇
        target_docs = all_gold[:1]
        print("未指定--all/--limit/--docs, 默认冒烟测试1篇", flush=True)

    print(f"目标评测: {len(target_docs)} 篇", flush=True)
    print(f"输出文件: {args.output}", flush=True)
    print(flush=True)

    doc_metrics: list[DocMetric] = []
    metas: list[dict] = []
    invalid_count = 0

    for i, gd in enumerate(target_docs, 1):
        raw_text = load_raw_text(gd.document_id)
        if not raw_text:
            print(f"[{i}/{len(target_docs)}] SKIP {gd.document_id}: 原文不存在", flush=True)
            continue

        print(f"[{i}/{len(target_docs)}] {gd.document_id} ({gd.notice_type}) ", end="", flush=True)
        t0 = time.perf_counter()
        try:
            dm, meta = await evaluate_doc(gd, raw_text)
        except Exception as e:
            print(f"ERROR: {type(e).__name__}: {e}", flush=True)
            invalid_count += 1
            metas.append({"invalid": True, "error": str(e), "total_tokens": 0})
            continue
        elapsed = time.perf_counter() - t0

        if dm is None:
            print(f"INVALID (tokens={meta.get('total_tokens')}, err={meta.get('error', '')[:50]})", flush=True)
            invalid_count += 1
            metas.append(meta)
            continue

        doc_metrics.append(dm)
        metas.append(meta)
        print(
            f"recall={dm.recall} precision={dm.precision} "
            f"iou_avg={dm.iou_avg} tokens={meta.get('total_tokens')} "
            f"latency={elapsed:.1f}s",
            flush=True,
        )

        if args.delay > 0:
            await asyncio.sleep(args.delay)

    print(flush=True)
    print("=" * 60, flush=True)
    if not doc_metrics:
        print("无有效评测结果", flush=True)
        return

    overall = aggregate(doc_metrics, metas)
    print(f"评测完成: {overall.docs_count} 篇有效 / {invalid_count} 篇失败", flush=True)
    print(f"模型: {overall.model_id}", flush=True)
    print(f"总tokens: {overall.total_tokens}", flush=True)
    print(flush=True)
    print("=== 总体指标 ===", flush=True)
    print(f"字段总数: {overall.fields_total}", flush=True)
    print(f"present字段数: {overall.fields_present}", flush=True)
    print(f"找到证据字段数: {overall.fields_found}", flush=True)
    print(f"证据检出率 recall: {overall.recall}", flush=True)
    print(f"证据精确率 precision: {overall.precision}", flush=True)
    print(f"平均IoU(全): {overall.iou_avg}", flush=True)
    print(f"平均IoU(匹配): {overall.iou_avg_matched}", flush=True)
    print(f"IoU P50: {overall.iou_p50}", flush=True)
    print(f"IoU P95: {overall.iou_p95}", flush=True)
    print(flush=True)
    print("=== 按公告类型 ===", flush=True)
    for t, b in sorted(overall.by_type.items()):
        print(f"  {t}: docs={b['docs']} recall={b['recall']} precision={b['precision']}", flush=True)

    # 写报告
    report = {
        "task": "W3-03",
        "version": "v1",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "overall": asdict(overall),
        "doc_metrics": [asdict(d) for d in doc_metrics],
    }
    Path(args.output).parent.mkdir(exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(flush=True)
    print(f"报告已写入: {args.output}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
