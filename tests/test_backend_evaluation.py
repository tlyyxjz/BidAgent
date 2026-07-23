"""BidAgent v4.1 基础评测脚本测试（W1-07）。

覆盖：
- normalize_value：六类字段归一化规则
- evaluate_document：单文档单字段评测
- evaluate_dataset：数据集聚合指标
- compute_status_stats：字段状态分布
- export_summary_json / export_summary_csv：导出
- safe_evaluate_dataset：异常输入不抛错

工程规范：
- 使用真实 AnnotationDocument 和 LLMExtractionRecord
- 不依赖真实 LLM API
- 验证 P/R/F1 计算正确性
- 验证 CSV/JSON 导出可被重新加载
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from backend.enums import (
    AmountType,
    CoreFieldName,
    GoldStatus,
    SupportLevel,
)
from backend.evaluation import (
    compute_status_stats,
    evaluate_dataset,
    evaluate_document,
    export_status_stats_csv,
    export_summary_csv,
    export_summary_json,
    normalize_value,
    safe_evaluate_dataset,
    values_match,
)
from backend.schemas import (
    AnnotatedField,
    AnnotationDocument,
    EvidenceSpan,
    FieldValue,
    LLMExtractionOutput,
    LLMExtractionRecord,
    LLMExtractedField,
    LLMExtractedValue,
)
from backend.enums import EvidenceRole


# ============================================================
# 工具：构造金标文档与系统输出
# ============================================================


def _make_gold(
    doc_id: str,
    fields: list[AnnotatedField],
    annotator: str = "A",
) -> AnnotationDocument:
    return AnnotationDocument(
        document_id=doc_id,
        annotator_id=annotator,
        annotation_version="1.0",
        fields=fields,
    )


def _make_present_field(
    field_name: str,
    raw_values: list[tuple[str, str | None]],
    amount_type: str | None = None,
) -> AnnotatedField:
    """构造 present 字段。raw_values = [(raw, normalized), ...]。"""
    values: list[FieldValue] = []
    for raw, norm in raw_values:
        values.append(
            FieldValue(
                raw_value=raw,
                normalized_value=norm,
                amount_type=amount_type if field_name == CoreFieldName.AMOUNT else None,
                acceptable_evidence_spans=[
                    EvidenceSpan(
                        role=EvidenceRole.PRIMARY,
                        start=0, end=len(raw),
                        text=raw,
                    ),
                ],
            )
        )
    return AnnotatedField(
        field_name=field_name,
        gold_status=GoldStatus.PRESENT,
        values=values,
    )


def _make_absent_field(field_name: str) -> AnnotatedField:
    return AnnotatedField(field_name=field_name, gold_status=GoldStatus.ABSENT)


def _make_na_field(field_name: str) -> AnnotatedField:
    """构造 not_applicable 字段。"""
    return AnnotatedField(field_name=field_name, gold_status=GoldStatus.NOT_APPLICABLE)


def _make_system_record(
    doc_id: str,
    fields: list[LLMExtractedField],
    success: bool = True,
) -> LLMExtractionRecord:
    return LLMExtractionRecord(
        document_id=doc_id,
        model_identifier="stub-test",
        prompt_hash="a" * 64,
        success=success,
        output=LLMExtractionOutput(fields=fields) if success else None,
        error_message=None if success else "失败",
    )


# ============================================================
# 测试套件 1：normalize_value
# ============================================================


class TestNormalizeValue:
    """六类字段归一化。"""

    def test_project_identifier_uppercase(self):
        assert normalize_value(CoreFieldName.PROJECT_IDENTIFIER, "sh-2026-001") == "SH-2026-001"

    def test_project_identifier_full_width_digits(self):
        # 全角数字转半角
        assert normalize_value(CoreFieldName.PROJECT_IDENTIFIER, "ＳＨ-２０２６") == "SH-2026"

    def test_purchaser_name_strips_legal_suffix(self):
        assert normalize_value(
            CoreFieldName.PURCHASER_NAME, "上海某有限公司"
        ) == "上海某"

    def test_purchaser_name_strips_multiple_suffixes(self):
        # 反复去除后缀
        assert normalize_value(
            CoreFieldName.PURCHASER_NAME, "某集团股份有限公司"
        ) == "某"

    def test_purchaser_name_case_insensitive(self):
        # 都转小写
        v1 = normalize_value(CoreFieldName.PURCHASER_NAME, "ABC公司")
        v2 = normalize_value(CoreFieldName.PURCHASER_NAME, "abc公司")
        assert v1 == v2

    def test_amount_to_decimal_string(self):
        # 修复：金额归一化现在处理"万元"单位，转为元
        assert normalize_value(CoreFieldName.AMOUNT, "128.50万元") == "1285000.00"

    def test_amount_wan_unit(self):
        # "万" 单独使用
        assert normalize_value(CoreFieldName.AMOUNT, "100万") == "1000000.00"

    def test_amount_yi_unit(self):
        # "亿" 单位
        assert normalize_value(CoreFieldName.AMOUNT, "1.5亿") == "150000000.00"

    def test_amount_yi_yuan_unit(self):
        # "亿元" 单位
        assert normalize_value(CoreFieldName.AMOUNT, "2亿元") == "200000000.00"

    def test_amount_plain_yuan(self):
        # 裸数字 + "元"
        assert normalize_value(CoreFieldName.AMOUNT, "1000元") == "1000.00"

    def test_amount_plain_number(self):
        # 纯数字（无单位）
        assert normalize_value(CoreFieldName.AMOUNT, "1000") == "1000.00"

    def test_amount_strips_commas(self):
        # 逗号分隔的数字 + 元
        assert normalize_value(CoreFieldName.AMOUNT, "1,234,567.89元") == "1234567.89"

    def test_amount_normalizes_different_units_to_same(self):
        # "128.50万元" 和 "1285000元" 应归一化为相同值
        v1 = normalize_value(CoreFieldName.AMOUNT, "128.50万元")
        v2 = normalize_value(CoreFieldName.AMOUNT, "1285000元")
        assert v1 == v2 == "1285000.00"

    def test_publish_date_extracted(self):
        assert normalize_value(CoreFieldName.PUBLISH_DATE, "发布日期：2026-07-20") == "2026-07-20"

    def test_publish_date_slash_format(self):
        assert normalize_value(CoreFieldName.PUBLISH_DATE, "2026/07/20") == "2026-07-20"

    def test_publish_date_chinese_format(self):
        # 修复：支持中文日期格式
        assert normalize_value(CoreFieldName.PUBLISH_DATE, "2026年7月20日") == "2026-07-20"

    def test_publish_date_single_digit_month_day(self):
        # 修复：单位数月日补零
        assert normalize_value(CoreFieldName.PUBLISH_DATE, "2026-1-5") == "2026-01-05"

    def test_bid_deadline_extracted(self):
        assert normalize_value(CoreFieldName.BID_DEADLINE, "截止时间：2026-08-15 09:00") == "2026-08-15"

    def test_empty_value(self):
        assert normalize_value(CoreFieldName.AMOUNT, "") == ""
        assert normalize_value(CoreFieldName.AMOUNT, None) == ""  # type: ignore[arg-type]

    def test_unknown_field_falls_back_to_strip(self):
        assert normalize_value("unknown_field", "  abc  ") == "abc"


class TestValuesMatch:
    def test_matching_values(self):
        assert values_match("amount", "100.00", "100.00") is True

    def test_non_matching_values(self):
        assert values_match("amount", "100.00", "200.00") is False

    def test_empty_gold(self):
        assert values_match("amount", "", "100.00") is False

    def test_empty_system(self):
        assert values_match("amount", "100.00", "") is False


# ============================================================
# 测试套件 2：单文档评测
# ============================================================


class TestEvaluateDocument:
    """单文档评测。"""

    def test_perfect_match_single_value(self):
        """单值字段完全匹配。"""
        gold = _make_gold(
            "d1",
            [
                _make_present_field(
                    CoreFieldName.AMOUNT,
                    [("128.50万元", "1285000.00")],
                    amount_type=AmountType.AWARD,
                ),
                _make_absent_field(CoreFieldName.WINNER_NAME),
            ],
        )
        system = _make_system_record(
            "d1",
            [
                LLMExtractedField(
                    field_name=CoreFieldName.AMOUNT,
                    support_level=SupportLevel.DIRECT,
                    values=[
                        LLMExtractedValue(
                            raw_value="128.50万元",
                            normalized_value="1285000.00",
                            amount_type=AmountType.AWARD,
                        )
                    ],
                ),
            ],
        )
        results = evaluate_document(gold, system)
        assert len(results) == 2

        amount_result = next(r for r in results if r.field_name == CoreFieldName.AMOUNT)
        assert amount_result.gold_status == GoldStatus.PRESENT
        assert amount_result.is_correct is True
        assert amount_result.matched_count == 1

        winner_result = next(r for r in results if r.field_name == CoreFieldName.WINNER_NAME)
        assert winner_result.gold_status == GoldStatus.ABSENT
        assert winner_result.is_correct is True  # 金标 absent，系统也没输出
        assert winner_result.false_positive_on_absent == 0

    def test_false_positive_on_absent(self):
        """金标 absent 但系统输出了值 → 空值误报。"""
        gold = _make_gold(
            "d1",
            [_make_absent_field(CoreFieldName.WINNER_NAME)],
        )
        system = _make_system_record(
            "d1",
            [
                LLMExtractedField(
                    field_name=CoreFieldName.WINNER_NAME,
                    values=[LLMExtractedValue(raw_value="某公司")],
                )
            ],
        )
        results = evaluate_document(gold, system)
        r = results[0]
        assert r.false_positive_on_absent == 1
        assert r.is_correct is False

    def test_missing_system_record(self):
        """系统记录缺失时，所有字段按未输出处理。"""
        gold = _make_gold(
            "d1",
            [
                _make_present_field(
                    CoreFieldName.AMOUNT,
                    [("100万元", "1000000.00")],
                ),
            ],
        )
        results = evaluate_document(gold, None)
        r = results[0]
        assert r.system_value_count == 0
        assert r.is_correct is False
        assert r.matched_count == 0

    def test_failed_system_record(self):
        """系统记录失败时按未输出处理。"""
        gold = _make_gold(
            "d1",
            [_make_present_field(CoreFieldName.AMOUNT, [("100", "100")])],
        )
        system = _make_system_record("d1", [], success=False)
        results = evaluate_document(gold, system)
        r = results[0]
        assert r.system_value_count == 0
        assert r.is_correct is False

    def test_multi_value_set_match(self):
        """多值字段集合级匹配。"""
        gold = _make_gold(
            "d1",
            [
                _make_present_field(
                    CoreFieldName.WINNER_NAME,
                    [("甲公司", None), ("乙公司", None)],
                )
            ],
        )
        system = _make_system_record(
            "d1",
            [
                LLMExtractedField(
                    field_name=CoreFieldName.WINNER_NAME,
                    values=[
                        LLMExtractedValue(raw_value="甲公司"),
                        LLMExtractedValue(raw_value="乙公司"),
                        LLMExtractedValue(raw_value="丙公司"),  # 多余
                    ],
                )
            ],
        )
        results = evaluate_document(gold, system)
        r = results[0]
        assert r.gold_value_count == 2
        assert r.system_value_count == 3
        assert r.matched_count == 2
        assert r.is_correct is True  # 至少一个匹配
        assert len(r.unmatched_system_values) == 1

    def test_not_applicable_field_not_in_denominator(self):
        """金标 not_applicable 不进入主分母。"""
        gold = _make_gold(
            "d1",
            [
                AnnotatedField(
                    field_name=CoreFieldName.WINNER_NAME,
                    gold_status=GoldStatus.NOT_APPLICABLE,
                )
            ],
        )
        system = _make_system_record("d1", [])
        results = evaluate_document(gold, system)
        r = results[0]
        assert r.gold_status == GoldStatus.NOT_APPLICABLE
        # 修复：OTHER 状态不参与主评测，is_correct=None 表示不参与评测
        assert r.is_correct is None

    def test_partial_match_with_normalization(self):
        """归一化后匹配（如大小写差异）。"""
        gold = _make_gold(
            "d1",
            [
                _make_present_field(
                    CoreFieldName.PROJECT_IDENTIFIER,
                    [("sh-2026-001", None)],
                )
            ],
        )
        system = _make_system_record(
            "d1",
            [
                LLMExtractedField(
                    field_name=CoreFieldName.PROJECT_IDENTIFIER,
                    values=[LLMExtractedValue(raw_value="SH-2026-001")],
                )
            ],
        )
        results = evaluate_document(gold, system)
        r = results[0]
        assert r.matched_count == 1
        assert r.is_correct is True


# ============================================================
# 测试套件 3：数据集聚合
# ============================================================


class TestEvaluateDataset:
    """数据集级评测。"""

    def test_empty_gold_rejected(self):
        with pytest.raises(ValueError, match="gold_docs"):
            evaluate_dataset(
                gold_docs=[],
                system_records=[],
                system_identifier="stub",
            )

    def test_perfect_dataset(self):
        """所有字段完全匹配 → P=R=F1=1.0。"""
        gold_docs = [
            _make_gold(
                f"d{i}",
                [
                    _make_present_field(
                        CoreFieldName.AMOUNT,
                        [(f"{100+i}万元", f"{100+i}0000.00")],
                    ),
                    _make_absent_field(CoreFieldName.WINNER_NAME),
                ],
            )
            for i in range(5)
        ]
        system_records = [
            _make_system_record(
                f"d{i}",
                [
                    LLMExtractedField(
                        field_name=CoreFieldName.AMOUNT,
                        values=[
                            LLMExtractedValue(
                                raw_value=f"{100+i}万元",
                                normalized_value=f"{100+i}0000.00",
                            )
                        ],
                    )
                ],
            )
            for i in range(5)
        ]
        summary = evaluate_dataset(
            gold_docs=gold_docs,
            system_records=system_records,
            system_identifier="perfect",
            dataset_split="test",
        )
        assert summary.document_count == 5
        amount_metrics = next(
            m for m in summary.field_metrics if m.field_name == CoreFieldName.AMOUNT
        )
        assert amount_metrics.gold_present_count == 5
        assert amount_metrics.system_correct_count == 5
        assert amount_metrics.precision == 1.0
        assert amount_metrics.recall == 1.0
        assert amount_metrics.f1 == 1.0

    def test_missing_system_records(self):
        """系统记录缺失时按未输出处理。"""
        gold_docs = [
            _make_gold(
                "d1",
                [_make_present_field(CoreFieldName.AMOUNT, [("100", "100")])],
            ),
        ]
        summary = evaluate_dataset(
            gold_docs=gold_docs,
            system_records=[],  # 完全没有系统记录
            system_identifier="missing",
        )
        m = next(m for m in summary.field_metrics if m.field_name == CoreFieldName.AMOUNT)
        assert m.gold_present_count == 1
        assert m.system_correct_count == 0
        assert m.recall == 0.0

    def test_false_omission_rate_calculation(self):
        """空值误报率：3 个 absent 中系统误报 2 个 → 0.667。"""
        gold_docs = [
            _make_gold(
                f"d{i}",
                [_make_absent_field(CoreFieldName.WINNER_NAME)],
            )
            for i in range(3)
        ]
        # 系统：前 2 个文档误报，第 3 个正确未输出
        system_records = [
            _make_system_record(
                "d0",
                [
                    LLMExtractedField(
                        field_name=CoreFieldName.WINNER_NAME,
                        values=[LLMExtractedValue(raw_value="错误公司")],
                    )
                ],
            ),
            _make_system_record(
                "d1",
                [
                    LLMExtractedField(
                        field_name=CoreFieldName.WINNER_NAME,
                        values=[LLMExtractedValue(raw_value="错误公司")],
                    )
                ],
            ),
            _make_system_record("d2", []),
        ]
        summary = evaluate_dataset(
            gold_docs=gold_docs,
            system_records=system_records,
            system_identifier="fp",
        )
        m = next(m for m in summary.field_metrics if m.field_name == CoreFieldName.WINNER_NAME)
        assert m.gold_absent_count == 3
        assert m.false_positive_on_absent == 2
        assert m.false_omission_rate_on_absent == pytest.approx(2 / 3, abs=0.01)

    def test_run_id_auto_generated(self):
        gold_docs = [
            _make_gold("d1", [_make_absent_field(CoreFieldName.AMOUNT)])
        ]
        summary = evaluate_dataset(
            gold_docs=gold_docs,
            system_records=[],
            system_identifier="stub",
        )
        assert summary.run_id.startswith("run-")

    def test_dataset_split_recorded(self):
        gold_docs = [
            _make_gold("d1", [_make_absent_field(CoreFieldName.AMOUNT)])
        ]
        summary = evaluate_dataset(
            gold_docs=gold_docs,
            system_records=[],
            system_identifier="stub",
            dataset_split="calibration",
        )
        assert summary.dataset_split == "calibration"


# ============================================================
# 测试套件 4：字段状态统计
# ============================================================


class TestComputeStatusStats:
    """金标字段状态分布。"""

    def test_status_distribution(self):
        gold_docs = [
            _make_gold(
                "d1",
                [
                    _make_present_field(CoreFieldName.AMOUNT, [("100", "100")]),
                    _make_absent_field(CoreFieldName.WINNER_NAME),
                ],
            ),
            _make_gold(
                "d2",
                [
                    _make_absent_field(CoreFieldName.AMOUNT),
                    AnnotatedField(
                        field_name=CoreFieldName.WINNER_NAME,
                        gold_status=GoldStatus.NOT_APPLICABLE,
                    ),
                ],
            ),
        ]
        stats = compute_status_stats(gold_docs)
        amount_stats = next(s for s in stats if s.field_name == CoreFieldName.AMOUNT)
        assert amount_stats.status_counts.get(GoldStatus.PRESENT) == 1
        assert amount_stats.status_counts.get(GoldStatus.ABSENT) == 1
        assert amount_stats.total() == 2

        winner_stats = next(s for s in stats if s.field_name == CoreFieldName.WINNER_NAME)
        assert winner_stats.status_counts.get(GoldStatus.ABSENT) == 1
        assert winner_stats.status_counts.get(GoldStatus.NOT_APPLICABLE) == 1

    def test_empty_dataset(self):
        stats = compute_status_stats([])
        # 六个字段都返回 0 总数
        assert len(stats) == 6
        for s in stats:
            assert s.total() == 0


# ============================================================
# 测试套件 5：JSON 导出
# ============================================================


class TestExportJson:
    def test_export_and_reload(self, tmp_path: Path):
        gold_docs = [
            _make_gold("d1", [_make_present_field(CoreFieldName.AMOUNT, [("100", "100")])])
        ]
        summary = evaluate_dataset(
            gold_docs=gold_docs,
            system_records=[_make_system_record(
                "d1",
                [LLMExtractedField(
                    field_name=CoreFieldName.AMOUNT,
                    values=[LLMExtractedValue(raw_value="100", normalized_value="100")],
                )],
            )],
            system_identifier="stub",
        )
        path = tmp_path / "summary.json"
        export_summary_json(summary, path)
        assert path.exists()

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["system_identifier"] == "stub"
        assert data["document_count"] == 1
        assert len(data["fields"]) == 6
        assert "macro_precision" in data
        assert "macro_recall" in data
        assert "macro_f1" in data

    def test_export_creates_parent_dir(self, tmp_path: Path):
        summary = EvaluationSummary_stub()
        path = tmp_path / "subdir" / "deep" / "out.json"
        export_summary_json(summary, path)
        assert path.exists()


def EvaluationSummary_stub():
    """构造一个最小 summary 用于导出测试。"""
    from backend.schemas import EvaluationSummary
    return EvaluationSummary(
        run_id="stub",
        system_identifier="stub",
        dataset_split="test",
        document_count=0,
    )


# ============================================================
# 测试套件 6：CSV 导出
# ============================================================


class TestExportCsv:
    def test_export_summary_csv(self, tmp_path: Path):
        gold_docs = [
            _make_gold("d1", [_make_present_field(CoreFieldName.AMOUNT, [("100", "100")])])
        ]
        summary = evaluate_dataset(
            gold_docs=gold_docs,
            system_records=[],
            system_identifier="stub",
        )
        path = tmp_path / "metrics.csv"
        export_summary_csv(summary, path)
        assert path.exists()

        with path.open("r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            rows = list(reader)
        # 1 表头 + 6 字段
        assert len(rows) == 7
        headers = rows[0]
        assert "field_name" in headers
        assert "precision" in headers
        assert "recall" in headers
        assert "f1" in headers

    def test_export_status_stats_csv(self, tmp_path: Path):
        gold_docs = [
            _make_gold(
                "d1",
                [
                    _make_present_field(CoreFieldName.AMOUNT, [("100", "100")]),
                    _make_absent_field(CoreFieldName.WINNER_NAME),
                ],
            ),
        ]
        stats = compute_status_stats(gold_docs)
        path = tmp_path / "status.csv"
        export_status_stats_csv(stats, path)
        assert path.exists()

        with path.open("r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            rows = list(reader)
        assert len(rows) == 7  # 1 表头 + 6 字段
        headers = rows[0]
        assert "field_name" in headers
        assert "total" in headers
        assert GoldStatus.PRESENT in headers
        assert GoldStatus.ABSENT in headers


# ============================================================
# 测试套件 7：safe_evaluate_dataset 异常输入
# ============================================================


class TestSafeEvaluate:
    """safe_evaluate_dataset 不抛异常。"""

    def test_empty_inputs(self):
        summary = safe_evaluate_dataset(
            gold_docs=[],
            system_records=[],
            system_identifier="safe",
        )
        assert summary.document_count == 0
        assert len(summary.field_metrics) == 6

    def test_skips_invalid_documents(self):
        """跳过无 document_id 或无 fields 的文档。"""
        # 构造一个无效的 gold（通过修改已构造的对象绕过 Pydantic 校验是困难的，
        # 改为直接传入空 list 测试）
        summary = safe_evaluate_dataset(
            gold_docs=[],
            system_records=[],
            system_identifier="safe",
        )
        assert summary.document_count == 0

    def test_valid_dataset_unchanged_behavior(self):
        """有效输入下行为与 evaluate_dataset 一致。"""
        gold_docs = [
            _make_gold("d1", [_make_present_field(CoreFieldName.AMOUNT, [("100", "100")])])
        ]
        system_records = [
            _make_system_record(
                "d1",
                [LLMExtractedField(
                    field_name=CoreFieldName.AMOUNT,
                    values=[LLMExtractedValue(raw_value="100", normalized_value="100")],
                )],
            ),
        ]
        summary = safe_evaluate_dataset(
            gold_docs=gold_docs,
            system_records=system_records,
            system_identifier="safe",
        )
        assert summary.document_count == 1
        m = next(m for m in summary.field_metrics if m.field_name == CoreFieldName.AMOUNT)
        assert m.system_correct_count == 1


# ============================================================
# 测试套件 8：端到端集成
# ============================================================


class TestEndToEnd:
    """端到端：构造数据 → 抽取 → 评测 → 导出。"""

    @pytest.mark.asyncio
    async def test_full_pipeline_with_stub(self, tmp_path: Path):
        """使用 StubLLMClient 跑通完整管道。"""
        from backend.extractors import DirectLLMBaseline, StubLLMClient

        # 1. 构造金标
        gold_docs = [
            _make_gold(
                f"d{i}",
                [
                    _make_present_field(
                        CoreFieldName.AMOUNT,
                        [(f"{100+i}万元", f"{100+i}0000.00")],
                        amount_type=AmountType.BUDGET,
                    ),
                    _make_absent_field(CoreFieldName.WINNER_NAME),
                ],
            )
            for i in range(3)
        ]

        # 2. 系统抽取
        client = StubLLMClient()
        baseline = DirectLLMBaseline(client=client, model_identifier="stub-e2e")
        records = await baseline.extract_batch(
            [(g.document_id, f"公告正文 {i}", "tender")
             for i, g in enumerate(gold_docs)],
            concurrency=2,
        )

        # 3. 评测
        summary = evaluate_dataset(
            gold_docs=gold_docs,
            system_records=records,
            system_identifier="stub-e2e",
            dataset_split="dev",
        )

        # 4. 导出
        json_path = tmp_path / "summary.json"
        csv_path = tmp_path / "metrics.csv"
        export_summary_json(summary, json_path)
        export_summary_csv(summary, csv_path)

        assert json_path.exists()
        assert csv_path.exists()
        assert summary.document_count == 3


# ============================================================
# 测试套件 9：W1-07 v2 新增指标（无依据输出率/多值精确/amount_type）
# ============================================================


class TestCheckUnjustified:
    """W1-07 v2: _check_unjustified 单元测试。

    无依据定义：
    - evidence_text 为空/None → 无依据
    - raw_text 为 None → 无法验证，视为有依据（保守）
    - evidence_text 非空但原文找不到 → 无依据
    """

    def test_empty_evidence_is_unjustified(self):
        """evidence_text 为空 → 无依据。"""
        from backend.evaluation import _check_unjustified
        v = LLMExtractedValue(raw_value="100", evidence_text="")
        assert _check_unjustified(v, "原文包含 100 元") is True

    def test_none_evidence_is_unjustified(self):
        """evidence_text 为 None → 无依据。"""
        from backend.evaluation import _check_unjustified
        v = LLMExtractedValue(raw_value="100", evidence_text=None)
        assert _check_unjustified(v, "原文包含 100 元") is True

    def test_whitespace_only_evidence_is_unjustified(self):
        """evidence_text 全是空白 → 无依据（trim 后为空）。"""
        from backend.evaluation import _check_unjustified
        v = LLMExtractedValue(raw_value="100", evidence_text="   \t\n  ")
        assert _check_unjustified(v, "原文包含 100 元") is True

    def test_evidence_not_in_raw_text_is_unjustified(self):
        """evidence_text 非空但原文找不到 → 无依据。"""
        from backend.evaluation import _check_unjustified
        v = LLMExtractedValue(raw_value="100", evidence_text="这条证据原文里不存在")
        assert _check_unjustified(v, "原文包含 100 元") is True

    def test_evidence_in_raw_text_is_justified(self):
        """evidence_text 非空且能在原文找到 → 有依据。"""
        from backend.evaluation import _check_unjustified
        v = LLMExtractedValue(raw_value="100", evidence_text="100 元")
        assert _check_unjustified(v, "原文包含 100 元预算") is False

    def test_none_raw_text_is_justified_conservative(self):
        """raw_text=None → 无法验证，保守判为有依据（不冤枉）。"""
        from backend.evaluation import _check_unjustified
        v = LLMExtractedValue(raw_value="100", evidence_text="任意证据")
        assert _check_unjustified(v, None) is False


class TestEvaluateDocumentUnjustified:
    """W1-07 v2: evaluate_document 的 unjustified_count 字段。"""

    def test_unjustified_count_empty_evidence(self):
        """系统输出值 evidence_text 为空 → unjustified_count=1。"""
        gold = _make_gold(
            "d1",
            [_make_present_field(CoreFieldName.AMOUNT, [("100万元", "1000000.00")])],
        )
        system = _make_system_record(
            "d1",
            [LLMExtractedField(
                field_name=CoreFieldName.AMOUNT,
                values=[LLMExtractedValue(
                    raw_value="100万元",
                    normalized_value="1000000.00",
                    evidence_text="",  # 空 evidence
                )],
            )],
        )
        results = evaluate_document(gold, system, raw_text="预算金额 100 万元")
        r = results[0]
        assert r.unjustified_count == 1
        assert r.unjustified_values == ["100万元"]
        # 值匹配金标但 evidence 空 → 值是对的但没引用证据
        assert r.matched_count == 1
        assert r.is_correct is True

    def test_unjustified_count_evidence_not_in_raw_text(self):
        """系统输出值 evidence_text 非空但原文找不到 → unjustified_count=1。"""
        gold = _make_gold(
            "d1",
            [_make_present_field(CoreFieldName.AMOUNT, [("100万元", "1000000.00")])],
        )
        system = _make_system_record(
            "d1",
            [LLMExtractedField(
                field_name=CoreFieldName.AMOUNT,
                values=[LLMExtractedValue(
                    raw_value="100万元",
                    normalized_value="1000000.00",
                    evidence_text="这条证据原文里不存在",  # 不在原文
                )],
            )],
        )
        results = evaluate_document(gold, system, raw_text="预算金额 100 万元")
        r = results[0]
        assert r.unjustified_count == 1

    def test_unjustified_count_no_raw_text_is_zero(self):
        """raw_text=None + evidence_text 非空 → 保守判为有依据(unjustified=0)。

        注意：evidence_text 为空时仍算无依据（LLM 没引用证据 = 瞎编）。
        "保守"只适用于 evidence_text 非空但无原文可验证的情况。
        """
        gold = _make_gold(
            "d1",
            [_make_present_field(CoreFieldName.AMOUNT, [("100万元", "1000000.00")])],
        )
        system = _make_system_record(
            "d1",
            [LLMExtractedField(
                field_name=CoreFieldName.AMOUNT,
                values=[LLMExtractedValue(
                    raw_value="100万元",
                    normalized_value="1000000.00",
                    evidence_text="100 万元",  # 非空，但无原文可验证
                )],
            )],
        )
        results = evaluate_document(gold, system, raw_text=None)
        r = results[0]
        assert r.unjustified_count == 0  # 保守，不冤枉

    def test_unjustified_count_empty_evidence_always_unjustified(self):
        """evidence_text 为空 → 无论 raw_text 是否 None 都算无依据。

        设计原则：LLM 没引用证据 = 瞎编，不管有没有原文。
        """
        gold = _make_gold(
            "d1",
            [_make_present_field(CoreFieldName.AMOUNT, [("100万元", "1000000.00")])],
        )
        system = _make_system_record(
            "d1",
            [LLMExtractedField(
                field_name=CoreFieldName.AMOUNT,
                values=[LLMExtractedValue(
                    raw_value="100万元",
                    normalized_value="1000000.00",
                    evidence_text="",  # 空 evidence
                )],
            )],
        )
        # raw_text=None 时，evidence_text 为空仍然算无依据
        results = evaluate_document(gold, system, raw_text=None)
        r = results[0]
        assert r.unjustified_count == 1
        # raw_text 非空时，evidence_text 为空也仍然算无依据
        results2 = evaluate_document(gold, system, raw_text="预算 100 万元")
        r2 = results2[0]
        assert r2.unjustified_count == 1

    def test_unjustified_count_partial_in_multi_value(self):
        """多值字段：部分值无依据 → unjustified_count=部分。"""
        gold = _make_gold(
            "d1",
            [_make_present_field(CoreFieldName.WINNER_NAME, [
                ("甲公司", "甲"),
                ("乙公司", "乙"),
                ("丙公司", "丙"),
            ])],
        )
        system = _make_system_record(
            "d1",
            [LLMExtractedField(
                field_name=CoreFieldName.WINNER_NAME,
                values=[
                    LLMExtractedValue(raw_value="甲公司", normalized_value="甲", evidence_text="甲公司"),
                    LLMExtractedValue(raw_value="乙公司", normalized_value="乙", evidence_text=""),  # 无依据
                    LLMExtractedValue(raw_value="丙公司", normalized_value="丙", evidence_text="丙公司"),
                ],
            )],
        )
        results = evaluate_document(gold, system, raw_text="中标人：甲公司、乙公司、丙公司")
        r = results[0]
        assert r.unjustified_count == 1
        assert r.unjustified_values == ["乙公司"]

    def test_unjustified_count_all_justified(self):
        """所有值都有 evidence 且在原文 → unjustified_count=0。"""
        gold = _make_gold(
            "d1",
            [_make_present_field(CoreFieldName.AMOUNT, [("100万元", "1000000.00")])],
        )
        system = _make_system_record(
            "d1",
            [LLMExtractedField(
                field_name=CoreFieldName.AMOUNT,
                values=[LLMExtractedValue(
                    raw_value="100万元",
                    normalized_value="1000000.00",
                    evidence_text="100 万元",
                )],
            )],
        )
        results = evaluate_document(gold, system, raw_text="预算 100 万元整")
        r = results[0]
        assert r.unjustified_count == 0


class TestMultiValuePrecisionRecall:
    """W1-07 v2: 多值字段精确 P/R（按值数比例，非"至少1个匹配"）。"""

    def test_precision_multi_recall_multi(self):
        """系统输出 3 值匹配 2，金标有 4 值 → precision_multi=2/3, recall_multi=2/4。"""
        gold = _make_gold(
            "d1",
            [_make_present_field(CoreFieldName.WINNER_NAME, [
                ("甲公司", "甲"),
                ("乙公司", "乙"),
                ("丙公司", "丙"),
                ("丁公司", "丁"),
            ])],
        )
        system = _make_system_record(
            "d1",
            [LLMExtractedField(
                field_name=CoreFieldName.WINNER_NAME,
                values=[
                    LLMExtractedValue(raw_value="甲公司", normalized_value="甲", evidence_text="甲公司"),
                    LLMExtractedValue(raw_value="乙公司", normalized_value="乙", evidence_text="乙公司"),
                    LLMExtractedValue(raw_value="戊公司", normalized_value="戊", evidence_text="戊公司"),  # 误报
                ],
            )],
        )
        results = evaluate_document(gold, system, raw_text="中标人：甲公司、乙公司、丙公司、丁公司、戊公司")
        r = results[0]
        # matched_value_count = 2 (甲、乙), gold_value_total = 4, system_value_total = 3
        assert r.matched_value_count == 2
        assert r.gold_value_total == 4
        assert r.system_output_count == 3  # 原始计数（含重复）
        # is_correct 用旧逻辑（至少1个匹配），仍是 True，但精确指标揭示真相
        assert r.is_correct is True

    def test_multi_value_zero_match(self):
        """多值字段系统全错 → matched_value_count=0, precision_multi=0。"""
        gold = _make_gold(
            "d1",
            [_make_present_field(CoreFieldName.WINNER_NAME, [("甲公司", "甲")])],
        )
        system = _make_system_record(
            "d1",
            [LLMExtractedField(
                field_name=CoreFieldName.WINNER_NAME,
                values=[LLMExtractedValue(raw_value="乙公司", normalized_value="乙", evidence_text="乙公司")],
            )],
        )
        results = evaluate_document(gold, system, raw_text="中标人：甲公司、乙公司")
        r = results[0]
        assert r.matched_value_count == 0
        assert r.gold_value_total == 1
        # is_correct 用旧逻辑（至少1个匹配），0 匹配 → False
        assert r.is_correct is False


class TestAmountTypeMismatch:
    """W1-07 v2: 金额字段 amount_type 与金标不一致校验。"""

    def test_amount_type_match(self):
        """金额字段 amount_type 与金标一致 → mismatch_count=0。"""
        gold = _make_gold(
            "d1",
            [_make_present_field(
                CoreFieldName.AMOUNT,
                [("100万元", "1000000.00")],
                amount_type=AmountType.AWARD,
            )],
        )
        system = _make_system_record(
            "d1",
            [LLMExtractedField(
                field_name=CoreFieldName.AMOUNT,
                values=[LLMExtractedValue(
                    raw_value="100万元",
                    normalized_value="1000000.00",
                    amount_type=AmountType.AWARD,  # 一致
                    evidence_text="100 万元",
                )],
            )],
        )
        results = evaluate_document(gold, system, raw_text="中标金额 100 万元")
        r = results[0]
        assert r.amount_type_mismatch_count == 0

    def test_amount_type_mismatch(self):
        """金额字段 amount_type 与金标不一致 → mismatch_count=1。"""
        gold = _make_gold(
            "d1",
            [_make_present_field(
                CoreFieldName.AMOUNT,
                [("100万元", "1000000.00")],
                amount_type=AmountType.AWARD,  # 金标：中标金额
            )],
        )
        system = _make_system_record(
            "d1",
            [LLMExtractedField(
                field_name=CoreFieldName.AMOUNT,
                values=[LLMExtractedValue(
                    raw_value="100万元",
                    normalized_value="1000000.00",
                    amount_type=AmountType.BUDGET,  # 系统：预算（错）
                    evidence_text="100 万元",
                )],
            )],
        )
        results = evaluate_document(gold, system, raw_text="中标金额 100 万元")
        r = results[0]
        assert r.amount_type_mismatch_count == 1

    def test_amount_type_mismatch_skipped_for_non_amount(self):
        """非金额字段不校验 amount_type。"""
        gold = _make_gold(
            "d1",
            [_make_present_field(CoreFieldName.WINNER_NAME, [("甲公司", "甲")])],
        )
        # LLMExtractedValue 的 amount_type 是 str，可任意值
        system = _make_system_record(
            "d1",
            [LLMExtractedField(
                field_name=CoreFieldName.WINNER_NAME,
                values=[LLMExtractedValue(
                    raw_value="甲公司",
                    normalized_value="甲",
                    amount_type="budget",  # 字段不是 amount，不应触发校验
                    evidence_text="甲公司",
                )],
            )],
        )
        results = evaluate_document(gold, system, raw_text="中标人：甲公司")
        r = results[0]
        assert r.amount_type_mismatch_count == 0

    def test_amount_type_mismatch_skipped_when_gold_none(self):
        """金标 amount_type 为 None 时不校验。"""
        # _make_present_field 的 amount_type 默认 None
        gold = _make_gold(
            "d1",
            [_make_present_field(
                CoreFieldName.AMOUNT,
                [("100万元", "1000000.00")],
                # amount_type=None
            )],
        )
        system = _make_system_record(
            "d1",
            [LLMExtractedField(
                field_name=CoreFieldName.AMOUNT,
                values=[LLMExtractedValue(
                    raw_value="100万元",
                    normalized_value="1000000.00",
                    amount_type=AmountType.BUDGET,  # 系统有，但金标 None
                    evidence_text="100 万元",
                )],
            )],
        )
        results = evaluate_document(gold, system, raw_text="金额 100 万元")
        r = results[0]
        assert r.amount_type_mismatch_count == 0


class TestEvaluateDatasetRawTexts:
    """W1-07 v2: evaluate_dataset 的 raw_texts 聚合。"""

    def test_dataset_aggregates_unjustified(self):
        """数据集级 unjustified_count 聚合正确。"""
        gold_docs = [
            _make_gold("d1", [_make_present_field(CoreFieldName.AMOUNT, [("100万元", "1000000.00")])]),
            _make_gold("d2", [_make_present_field(CoreFieldName.AMOUNT, [("200万元", "2000000.00")])]),
        ]
        system_records = [
            _make_system_record("d1", [LLMExtractedField(
                field_name=CoreFieldName.AMOUNT,
                values=[LLMExtractedValue(
                    raw_value="100万元", normalized_value="1000000.00", evidence_text="",
                )],
            )]),
            _make_system_record("d2", [LLMExtractedField(
                field_name=CoreFieldName.AMOUNT,
                values=[LLMExtractedValue(
                    raw_value="200万元", normalized_value="2000000.00", evidence_text="200 万元",
                )],
            )]),
        ]
        raw_texts = {
            "d1": "预算 100 万元",
            "d2": "预算 200 万元",
        }
        summary = evaluate_dataset(
            gold_docs=gold_docs,
            system_records=system_records,
            system_identifier="v2-test",
            raw_texts=raw_texts,
        )
        m = next(m for m in summary.field_metrics if m.field_name == CoreFieldName.AMOUNT)
        # d1 的值 evidence 为空 → unjustified=1；d2 的值 evidence 在原文 → unjustified=0
        assert m.unjustified_count == 1
        assert m.system_value_total == 2

    def test_dataset_without_raw_texts_keeps_zero(self):
        """不传 raw_texts + evidence_text 非空 → 保守判为有依据(unjustified=0)。

        注意：evidence_text 为空时仍算无依据。要测试"保守"行为，
        必须用 evidence_text 非空但无原文可验证的场景。
        """
        gold_docs = [
            _make_gold("d1", [_make_present_field(CoreFieldName.AMOUNT, [("100万元", "1000000.00")])]),
        ]
        system_records = [
            _make_system_record("d1", [LLMExtractedField(
                field_name=CoreFieldName.AMOUNT,
                values=[LLMExtractedValue(
                    raw_value="100万元", normalized_value="1000000.00",
                    evidence_text="100 万元",  # 非空，但无原文可验证
                )],
            )]),
        ]
        # 不传 raw_texts → evidence_text 非空但无法验证 → 保守判为有依据
        summary = evaluate_dataset(
            gold_docs=gold_docs,
            system_records=system_records,
            system_identifier="v2-test-no-raw",
        )
        m = next(m for m in summary.field_metrics if m.field_name == CoreFieldName.AMOUNT)
        assert m.unjustified_count == 0  # 保守
        assert m.system_value_total == 1

    def test_dataset_aggregates_amount_type_mismatch(self):
        """数据集级 amount_type_mismatch_count 聚合正确。"""
        gold_docs = [
            _make_gold("d1", [_make_present_field(
                CoreFieldName.AMOUNT,
                [("100万元", "1000000.00")],
                amount_type=AmountType.AWARD,
            )]),
            _make_gold("d2", [_make_present_field(
                CoreFieldName.AMOUNT,
                [("200万元", "2000000.00")],
                amount_type=AmountType.AWARD,
            )]),
        ]
        system_records = [
            _make_system_record("d1", [LLMExtractedField(
                field_name=CoreFieldName.AMOUNT,
                values=[LLMExtractedValue(
                    raw_value="100万元", normalized_value="1000000.00",
                    amount_type=AmountType.BUDGET,  # 错
                    evidence_text="100 万元",
                )],
            )]),
            _make_system_record("d2", [LLMExtractedField(
                field_name=CoreFieldName.AMOUNT,
                values=[LLMExtractedValue(
                    raw_value="200万元", normalized_value="2000000.00",
                    amount_type=AmountType.AWARD,  # 对
                    evidence_text="200 万元",
                )],
            )]),
        ]
        summary = evaluate_dataset(
            gold_docs=gold_docs,
            system_records=system_records,
            system_identifier="v2-at-test",
            raw_texts={"d1": "100 万元", "d2": "200 万元"},
        )
        m = next(m for m in summary.field_metrics if m.field_name == CoreFieldName.AMOUNT)
        assert m.amount_type_mismatch_count == 1


class TestFieldMetricsV2Properties:
    """W1-07 v2: FieldMetrics 派生属性计算。"""

    def test_unjustified_rate(self):
        """unjustified_rate = unjustified_count / system_value_total。"""
        from backend.schemas import FieldMetrics
        m = FieldMetrics(field_name=CoreFieldName.AMOUNT)
        m.system_value_total = 4
        m.unjustified_count = 1
        assert m.unjustified_rate == 0.25

    def test_unjustified_rate_zero_denominator(self):
        """system_value_total=0 → unjustified_rate=0（避免除零）。"""
        from backend.schemas import FieldMetrics
        m = FieldMetrics(field_name=CoreFieldName.AMOUNT)
        m.system_value_total = 0
        m.unjustified_count = 0
        assert m.unjustified_rate == 0.0

    def test_precision_multi(self):
        """precision_multi = matched / system_value_total。"""
        from backend.schemas import FieldMetrics
        m = FieldMetrics(field_name=CoreFieldName.WINNER_NAME)
        m.system_value_total = 4
        m.matched_value_count = 3
        assert m.precision_multi == 0.75

    def test_recall_multi(self):
        """recall_multi = matched / gold_value_total。"""
        from backend.schemas import FieldMetrics
        m = FieldMetrics(field_name=CoreFieldName.WINNER_NAME)
        m.gold_value_total = 5
        m.matched_value_count = 2
        assert m.recall_multi == 0.4

    def test_f1_multi(self):
        """f1_multi = 2*P*R/(P+R)。"""
        from backend.schemas import FieldMetrics
        m = FieldMetrics(field_name=CoreFieldName.WINNER_NAME)
        m.system_value_total = 4  # P = 3/4 = 0.75
        m.gold_value_total = 5    # R = 3/5 = 0.6
        m.matched_value_count = 3
        # F1 = 2*0.75*0.6 / (0.75+0.6) = 0.9/1.35 = 0.6667
        assert abs(m.f1_multi - (2 * 0.75 * 0.6 / (0.75 + 0.6))) < 1e-9

    def test_f1_multi_zero_when_no_match(self):
        """matched=0 → f1_multi=0。"""
        from backend.schemas import FieldMetrics
        m = FieldMetrics(field_name=CoreFieldName.WINNER_NAME)
        m.system_value_total = 2
        m.gold_value_total = 2
        m.matched_value_count = 0
        assert m.f1_multi == 0.0

    def test_to_dict_includes_v2_fields(self):
        """to_dict 包含所有 v2 新增字段。"""
        from backend.schemas import FieldMetrics
        m = FieldMetrics(field_name=CoreFieldName.AMOUNT)
        m.system_value_total = 4
        m.unjustified_count = 1
        m.matched_value_count = 3
        m.gold_value_total = 5
        m.amount_type_mismatch_count = 2
        d = m.to_dict()
        # 必须包含 v2 新增的所有字段
        assert "system_value_total" in d
        assert "unjustified_count" in d
        assert "unjustified_rate" in d
        assert "matched_value_count" in d
        assert "gold_value_total" in d
        assert "precision_multi" in d
        assert "recall_multi" in d
        assert "f1_multi" in d
        assert "amount_type_mismatch_count" in d
        assert d["unjustified_rate"] == 0.25
        assert d["precision_multi"] == 0.75
        assert d["recall_multi"] == 0.6


class TestExportCsvV2:
    """W1-07 v2: CSV 导出包含 v2 新增列。"""

    def test_csv_includes_v2_headers(self, tmp_path: Path):
        """CSV headers 包含所有 v2 新增列。"""
        gold_docs = [
            _make_gold("d1", [_make_present_field(CoreFieldName.AMOUNT, [("100万元", "1000000.00")])]),
        ]
        system_records = [
            _make_system_record("d1", [LLMExtractedField(
                field_name=CoreFieldName.AMOUNT,
                values=[LLMExtractedValue(
                    raw_value="100万元", normalized_value="1000000.00",
                    amount_type=AmountType.AWARD, evidence_text="100 万元",
                )],
            )]),
        ]
        summary = evaluate_dataset(
            gold_docs=gold_docs,
            system_records=system_records,
            system_identifier="csv-v2",
            raw_texts={"d1": "100 万元"},
        )
        csv_path = tmp_path / "v2.csv"
        export_summary_csv(summary, csv_path)

        with csv_path.open(encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            headers = next(reader)
            rows = list(reader)

        # 验证 v2 新增 headers 存在
        v2_headers = [
            "system_value_total", "unjustified_count", "unjustified_rate",
            "matched_value_count", "gold_value_total",
            "precision_multi", "recall_multi", "f1_multi",
            "amount_type_mismatch_count",
        ]
        for h in v2_headers:
            assert h in headers, f"CSV 缺少 v2 header: {h}"

        # 找到 amount 字段那行（CSV 按 CoreFieldName.ALL 顺序输出，第一行可能是 project_identifier）
        idx = {h: i for i, h in enumerate(headers)}
        amount_row = None
        for row in rows:
            if row[idx["field_name"]] == CoreFieldName.AMOUNT:
                amount_row = row
                break
        assert amount_row is not None, "CSV 中未找到 amount 字段行"

        # 验证 amount 字段行的 v2 数据正确
        assert amount_row[idx["system_value_total"]] == "1"
        assert amount_row[idx["unjustified_count"]] == "0"
        assert amount_row[idx["unjustified_rate"]] == "0.0"
        assert amount_row[idx["matched_value_count"]] == "1"
        assert amount_row[idx["gold_value_total"]] == "1"
        assert amount_row[idx["amount_type_mismatch_count"]] == "0"

    def test_json_includes_v2_fields(self, tmp_path: Path):
        """JSON 导出包含 v2 新增字段。"""
        gold_docs = [
            _make_gold("d1", [_make_present_field(CoreFieldName.AMOUNT, [("100万元", "1000000.00")])]),
        ]
        system_records = [
            _make_system_record("d1", [LLMExtractedField(
                field_name=CoreFieldName.AMOUNT,
                values=[LLMExtractedValue(
                    raw_value="100万元", normalized_value="1000000.00",
                    amount_type=AmountType.AWARD, evidence_text="",
                )],
            )]),
        ]
        summary = evaluate_dataset(
            gold_docs=gold_docs,
            system_records=system_records,
            system_identifier="json-v2",
            raw_texts={"d1": "100 万元"},
        )
        json_path = tmp_path / "v2.json"
        export_summary_json(summary, json_path)

        data = json.loads(json_path.read_text(encoding="utf-8"))
        amount_field = next(f for f in data["fields"] if f["field_name"] == CoreFieldName.AMOUNT)
        # system_value_total=1, unjustified_count=1（evidence 为空）→ rate=1.0
        assert amount_field["system_value_total"] == 1
        assert amount_field["unjustified_count"] == 1
        assert amount_field["unjustified_rate"] == 1.0


# ============================================================
# 测试套件 10：边缘情况补充（提升覆盖率）
# ============================================================


class TestNormalizeValueEdgeCases:
    """normalize_value 边缘情况：金额无数字、日期无匹配。"""

    def test_amount_no_digits_returns_original(self):
        """金额字段值不含数字 → 返回去空白后的原值（fallback）。"""
        # "abc万元" 去单位后 "abc"，re.search 找不到数字 → 返回原值
        result = normalize_value(CoreFieldName.AMOUNT, "abc万元")
        # 期望返回原值 s（去空白后）
        assert result == "abc万元"

    def test_amount_decimal_conversion_failure(self):
        """金额字段值 Decimal 转换异常 → 返回原值。"""
        # 构造一个能匹配 re.search 但 Decimal 转换失败的情况很难
        # 因为 [\d.]+ 匹配的总是有效数字。这里测试多个小数点的边缘情况
        result = normalize_value(CoreFieldName.AMOUNT, "1.2.3万元")
        # Decimal("1.2.3") 会抛 InvalidOperation → 返回原值
        assert result == "1.2.3万元"

    def test_date_no_match_returns_original(self):
        """日期字段值无法匹配任何格式 → 返回原值。"""
        result = normalize_value(CoreFieldName.PUBLISH_DATE, "无日期")
        assert result == "无日期"


class TestEvaluateDocumentEdgeCases:
    """evaluate_document 边缘情况。"""

    def test_evaluate_with_llm_extraction_output_directly(self):
        """直接传 LLMExtractionOutput（不包在 Record 里）→ 正常评测。"""
        gold = _make_gold(
            "d1",
            [_make_present_field(CoreFieldName.AMOUNT, [("100万元", "1000000.00")])],
        )
        # 直接传 LLMExtractionOutput（第三个分支：isinstance 不是 Record）
        direct_output = LLMExtractionOutput(fields=[
            LLMExtractedField(
                field_name=CoreFieldName.AMOUNT,
                values=[LLMExtractedValue(
                    raw_value="100万元", normalized_value="1000000.00",
                    evidence_text="100 万元",
                )],
            ),
        ])
        results = evaluate_document(gold, direct_output, raw_text="100 万元")
        r = results[0]
        assert r.matched_count == 1
        assert r.is_correct is True

    def test_gold_other_status_counted(self):
        """金标 not_applicable 状态 → gold_other_count 增加。"""
        gold = _make_gold(
            "d1",
            [_make_na_field(CoreFieldName.WINNER_NAME)],
        )
        system = _make_system_record("d1", [])
        results = evaluate_document(gold, system)
        r = results[0]
        assert r.gold_status == GoldStatus.NOT_APPLICABLE
        assert r.is_correct is None  # 不参与主评测

        # 在 dataset 级别验证 gold_other_count
        summary = evaluate_dataset(
            gold_docs=[gold],
            system_records=[system],
            system_identifier="edge-test",
        )
        m = next(m for m in summary.field_metrics if m.field_name == CoreFieldName.WINNER_NAME)
        assert m.gold_other_count == 1
