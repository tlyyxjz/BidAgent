"""W2-08 消融实验 A/B/C 三组。

对应总规划 v4.1 第十章 10.8 消融实验设计 + 第二周任务清单 W2-08。

三组对比:
- A 组 (Direct LLM): 直接输出字段，无证据要求
- B 组 (LLM + 候选证据): LLM 输出字段 + 候选证据文本，但不做程序验证
- C 组 (LLM + 程序证据验证): 验证证据存在性 + 字段等价性 + 确定性字段校验

三组使用相同底层模型、相同公告文本、相同字段定义、相同提示词主体。

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
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORK_DIR = Path(r"C:\Users\Lenovo\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\work-mode-projects\6a57291a0778ce48bfe693d2")
RAW_DIR = WORK_DIR / "_w2_raw"
ANNOT_DIR = WORK_DIR / "_w2_annotations"

from app.llm.extractor import (
    call_extraction_llm,
    call_extraction_llm_no_evidence,
    compute_prompt_hash,
    EXTRACTION_SYSTEM_PROMPT_NO_EVIDENCE,
    EXTRACTION_FEWSHOT_EXAMPLES_NO_EVIDENCE,
)
from app.llm.extraction_schemas import CORE_FIELD_NAMES, ExtractionResult, FieldExtraction
from app.processors.evidence_locator import EvidenceLocator
from app.processors.field_validator import (
    validate_amount, validate_date, validate_project_identifier,
    ValidationResult,
)


# 7 篇金标 (W1 已有 + W2 D2 验证过的)
DEFAULT_DOCS = [
    "tender_06",
    "tender_07",
    "award_05",
    "award_06",
    "correction_04",
    "correction_05",
    "multi_lot_02",
]


@dataclass
class GoldField:
    field_name: str
    gold_status: str  # present/absent/ambiguous/multi_value
    values: list  # [{"raw_value": ..., "acceptable_evidence_spans": [{"start","end","text"}]}]


@dataclass
class GoldDoc:
    document_id: str
    file: str
    fields: list  # [GoldField]


@dataclass
class GroupResult:
    group: str  # A/B/C
    doc_id: str
    field_name: str
    gold_status: str
    pred_status: str  # present/absent/ambiguous/multi_value/missing
    has_value: bool  # 系统是否输出了值
    has_evidence: bool  # 系统是否输出证据 (B/C 才有)
    evidence_verified: bool  # 证据是否在原文中存在 (C 才有)
    field_validated: bool  # 字段是否通过确定性校验 (C 才有)
    unjustified: bool  # 无依据输出 (有值但无证据/证据不存在)
    correct: Optional[bool]  # 字段值是否与金标一致 (None=无法判断)


@dataclass
class ExpSummary:
    group: str
    docs_count: int
    fields_total: int
    fields_with_value: int
    fields_with_evidence: int
    fields_evidence_verified: int
    fields_field_validated: int
    fields_unjustified: int
    unjustified_rate: float
    fields_correct: int
    fields_evaluable: int
    field_precision: float
    evidence_precision: float  # C 组字段级证据验证率 (已验证证据字段 / 有证据字段)
    model_id: str
    prompt_hash: str
    total_tokens: int
    latency_ms_avg: float
    invalid_docs_count: int = 0  # P2: LLM 失败被排除的文档数
    invalid_docs: list = field(default_factory=list)  # P2: 失败文档 ID 列表


def load_gold_doc(doc_prefix: str) -> Optional[GoldDoc]:
    """加载金标 (从 _w2_annotations 找对应文件)。"""
    matches = list(ANNOT_DIR.glob(f"annotation_{doc_prefix}*.json"))
    if not matches:
        return None
    with open(matches[0], encoding="utf-8") as f:
        data = json.load(f)
    return GoldDoc(
        document_id=data["document_id"],
        file=matches[0].name,
        fields=[
            GoldField(
                field_name=f["field_name"],
                gold_status=f["gold_status"],
                values=f.get("values", []),
            )
            for f in data["fields"]
        ],
    )


def load_raw_text(doc_prefix: str) -> Optional[str]:
    p = RAW_DIR / f"{doc_prefix}.txt"
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


def _status_matches(gold: str, pred: str) -> bool:
    """字段状态匹配判定。"""
    if gold == pred:
        return True
    # absent vs present/ambiguous 都算不匹配
    # multi_value 视为 present 的特殊形式
    if gold == "multi_value" and pred == "present":
        return True
    return False


def _value_correct(field_name: str, pred_value: str, gold_field: GoldField) -> bool:
    """粗略判断字段值是否与金标一致 (子串匹配)。"""
    if not pred_value or not gold_field.values:
        return False
    pred = pred_value.strip()
    for v in gold_field.values:
        gold_v = (v.get("raw_value") or "").strip()
        if not gold_v:
            continue
        # 子串匹配 (容错：LLM 可能多带前后缀)
        if gold_v in pred or pred in gold_v:
            return True
        # 数字类字段：去掉单位后比较
        if field_name == "amount":
            import re
            pred_num = re.findall(r"\d+\.?\d*", pred)
            gold_num = re.findall(r"\d+\.?\d*", gold_v)
            if pred_num and gold_num and pred_num[0] == gold_num[0]:
                return True
    return False


# ========== A 组：Direct LLM（直接输出字段，无证据要求）==========

async def run_group_a(doc: GoldDoc, raw_text: str) -> tuple[list[GroupResult], dict]:
    """A 组：直接调用 LLM，不要求证据（Sol 要求 W2-08 A 组用独立无证据 prompt）。

    修复 (P1)：原实现复用 call_extraction_llm (有证据 prompt) 仅评测时忽略证据，
    导致 LLM 仍被要求输出证据，不符合 "Direct LLM 无证据要求" 的实验目的。
    现改用 call_extraction_llm_no_evidence，使用独立的无证据 prompt + few-shot。
    """
    result = await call_extraction_llm_no_evidence(raw_text)
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
    rows: list[GroupResult] = []
    for gf in doc.fields:
        pred = pred_by_name.get(gf.field_name)
        pred_status = pred.field_status if pred else "missing"
        has_value = bool(pred and pred.raw_value)
        # A 组不评证据 (无证据 prompt，candidate_evidences 必为空)
        correct = _value_correct(gf.field_name, pred.raw_value if pred else "", gf) if has_value else None
        if gf.gold_status == "absent":
            correct = (pred_status == "absent") if pred else True
        rows.append(GroupResult(
            group="A", doc_id=doc.document_id, field_name=gf.field_name,
            gold_status=gf.gold_status, pred_status=pred_status,
            has_value=has_value, has_evidence=False, evidence_verified=False,
            field_validated=False,
            # A 组无证据要求：有值即算无依据 (对比 B/C 组通过证据降低无依据率)
            unjustified=has_value,
            correct=correct,
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
        return [], meta

    pred_by_name = {f.field_name: f for f in result.fields}
    locator = EvidenceLocator(raw_text)
    rows: list[GroupResult] = []
    for gf in doc.fields:
        pred = pred_by_name.get(gf.field_name)
        pred_status = pred.field_status if pred else "missing"
        has_value = bool(pred and pred.raw_value)
        has_evidence = bool(pred and len(pred.candidate_evidences) > 0)
        # B 组不验证证据，evidence_verified=False
        correct = _value_correct(gf.field_name, pred.raw_value if pred else "", gf) if has_value else None
        if gf.gold_status == "absent":
            correct = (pred_status == "absent") if pred else True
        rows.append(GroupResult(
            group="B", doc_id=doc.document_id, field_name=gf.field_name,
            gold_status=gf.gold_status, pred_status=pred_status,
            has_value=has_value, has_evidence=has_evidence, evidence_verified=False,
            field_validated=False,
            unjustified=has_value and not has_evidence,
            correct=correct,
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
    result = await call_extraction_llm(raw_text)
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
    locator = EvidenceLocator(raw_text)
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

        correct = _value_correct(gf.field_name, pred.raw_value if pred else "", gf) if has_value else None
        if gf.gold_status == "absent":
            correct = (pred_status == "absent") if pred else True
        rows.append(GroupResult(
            group="C", doc_id=doc.document_id, field_name=gf.field_name,
            gold_status=gf.gold_status, pred_status=pred_status,
            has_value=has_value, has_evidence=has_evidence,
            evidence_verified=evidence_verified, field_validated=field_validated,
            # C 组：有值但 (无证据 OR 证据未验证 OR 字段未通过校验) 算无依据
            unjustified=has_value and (not evidence_verified or not field_validated),
            correct=correct,
        ))
    return rows, meta


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
    evaluable = sum(1 for r in all_rows if r.correct is not None)
    invalid_docs = invalid_docs or []

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
        evidence_precision=round(ev_verified / max(with_evidence, 1), 4) if group == "C" else 0.0,
        model_id=metas[0].get("model_id", "unknown") if metas else "unknown",
        prompt_hash=metas[0].get("prompt_hash", "") if metas else "",
        total_tokens=sum(m.get("total_tokens", 0) for m in metas),
        latency_ms_avg=round(sum(m.get("latency_ms", 0) for m in metas) / max(len(metas), 1), 1),
        invalid_docs_count=len(invalid_docs),
        invalid_docs=invalid_docs,
    )


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--docs", nargs="*", default=DEFAULT_DOCS, help="要跑的公告前缀列表")
    parser.add_argument("--skip-llm", action="store_true", help="跳过 LLM 调用 (用空结果)")
    parser.add_argument("--out", default=str(WORK_DIR / "_w2_d4_ablation_result.json"))
    args = parser.parse_args()

    print("=" * 70)
    print("W2-08 消融实验 A/B/C 三组")
    print("=" * 70)
    print(f"公告数: {len(args.docs)}")
    print(f"模型: deepseek-v4-flash")
    print(f"prompt_hash: {compute_prompt_hash()}")
    print()

    # 加载金标
    docs: list[tuple[GoldDoc, str]] = []
    for prefix in args.docs:
        gd = load_gold_doc(prefix)
        rt = load_raw_text(prefix)
        if gd is None or rt is None:
            print(f"[WARN] 跳过 {prefix} (金标或原文缺失)")
            continue
        docs.append((gd, rt))
    print(f"实际加载: {len(docs)} 篇")

    if args.skip_llm:
        print("[SKIP-LLM] 跳过 LLM 调用，仅输出空结果")
        return

    all_rows_a, all_rows_b, all_rows_c = [], [], []
    metas_a, metas_b, metas_c = [], [], []
    invalid_a, invalid_b, invalid_c = [], [], []

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

    # 汇总 (传入 invalid_docs 用于报告)
    sum_a = summarize("A", all_rows_a, metas_a, invalid_a)
    sum_b = summarize("B", all_rows_b, metas_b, invalid_b)
    sum_c = summarize("C", all_rows_c, metas_c, invalid_c)

    # 打印 invalid docs 警告
    if invalid_a or invalid_b or invalid_c:
        print("\n" + "!" * 70)
        print("警告: 检测到 LLM 调用失败的 invalid docs (已排除出评测)")
        print(f"  A 组 invalid: {invalid_a}")
        print(f"  B 组 invalid: {invalid_b}")
        print(f"  C 组 invalid: {invalid_c}")
        print("!" * 70)

    print("\n" + "=" * 70)
    print("汇总对比")
    print("=" * 70)
    print(f"{'指标':<24} {'A组':>12} {'B组':>12} {'C组':>12}")
    print("-" * 70)
    print(f"{'字段总数':<24} {sum_a.fields_total:>12} {sum_b.fields_total:>12} {sum_c.fields_total:>12}")
    print(f"{'有值字段':<24} {sum_a.fields_with_value:>12} {sum_b.fields_with_value:>12} {sum_c.fields_with_value:>12}")
    print(f"{'有证据字段':<24} {sum_a.fields_with_evidence:>12} {sum_b.fields_with_evidence:>12} {sum_c.fields_with_evidence:>12}")
    print(f"{'证据已验证':<24} {sum_a.fields_evidence_verified:>12} {sum_b.fields_evidence_verified:>12} {sum_c.fields_evidence_verified:>12}")
    print(f"{'字段已校验':<24} {sum_a.fields_field_validated:>12} {sum_b.fields_field_validated:>12} {sum_c.fields_field_validated:>12}")
    print(f"{'无依据字段':<24} {sum_a.fields_unjustified:>12} {sum_b.fields_unjustified:>12} {sum_c.fields_unjustified:>12}")
    print(f"{'无依据率':<24} {sum_a.unjustified_rate:>12.2%} {sum_b.unjustified_rate:>12.2%} {sum_c.unjustified_rate:>12.2%}")
    print(f"{'字段正确数':<24} {sum_a.fields_correct:>12} {sum_b.fields_correct:>12} {sum_c.fields_correct:>12}")
    print(f"{'字段精确率':<24} {sum_a.field_precision:>12.2%} {sum_b.field_precision:>12.2%} {sum_c.field_precision:>12.2%}")
    print(f"{'证据精确率':<24} {'N/A':>12} {'N/A':>12} {sum_c.evidence_precision:>12.2%}")
    print(f"{'总 tokens':<24} {sum_a.total_tokens:>12} {sum_b.total_tokens:>12} {sum_c.total_tokens:>12}")
    print(f"{'平均延迟 ms':<24} {sum_a.latency_ms_avg:>12.0f} {sum_b.latency_ms_avg:>12.0f} {sum_c.latency_ms_avg:>12.0f}")

    # 保存
    out = {
        "summaries": {s.group: asdict(s) for s in [sum_a, sum_b, sum_c]},
        "rows": {
            "A": [asdict(r) for r in all_rows_a],
            "B": [asdict(r) for r in all_rows_b],
            "C": [asdict(r) for r in all_rows_c],
        },
        "docs": [gd.document_id for gd, _ in docs],
    }
    out_path = Path(args.out)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
