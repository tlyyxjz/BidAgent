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
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Re-export 公共符号以保持向后兼容
from scripts.eval_w3_evidence_types import (  # noqa: F401
    IOU_THRESHOLD,
    GoldEvidenceSpan,
    GoldField,
    GoldDoc,
    FieldMetric,
    DocMetric,
    OverallMetric,
)
from scripts.eval_w3_evidence_data import (  # noqa: F401
    RAW_DIR,
    GOLD_PATH,
    OUTPUT_DIR,
    load_gold_all,
    load_raw_text,
    get_body_offset,
)
from scripts.eval_w3_evidence_metrics import (  # noqa: F401
    compute_iou,
    match_evidence,
    evaluate_doc,
    percentile,
    aggregate,
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

    # Bootstrap CI (v4.1 10.10) - 按 project_id 分组
    from app.eval.bootstrap_ci import bootstrap_ci
    doc_metrics_dicts = [asdict(d) for d in doc_metrics]
    ci_result = bootstrap_ci(
        doc_metrics_dicts,
        metric_keys=["recall", "precision", "iou_avg"],
        group_key="project_id",
    )

    # 写报告
    report = {
        "task": "W3-03",
        "version": "v1",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "overall": asdict(overall),
        "doc_metrics": [asdict(d) for d in doc_metrics],
        "bootstrap_ci": ci_result,
    }
    Path(args.output).parent.mkdir(exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(flush=True)
    print(f"报告已写入: {args.output}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
