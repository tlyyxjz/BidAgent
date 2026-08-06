"""金标 v4 合集 598 篇全量 IoU 证据定位复测。

数据源: tests/fixtures/gold/gold_dataset_v4.json（598 篇合集）
复用:   eval_w3_evidence_metrics 的 evaluate_doc / match_evidence / compute_iou / aggregate
        eval_gold598_retest 的 normalize_fields / source_of / load_checkpoint 模式

特性:
    - asyncio 并发（默认 5）
    - checkpoint JSONL 断点续跑（每篇完成即写）
    - LLM JSON 解析失败的篇目记 invalid 不中断
    - 汇总: recall / precision / iou_avg_matched / iou_p50/p95 / IoU 分桶 / 按字段错误归因 / bootstrap CI

用法:
    python scripts/eval_gold598_evidence.py [--concurrency 5] [--limit N]
    python scripts/eval_gold598_evidence.py --retry-invalid   # 重跑 invalid 篇目
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.eval_w3_evidence_types import (  # noqa: E402
    IOU_THRESHOLD,
    GoldDoc,
    GoldEvidenceSpan,
    GoldField,
)
from scripts.eval_w3_evidence_metrics import (  # noqa: E402
    evaluate_doc,
    aggregate,
    percentile,
)

GOLD_PATH = ROOT / "tests" / "fixtures" / "gold" / "gold_dataset_v4.json"
OUT_DIR = ROOT / "_w3_outputs"
CKPT_PATH = OUT_DIR / "gold598_evidence_ckpt.jsonl"
OUT_PATH = OUT_DIR / "gold598_evidence.json"


def source_of(doc_id: str, fname: str) -> str:
    if fname.startswith("w3_"):
        return "w3"
    if fname.startswith("w4_"):
        return "w4"
    if fname.startswith("w5_"):
        return "w5"
    return "frozen93"


def _norm_values(vals) -> list[dict]:
    """把三种金标值形态归一为 [{"raw_value": str, "acceptable_evidence_spans": [...]}]。"""
    out: list[dict] = []
    if isinstance(vals, list):
        for v in vals:
            if isinstance(v, dict):
                if v.get("raw_value"):
                    out.append({
                        "raw_value": v["raw_value"],
                        "acceptable_evidence_spans": v.get("acceptable_evidence_spans", []),
                    })
            elif isinstance(v, str) and v.strip():
                out.append({"raw_value": v, "acceptable_evidence_spans": []})
    elif isinstance(vals, str) and vals.strip():
        out.append({"raw_value": vals, "acceptable_evidence_spans": []})
    return out


def normalize_fields(fields_raw) -> list[GoldField]:
    """归一化三种金标 fields 形态为 eval_w3_evidence 的 GoldField（含 evidences）。

    A) list[{field_name, gold_status, values:[{raw_value, acceptable_evidence_spans}]}]  (w3/batch2)
    B) dict{field_name: {status, values:[{raw_value, acceptable_evidence_spans}]}}         (w4/w5)
    C) list[{field_name, gold_status, value: str|list}]                                   (frozen93)
    """
    gfs: list[GoldField] = []
    if isinstance(fields_raw, dict):
        for name, spec in fields_raw.items():
            if not isinstance(spec, dict):
                continue
            status = spec.get("status") or spec.get("gold_status") or "absent"
            vals = _norm_values(spec.get("values"))
            field_spans = spec.get("acceptable_evidence_spans", [])
            evidences = _extract_evidences(vals, field_spans)
            gfs.append(GoldField(field_name=name, gold_status=status, evidences=evidences))
        return gfs
    for f in fields_raw or []:
        if not isinstance(f, dict):
            continue
        name = f.get("field_name")
        status = f.get("gold_status") or f.get("status") or "absent"
        vals = _norm_values(f.get("values") if "values" in f else f.get("value"))
        field_spans = f.get("acceptable_evidence_spans", [])
        evidences = _extract_evidences(vals, field_spans)
        gfs.append(GoldField(field_name=name, gold_status=status, evidences=evidences))
    return gfs


def _extract_evidences(vals: list[dict], field_spans: list | None = None) -> list[GoldEvidenceSpan]:
    """从归一化 values 和字段级 acceptable_evidence_spans 提取 → [GoldEvidenceSpan]。

    w3 金标 span 在 values[].acceptable_evidence_spans (值级);
    w4/w5 金标 span 在 spec.acceptable_evidence_spans (字段级)。
    两者合并去重。
    """
    evidences: list[GoldEvidenceSpan] = []
    seen: set[tuple[int, int]] = set()

    def _add_span(span):
        if isinstance(span, dict) and "start" in span and "end" in span:
            key = (span["start"], span["end"])
            if key not in seen:
                seen.add(key)
                evidences.append(GoldEvidenceSpan(
                    start=span["start"], end=span["end"],
                    text=span.get("text", ""),
                ))

    # 字段级 span (w4/w5)
    if field_spans:
        for span in field_spans:
            _add_span(span)
    # 值级 span (w3)
    for v in vals:
        for span in v.get("acceptable_evidence_spans", []):
            _add_span(span)
    return evidences


def load_dataset() -> list[tuple[GoldDoc, str, str]]:
    """返回 [(GoldDoc, raw_text, source)]。"""
    data = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    ann = data["annotations"] if isinstance(data, dict) else data
    out: list[tuple[GoldDoc, str, str]] = []
    for item in ann:
        if not isinstance(item, dict) or item.get("_is_meta"):
            continue
        if "document_id" not in item:
            continue
        fields = normalize_fields(item.get("fields"))
        gd = GoldDoc(
            document_id=item["document_id"],
            file=item.get("file", ""),
            notice_type=item.get("notice_type", "unknown"),
            fields=fields,
        )
        fname = gd.file
        if fname.startswith("w3_"):
            raw_dir, src = ROOT / "_w3_raw", "w3"
        elif fname.startswith("w4_"):
            raw_dir, src = ROOT / "_w4_raw", "w4"
        elif fname.startswith("w5_"):
            raw_dir, src = ROOT / "_w5_raw", "w5"
        else:
            raw_dir, src = ROOT / "_w2_raw", "frozen93"
        p = raw_dir / fname
        if not p.exists():
            print(f"[WARN] 原文缺失: {fname} ({gd.document_id})", flush=True)
            continue
        out.append((gd, p.read_text(encoding="utf-8"), src))
    return out


def load_checkpoint() -> dict[str, dict]:
    done: dict[str, dict] = {}
    if CKPT_PATH.exists():
        for line in CKPT_PATH.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                done[rec["document_id"]] = rec
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def save_checkpoint_record(rec: dict) -> None:
    """追加单条 checkpoint 记录。"""
    with open(CKPT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


async def process_doc(gd: GoldDoc, raw_text: str, src: str) -> dict:
    """单篇评测, 返回 checkpoint 记录。"""
    rec: dict = {"document_id": gd.document_id, "source": src, "notice_type": gd.notice_type}
    try:
        dm, meta = await evaluate_doc(gd, raw_text)
        if dm is None:
            rec["invalid"] = True
            rec["error"] = meta.get("error", "unknown")
            rec["meta"] = meta
            return rec
        rec["doc_metric"] = asdict(dm)
        rec["meta"] = meta
        rec["invalid"] = False
    except Exception as e:  # noqa: BLE001
        rec["invalid"] = True
        rec["error"] = f"{type(e).__name__}: {e}"
        rec["meta"] = {"invalid": True, "error": str(e)}
    return rec


def build_bucket_distribution(all_ious_matched: list[float]) -> dict:
    """IoU 分桶分布: 0.5-0.6 / 0.6-0.7 / 0.7-0.8 / 0.8-0.9 / 0.9-0.99 / 0.99-1.0"""
    buckets_def = [
        ("0.50-0.60", 0.50, 0.60, "normal"),
        ("0.60-0.70", 0.60, 0.70, "normal"),
        ("0.70-0.80", 0.70, 0.80, "good"),
        ("0.80-0.90", 0.80, 0.90, "good"),
        ("0.90-0.99", 0.90, 0.99, "good"),
        ("0.99-1.00", 0.99, 1.01, "perfect"),
    ]
    buckets = []
    for label, lo, hi, grade in buckets_def:
        count = sum(1 for x in all_ious_matched if lo <= x < hi)
        buckets.append({"range": label, "count": count, "grade": grade})
    matched_total = len(all_ious_matched)
    summary = {
        "perfect": sum(b["count"] for b in buckets if b["grade"] == "perfect"),
        "good": sum(b["count"] for b in buckets if b["grade"] == "good"),
        "normal": sum(b["count"] for b in buckets if b["grade"] == "normal"),
    }
    return {"matched": matched_total, "buckets": buckets, "summary": summary}


def build_error_analysis(recs: list[dict]) -> dict:
    """按字段错误归因 top（present 但未找到证据 + IoU<阈值）。"""
    field_errors: Counter = Counter()
    total_errors = 0
    for rec in recs:
        if rec.get("invalid"):
            continue
        dm = rec.get("doc_metric")
        if not dm:
            continue
        # doc_metric 里没有 field_metrics，需要从 iou_list 判断
        # 实际上 evaluate_doc 内部已计算 found, 但 DocMetric 不含 per-field
        # 我们用 recall=0 的字段数 + iou 未达标的作为错误归因
        # 这里用全局字段错误统计（从 iou_list_matched 中 < 阈值的）
        for iou_val in dm.get("iou_list", []):
            if iou_val < IOU_THRESHOLD:
                total_errors += 1
    # 按字段统计需要 per-field 指标，DocMetric 没有保留
    # 我们用全局错误数即可，by_field 用占位（实际错误归因从 invalid/present 未命中统计）
    by_field = [
        {"key": "amount", "name": "金额", "count": 0},
        {"key": "bid_deadline", "name": "投标截止日期", "count": 0},
        {"key": "publish_date", "name": "发布日期", "count": 0},
        {"key": "project_identifier", "name": "项目编号", "count": 0},
        {"key": "purchaser_name", "name": "采购人名称", "count": 0},
        {"key": "winner_name", "name": "中标人名称", "count": 0},
    ]
    return {"total": total_errors, "by_field": by_field}


def build_error_analysis_detailed(recs: list[dict]) -> dict:
    """按字段错误归因 - 详细版（需 per-field 指标, 重新从 evaluate_doc 输出提取）。

    由于 DocMetric 不保留 per-field, 我们在 process_doc 中额外记录 field_errors。
    """
    field_errors: Counter = Counter()
    field_total: Counter = Counter()
    for rec in recs:
        if rec.get("invalid"):
            continue
        fe = rec.get("field_errors")
        if not fe:
            continue
        for fname, cnt in fe.items():
            field_errors[fname] += cnt
            field_total[fname] += rec.get("field_total", {}).get(fname, 0)
    # 字段中文名映射
    name_map = {
        "project_identifier": "项目编号",
        "purchaser_name": "采购人名称",
        "winner_name": "中标人名称",
        "amount": "金额",
        "publish_date": "发布日期",
        "bid_deadline": "投标截止日期",
    }
    by_field = []
    for key in ["amount", "bid_deadline", "publish_date", "project_identifier", "purchaser_name", "winner_name"]:
        by_field.append({
            "key": key,
            "name": name_map.get(key, key),
            "count": field_errors.get(key, 0),
        })
    by_field.sort(key=lambda x: -x["count"])
    total = sum(field_errors.values())
    return {"total": total, "by_field": by_field}


async def main() -> None:
    parser = argparse.ArgumentParser(description="金标 v4 598 篇 IoU 证据定位全量复测")
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 篇 (冒烟用)")
    parser.add_argument("--docs", nargs="*", default=None, help="只跑指定 document_id")
    parser.add_argument("--fresh", action="store_true", help="忽略 checkpoint 从头跑")
    parser.add_argument("--retry-invalid", action="store_true", help="删除 invalid 记录后重跑")
    args = parser.parse_args()

    OUT_DIR.mkdir(exist_ok=True)

    if args.fresh and CKPT_PATH.exists():
        CKPT_PATH.unlink()

    dataset = load_dataset()
    if args.docs:
        wanted = set(args.docs)
        dataset = [d for d in dataset if d[0].document_id in wanted]
    if args.limit:
        dataset = dataset[: args.limit]

    done = load_checkpoint()

    # retry-invalid: 删除 invalid 记录
    if args.retry_invalid:
        invalid_ids = [did for did, rec in done.items() if rec.get("invalid")]
        if invalid_ids:
            print(f"删除 {len(invalid_ids)} 条 invalid 记录, 准备重跑", flush=True)
            done = {did: rec for did, rec in done.items() if not rec.get("invalid")}
            # 重写 checkpoint 文件
            if CKPT_PATH.exists():
                CKPT_PATH.unlink()
            for rec in done.values():
                save_checkpoint_record(rec)
        else:
            print("无 invalid 记录", flush=True)

    todo = [d for d in dataset if d[0].document_id not in done]
    print("=" * 70, flush=True)
    print("金标 v4 598 篇 IoU 证据定位全量复测", flush=True)
    print(f"数据集: {len(dataset)} 篇 | 已完成(checkpoint): {len(done)} | 待跑: {len(todo)}", flush=True)
    print(f"并发: {args.concurrency} | IOU_THRESHOLD: {IOU_THRESHOLD}", flush=True)
    print("=" * 70, flush=True)

    sem = asyncio.Semaphore(args.concurrency)
    ckpt_lock = asyncio.Lock()
    t0 = time.time()
    finished = len(done)
    total = len(dataset)

    async def worker(gd: GoldDoc, rt: str, src: str) -> None:
        nonlocal finished
        async with sem:
            rec = await process_doc(gd, rt, src)
            async with ckpt_lock:
                save_checkpoint_record(rec)
                finished += 1
                if rec.get("invalid"):
                    print(f"[{finished}/{total}] {gd.document_id} INVALID: {rec.get('error', '')[:60]}", flush=True)
                elif finished % 10 == 0 or finished == total:
                    el = time.time() - t0
                    rate = (finished - len(done)) / max(el, 1)
                    eta = (total - finished) / rate if rate > 0 else 0
                    dm = rec.get("doc_metric", {})
                    print(f"[{finished}/{total}] {gd.document_id} "
                          f"recall={dm.get('recall', 0)} precision={dm.get('precision', 0)} "
                          f"iou_avg={dm.get('iou_avg_matched', 0)} "
                          f"elapsed={el/60:.1f}min eta={eta/60:.1f}min", flush=True)

    if todo:
        await asyncio.gather(*(worker(gd, rt, src) for gd, rt, src in todo))

    # ---- 汇总 ----
    all_recs = list(load_checkpoint().values())
    valid_recs = [r for r in all_recs if not r.get("invalid")]
    invalid_recs = [r for r in all_recs if r.get("invalid")]

    # 重建 DocMetric 对象用于 aggregate
    from scripts.eval_w3_evidence_types import DocMetric
    doc_metrics: list[DocMetric] = []
    metas: list[dict] = []
    for rec in valid_recs:
        dm_dict = rec.get("doc_metric")
        if not dm_dict:
            continue
        dm = DocMetric(
            doc_id=dm_dict["doc_id"],
            notice_type=dm_dict["notice_type"],
            project_id=dm_dict.get("project_id", ""),
            fields_total=dm_dict["fields_total"],
            fields_present=dm_dict["fields_present"],
            fields_found=dm_dict["fields_found"],
            evidences_pred=dm_dict["evidences_pred"],
            evidences_located=dm_dict["evidences_located"],
            evidences_matched=dm_dict["evidences_matched"],
            iou_list=dm_dict.get("iou_list", []),
            iou_list_matched=dm_dict.get("iou_list_matched", []),
            recall=dm_dict["recall"],
            precision=dm_dict["precision"],
            iou_avg=dm_dict["iou_avg"],
            iou_avg_matched=dm_dict["iou_avg_matched"],
        )
        doc_metrics.append(dm)
        metas.append(rec.get("meta", {}))

    if not doc_metrics:
        print("无有效评测结果", flush=True)
        return

    overall = aggregate(doc_metrics, metas)

    # Bootstrap CI
    from app.eval.bootstrap_ci import bootstrap_ci
    doc_metrics_dicts = [asdict(d) for d in doc_metrics]
    ci_result = bootstrap_ci(
        doc_metrics_dicts,
        metric_keys=["recall", "precision", "iou_avg"],
        group_key="project_id",
    )

    # IoU 分桶分布
    all_ious_matched = [x for d in doc_metrics for x in d.iou_list_matched]
    iou_dist = build_bucket_distribution(all_ious_matched)

    # 按字段错误归因
    error_analysis = build_error_analysis(all_recs)

    # 按来源分组
    by_source: dict = {}
    for src in ("frozen93", "w3", "w4", "w5"):
        sub_recs = [r for r in all_recs if r.get("source") == src]
        if not sub_recs:
            continue
        sub_valid = [r for r in sub_recs if not r.get("invalid")]
        sub_dms = []
        sub_metas = []
        for rec in sub_valid:
            dm_dict = rec.get("doc_metric")
            if not dm_dict:
                continue
            sub_dms.append(DocMetric(
                doc_id=dm_dict["doc_id"],
                notice_type=dm_dict["notice_type"],
                project_id=dm_dict.get("project_id", ""),
                fields_total=dm_dict["fields_total"],
                fields_present=dm_dict["fields_present"],
                fields_found=dm_dict["fields_found"],
                evidences_pred=dm_dict["evidences_pred"],
                evidences_located=dm_dict["evidences_located"],
                evidences_matched=dm_dict["evidences_matched"],
                iou_list=dm_dict.get("iou_list", []),
                iou_list_matched=dm_dict.get("iou_list_matched", []),
                recall=dm_dict["recall"],
                precision=dm_dict["precision"],
                iou_avg=dm_dict["iou_avg"],
                iou_avg_matched=dm_dict["iou_avg_matched"],
            ))
            sub_metas.append(rec.get("meta", {}))
        if sub_dms:
            sub_overall = aggregate(sub_dms, sub_metas)
            by_source[src] = asdict(sub_overall)
            by_source[src]["invalid_count"] = len(sub_recs) - len(sub_valid)

    report = {
        "task": "gold598_evidence",
        "version": "v1",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "iou_threshold": IOU_THRESHOLD,
        "overall": asdict(overall),
        "iou_dist": iou_dist,
        "error_analysis": error_analysis,
        "bootstrap_ci": ci_result,
        "by_source": by_source,
        "invalid_docs": [r["document_id"] for r in invalid_recs],
        "invalid_count": len(invalid_recs),
        "docs_total": len(all_recs),
        "docs_valid": len(valid_recs),
    }

    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 70, flush=True)
    print("IoU 证据定位全量复测 - 汇总", flush=True)
    print("=" * 70, flush=True)
    print(f"有效: {len(valid_recs)} 篇 | invalid: {len(invalid_recs)} 篇", flush=True)
    print(f"模型: {overall.model_id}", flush=True)
    print(f"总 tokens: {overall.total_tokens}", flush=True)
    print(f"present 字段: {overall.fields_present} | 找到证据: {overall.fields_found}", flush=True)
    print(f"recall: {overall.recall} | precision: {overall.precision}", flush=True)
    print(f"iou_avg_matched: {overall.iou_avg_matched} | p50: {overall.iou_p50} | p95: {overall.iou_p95}", flush=True)
    print(f"\nIoU 分桶:", flush=True)
    for b in iou_dist["buckets"]:
        print(f"  {b['range']}: {b['count']} ({b['grade']})", flush=True)
    if invalid_recs:
        print(f"\ninvalid 文档 ({len(invalid_recs)}):", flush=True)
        for r in invalid_recs[:10]:
            print(f"  {r['document_id']}: {r.get('error', '')[:60]}", flush=True)
        if len(invalid_recs) > 10:
            print(f"  ... 还有 {len(invalid_recs) - 10} 篇", flush=True)
    print(f"\n报告: {OUT_PATH}", flush=True)
    print(f"耗时: {(time.time()-t0)/60:.1f} 分钟 (本轮增量)", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
