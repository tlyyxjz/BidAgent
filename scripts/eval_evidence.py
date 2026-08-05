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
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# app 符号必须在 eval_evidence_metrics 之前导入：eval_evidence_metrics 通过
# scripts.eval_evidence 模块属性引用 call_extraction_llm，以支持测试 patch
from app.llm.extractor import call_extraction_llm, compute_prompt_hash  # noqa: F401

# Re-export 公共符号以保持向后兼容 (tests 通过 scripts.eval_evidence 导入)
from scripts.eval_evidence_types import (  # noqa: F401
    DEFAULT_DOCS,
    IOU_THRESHOLD,
    GoldEvidenceSpan,
    GoldField,
    GoldDoc,
    FieldMetric,
    DocMetric,
    OverallMetric,
)
from scripts.eval_evidence_metrics import (  # noqa: F401
    WORK_DIR,
    load_gold_doc,
    load_raw_text,
    compute_iou,
    match_evidence,
    evaluate_doc,
    percentile,
)


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
    invalid_docs: list[str] = []  # P0-2: LLM 失败的 doc_id 列表
    for gd, rt in docs:
        print(f"--- {gd.document_id} ({len(rt)} 字符) ---")
        dm, meta = await evaluate_doc(gd, rt)
        if meta.get("invalid") or dm is None:
            # P0-2: LLM 失败 (error/tokens=0/fields 空)，跳过评测避免指标虚低
            print(f"  [INVALID] tokens={meta['total_tokens']}, error={meta['error']} - 跳过评测")
            invalid_docs.append(gd.document_id)
            continue
        print(f"  字段 present: {dm.fields_present}, found: {dm.fields_found}, "
              f"recall={dm.recall:.2%}, precision={dm.precision:.2%}, "
              f"iou_avg={dm.iou_avg:.4f} (overall), iou_avg_matched={dm.iou_avg_matched:.4f}, "
              f"located={dm.evidences_located}/{dm.evidences_pred}, "
              f"tokens={meta['total_tokens']}, latency={meta['latency_ms']}ms")
        doc_metrics.append(dm); metas.append(meta)

    # P0-2: 打印 invalid 警告 (recall/precision 分母已排除 invalid 篇)
    if invalid_docs:
        print(f"\n⚠️ {len(invalid_docs)} 篇 LLM 失败 (已排除出指标计算): {invalid_docs}")

    # 汇总
    total_fields = sum(dm.fields_total for dm in doc_metrics)
    total_present = sum(dm.fields_present for dm in doc_metrics)
    total_found = sum(dm.fields_found for dm in doc_metrics)
    total_pred = sum(dm.evidences_pred for dm in doc_metrics)
    total_located = sum(dm.evidences_located for dm in doc_metrics)  # P3
    total_matched = sum(dm.evidences_matched for dm in doc_metrics)
    all_ious = sorted([x for dm in doc_metrics for x in dm.iou_list])
    all_ious_matched = sorted([x for dm in doc_metrics for x in dm.iou_list_matched])  # P3

    # P1-18: iou_avg 用 all_ious_matched 求和 (未匹配算0) / total_pred 做分母 (未定位算0)
    # iou_avg_matched 用 all_ious_matched 做分母 (原口径，仅匹配证据)
    iou_avg_overall = round(sum(all_ious_matched) / max(total_pred, 1), 4) if total_pred else 0.0
    iou_avg_matched = round(sum(all_ious_matched) / max(len(all_ious_matched), 1), 4) if all_ious_matched else 0.0

    overall = OverallMetric(
        docs_count=len(doc_metrics),
        fields_total=total_fields,
        fields_present=total_present,
        fields_found=total_found,
        evidences_pred=total_pred,
        evidences_located=total_located,
        evidences_matched=total_matched,
        recall=round(total_found / max(total_present, 1), 4),
        precision=round(total_matched / max(total_pred, 1), 4),
        iou_avg=iou_avg_overall,
        iou_avg_matched=iou_avg_matched,
        iou_p50=round(percentile(all_ious_matched, 0.5), 4),  # P3: p50/p95 用 matched 口径
        iou_p95=round(percentile(all_ious_matched, 0.95), 4),
        model_id=metas[0].get("model_id", "unknown") if metas else "unknown",
        prompt_hash=metas[0].get("prompt_hash", "") if metas else "",
        total_tokens=sum(m.get("total_tokens", 0) for m in metas),
        invalid_docs=invalid_docs,
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
