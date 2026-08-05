"""W2-08/W4 消融实验 A/B/C/D 四组。

对应总规划 v4.1 第十章 10.8 消融实验设计 + 第二周任务清单 W2-08。

四组对比:
- A 组 (Direct LLM): 直接输出字段，无证据要求
- B 组 (LLM + 候选证据): LLM 输出字段 + 候选证据文本，但不做程序验证
- C 组 (LLM + 程序证据验证): 验证证据存在性 + 字段等价性 + 确定性字段校验
- D 组 (完整 BidAgent): C 组验证 + display_grade 选择性输出 (grade="low" 拒绝)
  D 组在 C 组验证结果基础上计算 display_grade (v4.1 第八章)，
  按选择性输出策略 (v4.1 第十章 10.7) 拒绝 grade="low" 的字段，
  模拟完整 BidAgent 的来源处理 + 版本/冲突处理 + 选择性输出能力。

四组使用相同底层模型、相同公告文本、相同字段定义、相同提示词主体。

输出对比报告:
- 无依据输出率 (lower is better)
- 字段 P/R/F1 (vs 金标)
- 证据精确率 (higher is better)

约束 (project_memory):
- 真实 LLM 调用，记录模型标识/参数/token/延迟
- 不得使用测试集（W4 才冻结）
- 实验记录模型标识、参数、数据版本、代码提交版本
- 真实数据，未达到目标时如实报告

用法:
    python scripts/eval_ablation.py [--docs 7] [--skip-llm]
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

# app 符号必须在 eval_ablation_groups 之前导入：eval_ablation_groups 通过
# scripts.eval_ablation 模块属性引用这些符号，以支持测试 patch
# (patch("scripts.eval_ablation.call_extraction_llm"))
from app.llm.extractor import (  # noqa: F401
    call_extraction_llm,
    call_extraction_llm_no_evidence,
    compute_prompt_hash,
)
from app.processors.evidence_locator import EvidenceLocator  # noqa: F401

# Re-export 公共符号以保持向后兼容 (tests 通过 scripts.eval_ablation 导入)
from scripts.eval_ablation_types import (  # noqa: F401
    DEFAULT_DOCS,
    GoldField,
    GoldDoc,
    GroupResult,
    ExpSummary,
)
from scripts.eval_ablation_data import (  # noqa: F401
    WORK_DIR,
    W3_OUTPUT_DIR,
    load_gold_all_w3,
    load_gold_doc,
    load_raw_text,
)
from scripts.eval_ablation_helpers import _classify_gold_status  # noqa: F401
from scripts.eval_ablation_groups import (  # noqa: F401
    run_group_a,
    run_group_b,
    run_group_c,
    run_group_d,
)
from scripts.experiment_meta import collect_experiment_meta


# ========== 汇总 ==========

def summarize(
    group: str,
    all_rows: list[GroupResult],
    metas: list[dict],
    invalid_docs: list[str] = None,
) -> ExpSummary:
    total = len(all_rows)
    with_value = sum(1 for r in all_rows if r.has_value)
    with_evidence = sum(1 for r in all_rows if r.has_evidence)
    ev_verified = sum(1 for r in all_rows if r.evidence_verified)
    f_validated = sum(1 for r in all_rows if r.field_validated)
    unjustified = sum(1 for r in all_rows if r.unjustified)
    correct = sum(1 for r in all_rows if r.correct is True)
    # v4.1 §10.3: unreadable/attachment_only 状态不计入可评测字段 (correct=None)
    evaluable = sum(1 for r in all_rows if r.correct is not None)
    invalid_docs = invalid_docs or []

    # v4.1 sec 7.4: 多值字段平均集合级 F1
    mv_f1s = [r.multi_value_f1 for r in all_rows if r.multi_value_f1 is not None]
    mv_f1_avg = round(sum(mv_f1s) / max(len(mv_f1s), 1), 4) if mv_f1s else 0.0

    # v4.1 §10: 空值误报率（should_not_have_value 字段中系统错误输出值的比例）
    should_not_have_value_fields = [r for r in all_rows if _classify_gold_status(r.gold_status) == "should_not_have_value"]
    null_false_positives = sum(1 for r in should_not_have_value_fields if r.has_value)
    null_false_positive_rate = round(
        null_false_positives / len(should_not_have_value_fields), 4
    ) if should_not_have_value_fields else 0.0

    # v4.1 §10.12 收集实验复现信息（16 项）
    _model_id = metas[0].get("model_id", "unknown") if metas else "unknown"
    _prompt_hash = metas[0].get("prompt_hash", "") if metas else ""
    meta = collect_experiment_meta(
        model_id=_model_id,
        prompt_hash=_prompt_hash,
        dataset_path=None,
    )

    return ExpSummary(
        group=group,
        docs_count=len({r.doc_id for r in all_rows}),
        fields_total=total,
        fields_with_value=with_value,
        fields_with_evidence=with_evidence,
        fields_evidence_verified=ev_verified,
        fields_field_validated=f_validated,
        fields_unjustified=unjustified,
        unjustified_rate=round(unjustified / max(with_value, 1), 4),
        fields_correct=correct,
        fields_evaluable=evaluable,
        field_precision=round(correct / max(evaluable, 1), 4),
        # C 组字段级证据验证率 (已验证证据字段 / 有证据字段)
        # 命名澄清 (P3): 此处是字段级，非证据级，与 W2-09 证据级精确率口径不同
        evidence_precision=round(ev_verified / max(with_evidence, 1), 4) if group in ("C", "D") else 0.0,
        model_id=_model_id,
        prompt_hash=_prompt_hash,
        total_tokens=sum(m.get("total_tokens", 0) for m in metas),
        latency_ms_avg=round(sum(m.get("latency_ms", 0) for m in metas) / max(len(metas), 1), 1),
        invalid_docs_count=len(invalid_docs),
        invalid_docs=invalid_docs,
        multi_value_f1_avg=mv_f1_avg,
        null_false_positive_rate=null_false_positive_rate,
        # ==== v4.1 §10.12 实验复现信息（14 项新增）====
        model_role=meta.model_role,
        provider=meta.provider,
        model_snapshot=meta.model_snapshot,
        request_time=meta.request_time,
        temperature=meta.temperature,
        top_p=meta.top_p,
        seed=meta.seed,
        request_id=meta.request_id,
        response_hash=meta.response_hash,
        normalizer_version=meta.normalizer_version,
        evidence_rule_version=meta.evidence_rule_version,
        display_rule_version=meta.display_rule_version,
        dataset_version=meta.dataset_version,
        code_commit=meta.code_commit,
    )


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--docs", nargs="*", default=None, help="要跑的公告前缀列表 (w2) 或 document_id 子集 (w3)")
    parser.add_argument("--source", choices=["w2", "w3"], default="w2", help="数据源: w2=21篇W2标注, w3=99篇W3金标")
    parser.add_argument("--skip-llm", action="store_true", help="跳过 LLM 调用 (用空结果)")
    parser.add_argument("--out", default=None, help="输出路径 (默认自动: w2->WORK_DIR/_w2_d4_ablation_result.json, w3->W3_OUTPUT_DIR/w3_ablation_full.json)")
    args = parser.parse_args()

    source = args.source

    # w3 模式: 从 gold JSON 动态读取全量 document_id; w2 模式: 用 DEFAULT_DOCS
    if source == "w3":
        all_gold = load_gold_all_w3()
        docs_to_run = [gd.document_id for gd in all_gold]
        if args.docs:
            wanted = set(args.docs)
            docs_to_run = [d for d in docs_to_run if d in wanted]
        out_path = args.out or str(W3_OUTPUT_DIR / "w3_ablation_full.json")
        W3_OUTPUT_DIR.mkdir(exist_ok=True)
    else:
        docs_to_run = args.docs if args.docs is not None else DEFAULT_DOCS
        out_path = args.out or str(WORK_DIR / "_w2_d4_ablation_result.json")

    print("=" * 70)
    print("W2-08/W4 消融实验 A/B/C/D 四组")
    print("=" * 70)
    print(f"数据源: {source}")
    print(f"公告数: {len(docs_to_run)}")
    print(f"模型: deepseek-v4-flash")
    print(f"prompt_hash: {compute_prompt_hash()}")
    print(f"输出: {out_path}")
    print()

    # 加载金标和原文
    docs: list[tuple[GoldDoc, str]] = []
    for prefix in docs_to_run:
        gd = load_gold_doc(prefix, source=source)
        rt = load_raw_text(prefix, source=source)
        if gd is None or rt is None:
            print(f"[WARN] 跳过 {prefix} (金标或原文缺失)")
            continue
        docs.append((gd, rt))
    print(f"实际加载: {len(docs)} 篇")

    if args.skip_llm:
        print("[SKIP-LLM] 跳过 LLM 调用，仅输出空结果")
        return

    all_rows_a, all_rows_b, all_rows_c, all_rows_d = [], [], [], []
    metas_a, metas_b, metas_c, metas_d = [], [], [], []
    invalid_a, invalid_b, invalid_c, invalid_d = [], [], [], []

    for gd, rt in docs:
        print(f"\n--- {gd.document_id} ({len(rt)} 字符) ---")
        # A 组
        rows_a, meta_a = await run_group_a(gd, rt)
        if meta_a.get("invalid"):
            print(f"  A: [INVALID] tokens={meta_a['total_tokens']}, error={meta_a['error']} - 跳过评测")
            invalid_a.append(gd.document_id)
            metas_a.append(meta_a)
        else:
            print(f"  A: {len(rows_a)} 字段, tokens={meta_a['total_tokens']}, latency={meta_a['latency_ms']}ms, error={meta_a['error']}")
            all_rows_a.extend(rows_a); metas_a.append(meta_a)
        # B 组
        rows_b, meta_b = await run_group_b(gd, rt)
        if meta_b.get("invalid"):
            print(f"  B: [INVALID] tokens={meta_b['total_tokens']}, error={meta_b['error']} - 跳过评测")
            invalid_b.append(gd.document_id)
            metas_b.append(meta_b)
        else:
            print(f"  B: {len(rows_b)} 字段, tokens={meta_b['total_tokens']}, latency={meta_b['latency_ms']}ms")
            all_rows_b.extend(rows_b); metas_b.append(meta_b)
        # C 组
        rows_c, meta_c = await run_group_c(gd, rt)
        if meta_c.get("invalid"):
            print(f"  C: [INVALID] tokens={meta_c['total_tokens']}, error={meta_c['error']} - 跳过评测")
            invalid_c.append(gd.document_id)
            metas_c.append(meta_c)
        else:
            print(f"  C: {len(rows_c)} 字段, tokens={meta_c['total_tokens']}, latency={meta_c['latency_ms']}ms")
            all_rows_c.extend(rows_c); metas_c.append(meta_c)
        # D 组 (复用 C 组 LLM 调用结果 + display_grade 选择性输出)
        # Baseline 公平性: D 组不重新调用 LLM，与 C 组共享同一次 LLM 调用
        if meta_c.get("invalid"):
            rows_d, meta_d = [], meta_c
            print(f"  D: [INVALID] 复用 C 组 invalid 状态 - 跳过评测")
            invalid_d.append(gd.document_id)
            metas_d.append(meta_d)
        else:
            rows_d, meta_d = await run_group_d(rows_c, meta_c)
            gd_dist = meta_d.get("display_grade_dist", {})
            print(f"  D: {len(rows_d)} 字段, tokens={meta_d['total_tokens']} (复用C组), latency={meta_d['latency_ms']}ms (复用C组), "
                  f"grade={gd_dist}")
            all_rows_d.extend(rows_d); metas_d.append(meta_d)

    # 汇总 (传入 invalid_docs 用于报告)
    sum_a = summarize("A", all_rows_a, metas_a, invalid_a)
    sum_b = summarize("B", all_rows_b, metas_b, invalid_b)
    sum_c = summarize("C", all_rows_c, metas_c, invalid_c)
    sum_d = summarize("D", all_rows_d, metas_d, invalid_d)

    # 打印 invalid docs 警告
    if invalid_a or invalid_b or invalid_c or invalid_d:
        print("\n" + "!" * 70)
        print("警告: 检测到 LLM 调用失败的 invalid docs (已排除出评测)")
        print(f"  A 组 invalid: {invalid_a}")
        print(f"  B 组 invalid: {invalid_b}")
        print(f"  C 组 invalid: {invalid_c}")
        print(f"  D 组 invalid: {invalid_d}")
        print("!" * 70)

    print("\n" + "=" * 70)
    print("汇总对比")
    print("=" * 70)
    print(f"{'指标':<24} {'A组':>12} {'B组':>12} {'C组':>12} {'D组':>12}")
    print("-" * 80)
    print(f"{'字段总数':<24} {sum_a.fields_total:>12} {sum_b.fields_total:>12} {sum_c.fields_total:>12} {sum_d.fields_total:>12}")
    print(f"{'有值字段':<24} {sum_a.fields_with_value:>12} {sum_b.fields_with_value:>12} {sum_c.fields_with_value:>12} {sum_d.fields_with_value:>12}")
    print(f"{'有证据字段':<24} {sum_a.fields_with_evidence:>12} {sum_b.fields_with_evidence:>12} {sum_c.fields_with_evidence:>12} {sum_d.fields_with_evidence:>12}")
    print(f"{'证据已验证':<24} {sum_a.fields_evidence_verified:>12} {sum_b.fields_evidence_verified:>12} {sum_c.fields_evidence_verified:>12} {sum_d.fields_evidence_verified:>12}")
    print(f"{'字段已校验':<24} {sum_a.fields_field_validated:>12} {sum_b.fields_field_validated:>12} {sum_c.fields_field_validated:>12} {sum_d.fields_field_validated:>12}")
    print(f"{'无依据字段':<24} {sum_a.fields_unjustified:>12} {sum_b.fields_unjustified:>12} {sum_c.fields_unjustified:>12} {sum_d.fields_unjustified:>12}")
    print(f"{'无依据率':<24} {sum_a.unjustified_rate:>12.2%} {sum_b.unjustified_rate:>12.2%} {sum_c.unjustified_rate:>12.2%} {sum_d.unjustified_rate:>12.2%}")
    print(f"{'字段正确数':<24} {sum_a.fields_correct:>12} {sum_b.fields_correct:>12} {sum_c.fields_correct:>12} {sum_d.fields_correct:>12}")
    print(f"{'字段精确率':<24} {sum_a.field_precision:>12.2%} {sum_b.field_precision:>12.2%} {sum_c.field_precision:>12.2%} {sum_d.field_precision:>12.2%}")
    print(f"{'证据精确率':<24} {'N/A':>12} {'N/A':>12} {sum_c.evidence_precision:>12.2%} {sum_d.evidence_precision:>12.2%}")
    print(f"{'多值字段 F1':<24} {sum_a.multi_value_f1_avg:>12.4f} {sum_b.multi_value_f1_avg:>12.4f} {sum_c.multi_value_f1_avg:>12.4f} {sum_d.multi_value_f1_avg:>12.4f}")
    print(f"{'空值误报率':<24} {sum_a.null_false_positive_rate:>12.4f} {sum_b.null_false_positive_rate:>12.4f} {sum_c.null_false_positive_rate:>12.4f} {sum_d.null_false_positive_rate:>12.4f}")
    print(f"{'总 tokens':<24} {sum_a.total_tokens:>12} {sum_b.total_tokens:>12} {sum_c.total_tokens:>12} {sum_d.total_tokens:>12}")
    print(f"{'平均延迟 ms':<24} {sum_a.latency_ms_avg:>12.0f} {sum_b.latency_ms_avg:>12.0f} {sum_c.latency_ms_avg:>12.0f} {sum_d.latency_ms_avg:>12.0f}")

    # 保存
    out = {
        "summaries": {s.group: asdict(s) for s in [sum_a, sum_b, sum_c, sum_d]},
        "rows": {
            "A": [asdict(r) for r in all_rows_a],
            "B": [asdict(r) for r in all_rows_b],
            "C": [asdict(r) for r in all_rows_c],
            "D": [asdict(r) for r in all_rows_d],
        },
        "docs": [gd.document_id for gd, _ in docs],
    }
    final_out_path = Path(out_path)
    final_out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已保存: {final_out_path}")


if __name__ == "__main__":
    asyncio.run(main())
