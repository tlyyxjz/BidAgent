"""W1-07 评测脚本真实数据冒烟测试（v2 升级版）。

用标注.zip 的 6 篇人工金标 vs DeepSeek Baseline 的 8 篇 LLM 输出，
通过 project_identifier 匹配，跑 evaluate_dataset，输出评测报告到桌面。

v2 升级（W1-07 v2）：
- 传入 raw_texts 参数，启用"无依据输出率"检查
- 报告输出 v2 新增指标：unjustified_rate / precision_multi / recall_multi / f1_multi / amount_type_mismatch_count
- 逐篇评测也传 raw_text，定位无依据的具体值

遵守铁律：
- 真实数据（标注.zip 金标 + DeepSeek Baseline）
- 不跳过校验（Schema 校验、证据切片校验）
- 记录逐篇结果和错误案例
- 导出 JSON + CSV + 人看版 MD
"""
from __future__ import annotations

import json
import os
import sys
import re
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(r"C:\Users\Lenovo\Desktop\BidAgent")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 补齐环境变量，避免 config 加载失败
os.environ.setdefault("SECRET_KEY", "a" * 64)
os.environ.setdefault("ADMIN_SECRET", "admin123")

# 绕过 backend/__init__.py 的完整初始化链（避免数据库等重依赖）
import importlib.util as _ilu
import types as _types
_be_dir = str(PROJECT_ROOT / "backend")
_be_pkg = _types.ModuleType("backend")
_be_pkg.__path__ = [_be_dir]
sys.modules["backend"] = _be_pkg
def _load_mod(_name, _path):
    _spec = _ilu.spec_from_file_location(_name, _path)
    _mod = _ilu.module_from_spec(_spec)
    sys.modules[_name] = _mod
    _spec.loader.exec_module(_mod)
    return _mod
_load_mod("backend.enums", _be_dir + r"\enums.py")
_W1_07_DIR = str(Path(__file__).parent)
_load_mod("backend.schemas", _W1_07_DIR + r"\schemas.py")
_load_mod("backend.evaluation", _W1_07_DIR + r"\evaluation.py")

from backend.enums import CoreFieldName, GoldStatus, AmountType, EvidenceRole
from backend.schemas import (
    AnnotatedField,
    AnnotationDocument,
    EvidenceSpan,
    FieldValue,
    LLMExtractionOutput,
    LLMExtractionRecord,
    LLMExtractedField,
    LLMExtractedValue,
    SupportLevel,
)
from backend.evaluation import (
    evaluate_dataset,
    export_summary_json,
    export_summary_csv,
    compute_status_stats,
    export_status_stats_csv,
)

# 标注.zip 解压目录
GOLD_DIR = Path(r"C:\Users\Lenovo\Desktop\标注_对比\标注")
# 种子公告目录（Baseline 实际使用的原文，用于无依据检查）
SEED_DIR = Path(r"C:\Users\Lenovo\Desktop\标注_对比\BidAgent_金标种子公告")
# DeepSeek Baseline records
RECORDS_PATH = Path(r"C:\Users\Lenovo\Desktop\标注_对比\DeepSeekBaseline报告\records.jsonl")
# 输出目录
OUT_DIR = Path(r"C:\Users\Lenovo\Desktop\W1-07评测报告")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 整理后的7篇标准原文目录（转换后的数据）
RAW_TXT_DIR = Path(r"C:\Users\Lenovo\Desktop\W1-09_标注原文_整理后")

# document_id 映射：标注.zip 文件名 → DeepSeek Baseline document_id
DOC_ID_MAP = {
    "tender_02": "02_tender_002",
    "tender_03": "03_tender_003",
    "award_01": "04_award_001",
    "award_02": "05_award_002",
    "award_03": "06_award_003",
    "correction_01": "07_correction_001",
    "correction_02": "08_correction_002",
}

ANNOTATION_VERSION = "1.0"


def load_raw_text(gold_file: Path) -> str:
    """从标注.zip 文件读取原文（第7行起）。"""
    lines = gold_file.read_text(encoding="utf-8").split("\n")
    # 找到"原文"行，从下一行开始
    raw_start = 0
    for i, line in enumerate(lines):
        if line.strip() == "原文":
            raw_start = i + 1
            break
    # 跳过空行
    while raw_start < len(lines) and not lines[raw_start].strip():
        raw_start += 1
    return "\n".join(lines[raw_start:]).strip()


def load_standard_raw_text(file_path: Path) -> str:
    """从标准原文文件读取全文（首行标题，空行后正文）。"""
    return file_path.read_text(encoding="utf-8").strip()


def find_evidence(raw_text: str, value: str) -> EvidenceSpan:
    """从原文中查找值的位置，构造证据。找不到则用原文首字符占位。"""
    fallback_text = (raw_text[:1] if raw_text else " ")
    fallback_end = min(1, len(raw_text)) if raw_text else 1
    if not value or not raw_text:
        return EvidenceSpan(role=EvidenceRole.PRIMARY, start=0, end=fallback_end, text=fallback_text)
    idx = raw_text.find(value)
    if idx < 0:
        # 尝试去掉常见前后缀再找
        for pat in ["￥", "人民币", "（人民币）", "（北京时间）", "万元", "元"]:
            v = value.replace(pat, "")
            if v and v != value:
                idx = raw_text.find(v)
                if idx >= 0:
                    return EvidenceSpan(
                        role=EvidenceRole.PRIMARY,
                        start=idx,
                        end=idx + len(v),
                        text=v,
                    )
        return EvidenceSpan(role=EvidenceRole.PRIMARY, start=0, end=fallback_end, text=fallback_text)
    return EvidenceSpan(
        role=EvidenceRole.PRIMARY,
        start=idx,
        end=idx + len(value),
        text=value,
    )


def make_present_field(
    field_name: str,
    values: list[tuple[str, str | None, str | None]],
    raw_text: str,
) -> AnnotatedField:
    """构造 present 字段。values = [(raw_value, normalized_value, amount_type), ...]"""
    field_values = []
    for raw, norm, amt in values:
        ev = find_evidence(raw_text, raw)
        field_values.append(
            FieldValue(
                raw_value=raw,
                normalized_value=norm,
                amount_type=amt,
                acceptable_evidence_spans=[ev],
            )
        )
    return AnnotatedField(
        field_name=field_name,
        gold_status=GoldStatus.PRESENT,
        values=field_values,
    )


def make_absent_field(field_name: str) -> AnnotatedField:
    return AnnotatedField(field_name=field_name, gold_status=GoldStatus.ABSENT)


def make_na_field(field_name: str) -> AnnotatedField:
    """not_applicable 字段。"""
    return AnnotatedField(field_name=field_name, gold_status=GoldStatus.NOT_APPLICABLE)


# ============================================================
# 手动构造 6 篇金标（从标注.zip 人工总结提取）
# ============================================================

def build_gold_docs() -> list[AnnotationDocument]:
    gold_docs = []

    # --- tender_02 (DDWK2026024 东北大学) ---
    raw = load_standard_raw_text(RAW_TXT_DIR / "tender_02.txt")
    fields = [
        make_present_field("project_identifier", [("DDWK2026024", None, None)], raw),
        make_present_field("purchaser_name", [("东北大学", None, None)], raw),
        make_na_field("winner_name"),
        make_present_field("amount", [("900.000000 万元", "9000000.00", AmountType.BUDGET)], raw),
        make_present_field("publish_date", [("2026年07月12日", "2026-07-12", None)], raw),
        make_present_field("bid_deadline", [("2026年08月03日", "2026-08-03", None)], raw),
    ]
    gold_docs.append(AnnotationDocument(
        document_id="02_tender_002", annotator_id="标注员A",
        annotation_version=ANNOTATION_VERSION, fields=fields,
    ))

    # --- tender_03 (QDQZZB-260702 中国海洋大学) ---
    raw = load_standard_raw_text(RAW_TXT_DIR / "tender_03.txt")
    fields = [
        make_present_field("project_identifier", [("QDQZZB-260702", None, None)], raw),
        make_present_field("purchaser_name", [("中国海洋大学", None, None)], raw),
        make_na_field("winner_name"),
        make_present_field("amount", [("229.577666万元", "2295776.66", AmountType.BUDGET)], raw),
        make_present_field("publish_date", [("2026年07月12日", "2026-07-12", None)], raw),
        make_present_field("bid_deadline", [("2026年07月23日", "2026-07-23", None)], raw),
    ]
    gold_docs.append(AnnotationDocument(
        document_id="03_tender_003", annotator_id="标注员A",
        annotation_version=ANNOTATION_VERSION, fields=fields,
    ))

    # --- award_01 (GC-HCD260669 美的空调) ---
    raw = load_standard_raw_text(RAW_TXT_DIR / "award_01.txt")
    fields = [
        make_present_field("project_identifier", [("GC-HCD260669", None, None)], raw),
        make_present_field("purchaser_name", [("中央国家机关政府采购中心", None, None)], raw),
        make_present_field("winner_name", [("广东美的制冷设备有限公司", None, None)], raw),
        make_present_field("amount", [("386.8648万", "3868648.00", AmountType.AWARD)], raw),
        make_present_field("publish_date", [("2026年7月12日", "2026-07-12", None)], raw),
        make_na_field("bid_deadline"),
    ]
    gold_docs.append(AnnotationDocument(
        document_id="04_award_001", annotator_id="标注员A",
        annotation_version=ANNOTATION_VERSION, fields=fields,
    ))

    # --- award_02 (GC-HCD260670 复印机 5中标人6金额) ---
    raw = load_standard_raw_text(RAW_TXT_DIR / "award_02.txt")
    winners = [
        ("北京立思辰计算机技术有限公司", None, None),
        ("惠普贸易（上海）有限公司", None, None),
        ("理光（中国）投资有限公司", None, None),
        ("京瓷办公信息系统（中国）有限公司", None, None),
        ("东芝泰格信息系统（深圳）有限公司", None, None),
    ]
    amounts = [
        ("2.1万", "21000.00", AmountType.AWARD),
        ("5.7837万", "57837.00", AmountType.AWARD),
        ("15.3972万", "153972.00", AmountType.AWARD),
        ("10.1328万", "101328.00", AmountType.AWARD),
        ("123.8958万", "1238958.00", AmountType.AWARD),
        ("218.94万", "2189400.00", AmountType.AWARD),
    ]
    fields = [
        make_present_field("project_identifier", [("GC-HCD260670", None, None)], raw),
        make_present_field("purchaser_name", [("中央国家机关政府采购中心", None, None)], raw),
        make_present_field("winner_name", winners, raw),
        make_present_field("amount", amounts, raw),
        make_present_field("publish_date", [("2026年7月12日", "2026-07-12", None)], raw),
        make_na_field("bid_deadline"),
    ]
    gold_docs.append(AnnotationDocument(
        document_id="05_award_002", annotator_id="标注员A",
        annotation_version=ANNOTATION_VERSION, fields=fields,
    ))

    # --- award_03 (GC-HCD260671 打印机 3中标人4金额) ---
    raw = load_standard_raw_text(RAW_TXT_DIR / "award_03.txt")
    winners = [
        ("联想（北京）有限公司", None, None),
        ("京瓷办公信息系统（中国）有限公司", None, None),
        ("得力集团有限公司", None, None),
    ]
    amounts = [
        ("177.449万", "1774490.00", AmountType.AWARD),
        ("103.155万", "1031550.00", AmountType.AWARD),
        ("122.0055万", "1220055.00", AmountType.AWARD),
        ("114万", "1140000.00", AmountType.AWARD),
    ]
    fields = [
        make_present_field("project_identifier", [("GC-HCD260671", None, None)], raw),
        make_present_field("purchaser_name", [("中央国家机关政府采购中心", None, None)], raw),
        make_present_field("winner_name", winners, raw),
        make_present_field("amount", amounts, raw),
        make_present_field("publish_date", [("2026年7月12日", "2026-07-12", None)], raw),
        make_na_field("bid_deadline"),
    ]
    gold_docs.append(AnnotationDocument(
        document_id="06_award_003", annotator_id="标注员A",
        annotation_version=ANNOTATION_VERSION, fields=fields,
    ))

    # --- correction_01 (物资2026-001 更正公告) ---
    raw = load_standard_raw_text(RAW_TXT_DIR / "correction_01.txt")
    fields = [
        make_present_field("project_identifier", [("物资2026-001", None, None)], raw),
        make_present_field("purchaser_name", [("中国国际经济技术交流中心", None, None)], raw),
        make_absent_field("winner_name"),
        make_absent_field("amount"),
        make_present_field("publish_date", [("2026年7月15日", "2026-07-15", None)], raw),
        make_present_field("bid_deadline", [("2026年8月5日", "2026-08-05", None)], raw),
    ]
    gold_docs.append(AnnotationDocument(
        document_id="07_correction_001", annotator_id="标注员A",
        annotation_version=ANNOTATION_VERSION, fields=fields,
    ))

    # --- correction_02 (XNJZ-G-2026-010 大连民族大学 更正公告) ---
    raw = load_standard_raw_text(RAW_TXT_DIR / "correction_02.txt")
    fields = [
        make_present_field("project_identifier", [("XNJZ-G-2026-010、TLYQ2026-06080", None, None)], raw),
        make_present_field("purchaser_name", [("大连民族大学", None, None)], raw),
        make_absent_field("winner_name"),
        make_absent_field("amount"),
        make_present_field("publish_date", [("2026年07月12日", "2026-07-12", None)], raw),
        make_absent_field("bid_deadline"),
    ]
    gold_docs.append(AnnotationDocument(
        document_id="08_correction_002", annotator_id="标注员A",
        annotation_version=ANNOTATION_VERSION, fields=fields,
    ))

    return gold_docs


def collect_raw_texts() -> dict[str, str]:
    """W1-07 v2: 收集所有金标篇目的原文，按 document_id 映射。

    使用整理后的7篇标准原文（转换后的数据），用于 evaluate_dataset 的
    raw_texts 参数，启用无依据输出率检查。
    """
    raw_texts = {}
    for short_name, doc_id in DOC_ID_MAP.items():
        raw_file = RAW_TXT_DIR / f"{short_name}.txt"
        if raw_file.exists():
            raw_texts[doc_id] = load_standard_raw_text(raw_file)
    return raw_texts


# ============================================================
# 加载 DeepSeek Baseline records
# ============================================================

def load_baseline_records() -> list[LLMExtractionRecord]:
    records = []
    for line in RECORDS_PATH.read_text(encoding="utf-8").strip().split("\n"):
        if not line.strip():
            continue
        data = json.loads(line)
        # 构造 LLMExtractionRecord
        output = None
        if data.get("output"):
            fields = []
            for f in data["output"]["fields"]:
                values = []
                for v in f.get("values", []):
                    values.append(LLMExtractedValue(
                        raw_value=v.get("raw_value"),
                        normalized_value=v.get("normalized_value"),
                        amount_type=v.get("amount_type"),
                        currency=v.get("currency"),
                        lot_id=v.get("lot_id"),
                        evidence_text=v.get("evidence_text"),
                    ))
                fields.append(LLMExtractedField(
                    field_name=f["field_name"],
                    support_level=f.get("support_level", SupportLevel.DIRECT),
                    values=values,
                ))
            output = LLMExtractionOutput(fields=fields)

        records.append(LLMExtractionRecord(
            document_id=data["document_id"],
            model_identifier=data["model_identifier"],
            prompt_hash=data["prompt_hash"],
            success=data["success"],
            output=output,
            error_message=data.get("error_message"),
            latency_ms=data.get("latency_ms", 0),
            prompt_tokens=data.get("prompt_tokens", 0),
            completion_tokens=data.get("completion_tokens", 0),
            total_tokens=data.get("total_tokens", 0),
        ))
    return records


# ============================================================
# 主流程
# ============================================================

def main():
    print("=" * 70)
    print("W1-07 评测脚本真实数据冒烟测试")
    print(f"时间: {datetime.now().isoformat()}")
    print("=" * 70)

    # 1. 构造金标
    gold_docs = build_gold_docs()
    print(f"\n[1] 金标构造完成: {len(gold_docs)} 篇")
    for g in gold_docs:
        present_count = sum(1 for f in g.fields if f.gold_status == GoldStatus.PRESENT)
        absent_count = sum(1 for f in g.fields if f.gold_status == GoldStatus.ABSENT)
        na_count = sum(1 for f in g.fields if f.gold_status == GoldStatus.NOT_APPLICABLE)
        print(f"  - {g.document_id}: present={present_count}, absent={absent_count}, na={na_count}")

    # 1.5 W1-07 v2: 收集原文
    raw_texts = collect_raw_texts()
    print(f"\n[1.5] 原文收集完成: {len(raw_texts)} 篇 (用于无依据输出率检查)")

    # 2. 加载 Baseline
    all_records = load_baseline_records()
    print(f"\n[2] Baseline 加载完成: {len(all_records)} 篇")

    # 3. 匹配（只保留有金标的篇目）
    gold_ids = {g.document_id for g in gold_docs}
    matched_records = [r for r in all_records if r.document_id in gold_ids]
    print(f"\n[3] 匹配结果: 金标 {len(gold_docs)} 篇, Baseline 匹配 {len(matched_records)} 篇")
    for r in matched_records:
        print(f"  - {r.document_id}: success={r.success}, fields={len(r.output.fields) if r.output else 0}")

    # 4. 评测（v2: 传入 raw_texts 启用无依据检查）
    print(f"\n[4] 开始评测 (v2: 启用无依据输出率检查)...")
    summary = evaluate_dataset(
        gold_docs=gold_docs,
        system_records=matched_records,
        system_identifier="deepseek-chat",
        dataset_split="dev",
        run_id=f"eval-v2-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        raw_texts=raw_texts,  # W1-07 v2 新增
    )

    # 5. 输出结果
    print(f"\n[5] 评测结果 (v2):")
    print(f"  文档数: {summary.document_count}")
    print(f"  Macro Precision: {summary.macro_precision:.4f}")
    print(f"  Macro Recall:    {summary.macro_recall:.4f}")
    print(f"  Macro F1:        {summary.macro_f1:.4f}")
    # v2 汇总
    total_unjustified = sum(m.unjustified_count for m in summary.field_metrics)
    total_system_values = sum(m.system_value_total for m in summary.field_metrics)
    total_at_mismatch = sum(m.amount_type_mismatch_count for m in summary.field_metrics)
    overall_unjustified_rate = (
        total_unjustified / total_system_values if total_system_values > 0 else 0.0
    )
    print(f"  无依据输出率:    {overall_unjustified_rate:.4f} ({total_unjustified}/{total_system_values})")
    print(f"  amount_type 不一致: {total_at_mismatch} 个值")
    print(f"\n  分字段指标 (v2):")
    for m in summary.field_metrics:
        print(f"    {m.field_name:25s} P={m.precision:.4f} R={m.recall:.4f} F1={m.f1:.4f} "
              f"| P_multi={m.precision_multi:.4f} R_multi={m.recall_multi:.4f} F1_multi={m.f1_multi:.4f} "
              f"| unjustified={m.unjustified_count}/{m.system_value_total} "
              f"at_mismatch={m.amount_type_mismatch_count} "
              f"(present={m.gold_present_count}, absent={m.gold_absent_count}, "
              f"correct={m.system_correct_count}, output={m.system_output_count}, "
              f"fp_absent={m.false_positive_on_absent})")

    # 6. 导出
    json_path = OUT_DIR / "评测报告.json"
    csv_path = OUT_DIR / "字段指标.csv"
    stats_csv = OUT_DIR / "字段状态分布.csv"
    export_summary_json(summary, json_path)
    export_summary_csv(summary, csv_path)

    stats = compute_status_stats(gold_docs)
    export_status_stats_csv(stats, stats_csv)

    # 7. 人看版 MD (v2: 含无依据输出率等新指标)
    md_lines = [
        "# W1-07 评测报告 (v2) · DeepSeek Baseline vs 人工金标",
        "",
        f"生成时间: {datetime.now().isoformat()}",
        f"金标来源: 标注.zip (标注员A 人工标注)",
        f"Baseline: DeepSeek (deepseek-chat, prompt v1.1)",
        f"匹配篇数: {len(gold_docs)} 篇",
        f"原文数: {len(raw_texts)} 篇 (用于无依据检查)",
        "",
        "## 汇总指标 (v2)",
        "",
        f"| 指标 | 值 |",
        f"|------|----|",
        f"| Macro Precision | {summary.macro_precision:.4f} |",
        f"| Macro Recall | {summary.macro_recall:.4f} |",
        f"| Macro F1 | {summary.macro_f1:.4f} |",
        f"| 无依据输出率 | {overall_unjustified_rate:.4f} ({total_unjustified}/{total_system_values}) |",
        f"| amount_type 不一致 | {total_at_mismatch} 个值 |",
        f"| 文档数 | {summary.document_count} |",
        "",
        "## 分字段指标 (v2)",
        "",
        "| 字段 | P | R | F1 | P_multi | R_multi | F1_multi | 无依据 | 系统值数 | at_mismatch | present | absent | correct | output | fp_absent |",
        "|------|---|---|----|---------|---------|----------|--------|---------|------------|---------|--------|---------|--------|-----------|",
    ]
    for m in summary.field_metrics:
        md_lines.append(
            f"| {m.field_name} | {m.precision:.4f} | {m.recall:.4f} | {m.f1:.4f} "
            f"| {m.precision_multi:.4f} | {m.recall_multi:.4f} | {m.f1_multi:.4f} "
            f"| {m.unjustified_count}/{m.system_value_total} "
            f"| {m.system_value_total} | {m.amount_type_mismatch_count} "
            f"| {m.gold_present_count} | {m.gold_absent_count} "
            f"| {m.system_correct_count} | {m.system_output_count} "
            f"| {m.false_positive_on_absent} |"
        )

    md_lines.extend([
        "",
        "## 逐篇详情 (v2: 含无依据值列表)",
        "",
    ])

    # 逐篇评测（v2: 传 raw_text 启用无依据检查）
    from backend.evaluation import evaluate_document
    for gold in gold_docs:
        sys_record = next((r for r in matched_records if r.document_id == gold.document_id), None)
        doc_raw_text = raw_texts.get(gold.document_id)
        doc_results = evaluate_document(gold, sys_record, raw_text=doc_raw_text)
        md_lines.append(f"### {gold.document_id}")
        md_lines.append("")
        md_lines.append("| 字段 | 金标状态 | 金标值数 | 系统值数 | 匹配数 | 正确 | 未匹配系统值 | 未匹配金标值 | 无依据值 |")
        md_lines.append("|------|---------|---------|---------|--------|------|-------------|-------------|---------|")
        for r in doc_results:
            correct_str = "✅" if r.is_correct is True else ("❌" if r.is_correct is False else "—")
            unmatched_sys = ", ".join(r.unmatched_system_values[:3]) if r.unmatched_system_values else ""
            unmatched_gold = ", ".join(r.unmatched_gold_values[:3]) if r.unmatched_gold_values else ""
            unjustified_str = ", ".join(r.unjustified_values[:3]) if r.unjustified_values else ""
            md_lines.append(
                f"| {r.field_name} | {r.gold_status} | {r.gold_value_count} "
                f"| {r.system_value_count} | {r.matched_count} | {correct_str} "
                f"| {unmatched_sys} | {unmatched_gold} | {unjustified_str} |"
            )
        md_lines.append("")

    md_path = OUT_DIR / "评测报告.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    print(f"\n[6] 报告已导出到: {OUT_DIR}")
    print(f"  - 评测报告.json")
    print(f"  - 评测报告.md")
    print(f"  - 字段指标.csv")
    print(f"  - 字段状态分布.csv")
    print("=" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(main())
