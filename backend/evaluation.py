"""BidAgent v4.1 基础评测脚本（W1-07）。

对应《第一周任务清单》Day 4 要求：
- 单值字段 Precision、Recall、F1
- 多值字段集合级 Precision、Recall、F1
- 空值误报率
- 字段状态统计
- 分字段结果
- 汇总结果导出 JSON/CSV
- 测试异常输入和缺失字段

对应 v4.1 §10.3 金标字段状态规则：
- present / absent 进入主评测分母
- not_applicable / ambiguous / attachment_only / unreadable 不计入主分母，单独统计

工程规范：
- 严格区分单值/多值字段
- 集合级匹配用归一化后的值，避免格式差异影响
- 失败/缺失系统输出不静默丢弃，记为 0 输出
- 导出 JSON 含完整派生指标，CSV 仅含字段级指标便于表格查看
"""
from __future__ import annotations

import csv
import json
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from app.utils.logger import get_logger

from backend.enums import (
    AmountType,
    CoreFieldName,
    GoldStatus,
)
from backend.schemas import (
    AnnotatedField,
    AnnotationDocument,
    EvaluationSummary,
    FieldMetrics,
    FieldValue,
    LLMExtractionOutput,
    LLMExtractionRecord,
    LLMExtractedField,
)

logger = get_logger("backend.evaluation")


# ============================================================
# 值归一化与匹配
# ============================================================


# 按长度降序排列，确保 "股份有限公司" 在 "公司" 之前匹配
_LEGAL_SUFFIXES_RAW = (
    "股份有限公司",
    "有限责任公司",
    "有限公司",
    "集团",
    "股份",
    "公司",
    "中心",
    "局",
    "院",
    "厂",
    "站",
    "队",
)
_LEGAL_SUFFIXES = tuple(sorted(_LEGAL_SUFFIXES_RAW, key=len, reverse=True))


def normalize_value(field_name: str, raw: str) -> str:
    """按字段类型归一化值，用于集合级匹配。

    - project_identifier: 去空白、英文大写、统一全半角（NFKC）
    - purchaser_name / winner_name: 去法律后缀 + 去空白 + 大小写不敏感
    - amount: 转 Decimal 字符串（保留 2 位）
    - publish_date / bid_deadline: 提取 YYYY-MM-DD
    - 其他: 去首尾空白
    """
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s:
        return ""

    if field_name == CoreFieldName.PROJECT_IDENTIFIER:
        # NFKC：全角字母/数字/标点统一为半角
        s = unicodedata.normalize("NFKC", s)
        return s.upper()

    if field_name in (CoreFieldName.PURCHASER_NAME, CoreFieldName.WINNER_NAME):
        s = s.lower()
        # 反复去除法律后缀（已按长度降序，避免"某集团股份有限公司"先剥"公司"残留"股份"）
        changed = True
        while changed:
            changed = False
            for suffix in _LEGAL_SUFFIXES:
                if s.endswith(suffix) and len(s) > len(suffix):
                    s = s[: -len(suffix)].rstrip()
                    changed = True
                    break
        return s

    if field_name == CoreFieldName.AMOUNT:
        # 提取数字部分
        m = re.search(r"[\d,]+\.?\d*", s.replace(",", ""))
        if not m:
            return s
        try:
            d = Decimal(m.group(0))
            return f"{d:.2f}"
        except (InvalidOperation, ValueError):
            return s

    if field_name in (CoreFieldName.PUBLISH_DATE, CoreFieldName.BID_DEADLINE):
        # 提取 YYYY-MM-DD
        m = re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", s)
        if m:
            return m.group(0).replace("/", "-")
        return s

    return s


def values_match(field_name: str, gold_norm: str, system_norm: str) -> bool:
    """两个归一化后的值是否匹配。"""
    if not gold_norm or not system_norm:
        return False
    return gold_norm == system_norm


# ============================================================
# 单文档评测
# ============================================================


@dataclass
class DocumentFieldResult:
    """单文档单字段的评测结果。"""
    document_id: str
    field_name: str
    gold_status: str
    gold_value_count: int
    system_value_count: int
    matched_count: int  # 与金标匹配的值数量
    system_output_count: int  # 系统输出值数量
    false_positive_on_absent: int  # 金标 absent 但系统有输出
    is_correct: bool  # 单值字段：是否完全匹配；多值字段：是否有至少一个匹配
    unmatched_system_values: list[str] = field(default_factory=list)
    unmatched_gold_values: list[str] = field(default_factory=list)


def _field_value_set(field: AnnotatedField) -> set[str]:
    """提取金标字段的归一化值集合。"""
    return {
        normalize_value(field.field_name, v.normalized_value or v.raw_value)
        for v in field.values
        if (v.normalized_value or v.raw_value)
    }


def _system_value_set(field: LLMExtractedField) -> set[str]:
    """提取系统字段的归一化值集合。"""
    return {
        normalize_value(field.field_name, v.normalized_value or v.raw_value)
        for v in field.values
        if (v.normalized_value or v.raw_value)
    }


def evaluate_document(
    gold: AnnotationDocument,
    system: LLMExtractionRecord | LLMExtractionOutput | None,
) -> list[DocumentFieldResult]:
    """评测单文档，返回六个字段的结果列表。

    系统输出为 None 或失败记录时，所有字段按"系统未输出"处理。
    """
    document_id = gold.document_id

    # 提取系统输出
    if system is None:
        system_output: LLMExtractionOutput | None = None
    elif isinstance(system, LLMExtractionRecord):
        system_output = system.output if system.success else None
    else:
        system_output = system

    # 系统字段映射
    system_fields_map: dict[str, LLMExtractedField] = {}
    if system_output is not None:
        for sf in system_output.fields:
            system_fields_map[sf.field_name] = sf

    results: list[DocumentFieldResult] = []
    for gold_field in gold.fields:
        fname = gold_field.field_name
        gold_set = _field_value_set(gold_field)
        system_field = system_fields_map.get(fname)
        system_set = _system_value_set(system_field) if system_field else set()

        matched = gold_set & system_set
        unmatched_system = system_set - gold_set
        unmatched_gold = gold_set - system_set

        # 单值字段正确性：金标 present 且系统匹配金标
        if gold_field.gold_status == GoldStatus.PRESENT:
            is_correct = len(matched) > 0
        elif gold_field.gold_status == GoldStatus.ABSENT:
            # 金标 absent，系统也不应有输出
            is_correct = len(system_set) == 0
        else:
            # not_applicable / ambiguous / attachment_only / unreadable
            # 不计入主评测分母，但仍记录
            is_correct = False

        # 空值误报：金标 absent 但系统有输出
        fp_on_absent = 1 if (
            gold_field.gold_status == GoldStatus.ABSENT and len(system_set) > 0
        ) else 0

        results.append(
            DocumentFieldResult(
                document_id=document_id,
                field_name=fname,
                gold_status=gold_field.gold_status,
                gold_value_count=len(gold_set),
                system_value_count=len(system_set),
                matched_count=len(matched),
                system_output_count=len(system_set),
                false_positive_on_absent=fp_on_absent,
                is_correct=is_correct,
                unmatched_system_values=sorted(unmatched_system),
                unmatched_gold_values=sorted(unmatched_gold),
            )
        )

    return results


# ============================================================
# 数据集评测
# ============================================================


def evaluate_dataset(
    gold_docs: list[AnnotationDocument],
    system_records: list[LLMExtractionRecord],
    system_identifier: str,
    dataset_split: str = "test",
    run_id: str | None = None,
) -> EvaluationSummary:
    """评测整个数据集，返回 EvaluationSummary。

    要求 gold_docs 与 system_records 的 document_id 一一对应。
    缺失系统记录按失败处理。
    """
    if not gold_docs:
        raise ValueError("gold_docs 不能为空")

    logger.info(
        "evaluate_dataset start gold_count={} system_count={} system_id={} split={}",
        len(gold_docs),
        len(system_records),
        system_identifier,
        dataset_split,
    )

    # 按 document_id 索引系统记录
    sys_map: dict[str, LLMExtractionRecord] = {}
    for r in system_records:
        sys_map[r.document_id] = r

    # 收集所有文档的字段结果
    all_results: list[DocumentFieldResult] = []
    matched_doc_count = 0
    for gold in gold_docs:
        sys_record = sys_map.get(gold.document_id)
        if sys_record is not None:
            matched_doc_count += 1
        results = evaluate_document(gold, sys_record)
        all_results.extend(results)

    # 按字段聚合
    per_field: dict[str, FieldMetrics] = {
        name: FieldMetrics(field_name=name)
        for name in CoreFieldName.ALL
    }

    for r in all_results:
        m = per_field[r.field_name]
        if r.gold_status == GoldStatus.PRESENT:
            m.gold_present_count += 1
        elif r.gold_status == GoldStatus.ABSENT:
            m.gold_absent_count += 1
        else:
            m.gold_other_count += 1

        if r.system_output_count > 0:
            m.system_output_count += 1
        if r.is_correct and r.gold_status in (GoldStatus.PRESENT,):
            m.system_correct_count += 1
        m.false_positive_on_absent += r.false_positive_on_absent

    # run_id 默认用时间戳
    if run_id is None:
        from datetime import datetime
        run_id = f"run-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    summary = EvaluationSummary(
        run_id=run_id,
        system_identifier=system_identifier,
        dataset_split=dataset_split,
        document_count=len(gold_docs),
        field_metrics=list(per_field.values()),
    )

    logger.info(
        "evaluate_dataset done run_id={} docs={} matched={} macro_p={:.4f} macro_r={:.4f} macro_f1={:.4f}",
        run_id,
        len(gold_docs),
        matched_doc_count,
        summary.macro_precision,
        summary.macro_recall,
        summary.macro_f1,
    )
    return summary


# ============================================================
# 字段状态统计
# ============================================================


@dataclass
class FieldStatusStats:
    """单字段的状态分布统计。"""
    field_name: str
    status_counts: dict[str, int] = field(default_factory=dict)

    def total(self) -> int:
        return sum(self.status_counts.values())


def compute_status_stats(
    gold_docs: list[AnnotationDocument],
) -> list[FieldStatusStats]:
    """统计金标数据集中每个字段的状态分布。

    用于报告数据集质量：
    - present 比例
    - absent 比例
    - not_applicable / ambiguous / attachment_only / unreadable 比例
    """
    per_field: dict[str, FieldStatusStats] = {
        name: FieldStatusStats(field_name=name)
        for name in CoreFieldName.ALL
    }
    for doc in gold_docs:
        for f in doc.fields:
            stats = per_field[f.field_name]
            stats.status_counts[f.gold_status] = (
                stats.status_counts.get(f.gold_status, 0) + 1
            )
    return list(per_field.values())


# ============================================================
# 导出
# ============================================================


def export_summary_json(summary: EvaluationSummary, path: str | Path) -> Path:
    """导出评测汇总为 JSON 文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = summary.to_dict()
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def export_summary_csv(summary: EvaluationSummary, path: str | Path) -> Path:
    """导出字段级评测指标为 CSV 文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    headers = [
        "field_name",
        "gold_present_count",
        "gold_absent_count",
        "gold_other_count",
        "system_correct_count",
        "system_output_count",
        "false_positive_on_absent",
        "precision",
        "recall",
        "f1",
        "false_omission_rate_on_absent",
    ]

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for m in summary.field_metrics:
            d = m.to_dict()
            writer.writerow([d[h] for h in headers])
    return path


def export_status_stats_csv(
    stats: list[FieldStatusStats],
    path: str | Path,
) -> Path:
    """导出字段状态分布为 CSV。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # 收集所有出现过的状态
    all_statuses: list[str] = list(GoldStatus.ALL)
    headers = ["field_name", "total"] + all_statuses

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for s in stats:
            row = [s.field_name, s.total()]
            for status in all_statuses:
                row.append(s.status_counts.get(status, 0))
            writer.writerow(row)
    return path


# ============================================================
# 异常输入处理
# ============================================================


def safe_evaluate_dataset(
    gold_docs: Iterable[AnnotationDocument],
    system_records: Iterable[LLMExtractionRecord],
    system_identifier: str,
    dataset_split: str = "test",
    run_id: str | None = None,
) -> EvaluationSummary:
    """安全版评测 - 跳过无效输入而非抛异常。

    用于生产环境批量评测，避免单条数据问题导致整体失败。
    """
    gold_list: list[AnnotationDocument] = []
    for g in gold_docs:
        try:
            # 验证文档完整性
            if not g.document_id or not g.fields:
                continue
            gold_list.append(g)
        except Exception:
            continue

    sys_list: list[LLMExtractionRecord] = []
    for r in system_records:
        try:
            if not r.document_id:
                continue
            sys_list.append(r)
        except Exception:
            continue

    if not gold_list:
        # 返回空 summary 而非抛异常
        from datetime import datetime
        return EvaluationSummary(
            run_id=run_id or f"empty-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            system_identifier=system_identifier,
            dataset_split=dataset_split,
            document_count=0,
            field_metrics=[FieldMetrics(field_name=n) for n in CoreFieldName.ALL],
        )

    return evaluate_dataset(
        gold_docs=gold_list,
        system_records=sys_list,
        system_identifier=system_identifier,
        dataset_split=dataset_split,
        run_id=run_id,
    )


__all__ = [
    "DocumentFieldResult",
    "FieldStatusStats",
    "compute_status_stats",
    "evaluate_dataset",
    "evaluate_document",
    "export_status_stats_csv",
    "export_summary_csv",
    "export_summary_json",
    "normalize_value",
    "safe_evaluate_dataset",
    "values_match",
]
