"""BidAgent v4.1 标注 JSON Schema 校验测试（W1-04）。

覆盖：
- EvidenceSpan：合法/非法区间
- FieldValue：primary 证据校验、金额类型
- AnnotatedField：状态与值一致性
- AnnotationDocument：字段名唯一性、序列化
- LLMExtractionRecord：成功/失败一致性
- FieldMetrics：Precision/Recall/F1 计算
- EvaluationSummary：宏平均
- make_empty_annotation_document 构造函数

工程规范：
- 不依赖数据库
- 不依赖外部 LLM API
- 直接断言 ValidationError 与具体字段
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest
from pydantic import ValidationError

from backend.enums import (
    AmountType,
    CoreFieldName,
    EvidenceRole,
    GoldStatus,
    SupportLevel,
    TaxStatus,
)
from backend.schemas import (
    AnnotatedField,
    AnnotationDocument,
    EvidenceSpan,
    EvaluationSummary,
    FieldMetrics,
    FieldValue,
    LLMExtractionOutput,
    LLMExtractionRecord,
    LLMExtractedField,
    LLMExtractedValue,
    make_empty_annotation_document,
)


# ============================================================
# 测试套件 1：EvidenceSpan
# ============================================================


class TestEvidenceSpan:
    """证据区间校验。"""

    def test_valid_primary_span(self):
        span = EvidenceSpan(
            role=EvidenceRole.PRIMARY, start=10, end=20, text="中标金额：128.50万元"
        )
        assert span.role == EvidenceRole.PRIMARY
        assert span.start == 10
        assert span.end == 20

    def test_start_must_be_non_negative(self):
        with pytest.raises(ValidationError) as exc:
            EvidenceSpan(role="primary", start=-1, end=10, text="x")
        assert "start" in str(exc.value).lower() or "greater_than_equal" in str(exc.value).lower()

    def test_end_must_be_positive(self):
        with pytest.raises(ValidationError):
            EvidenceSpan(role="primary", start=0, end=0, text="x")

    def test_end_must_exceed_start(self):
        with pytest.raises(ValidationError) as exc:
            EvidenceSpan(role="primary", start=20, end=10, text="x")
        assert "end" in str(exc.value).lower()

    def test_text_cannot_be_empty(self):
        with pytest.raises(ValidationError):
            EvidenceSpan(role="primary", start=0, end=10, text="")

    def test_unknown_role_rejected(self):
        with pytest.raises(ValidationError):
            EvidenceSpan(role="unknown_role", start=0, end=10, text="x")  # type: ignore[arg-type]

    def test_extra_field_forbidden(self):
        with pytest.raises(ValidationError) as exc:
            EvidenceSpan(
                role="primary", start=0, end=10, text="x",
                extra_field="should_fail",  # type: ignore[call-arg]
            )
        assert "extra" in str(exc.value).lower()


# ============================================================
# 测试套件 2：FieldValue
# ============================================================


class TestFieldValue:
    """字段值校验。"""

    def test_valid_value_with_evidence(self):
        v = FieldValue(
            raw_value="128.50万元",
            normalized_value="1285000.00",
            amount_type=AmountType.AWARD,
            currency="CNY",
            original_unit="万元",
            tax_status=TaxStatus.UNKNOWN,
            lot_id="包1",
            acceptable_evidence_spans=[
                EvidenceSpan(
                    role=EvidenceRole.PRIMARY, start=1024, end=1040,
                    text="中标金额：128.50万元",
                ),
            ],
        )
        assert v.raw_value == "128.50万元"
        assert v.amount_type == AmountType.AWARD

    def test_evidence_without_primary_rejected(self):
        """有证据但没有 primary 时拒绝。"""
        with pytest.raises(ValidationError) as exc:
            FieldValue(
                raw_value="128.50万元",
                acceptable_evidence_spans=[
                    EvidenceSpan(
                        role=EvidenceRole.CONTEXT, start=0, end=10, text="上下文",
                    ),
                ],
            )
        assert "primary" in str(exc.value).lower()

    def test_empty_evidence_allowed(self):
        """没有证据时允许（系统输出场景）。"""
        v = FieldValue(raw_value="某公司")
        assert v.acceptable_evidence_spans == []

    def test_raw_value_cannot_be_empty(self):
        with pytest.raises(ValidationError):
            FieldValue(raw_value="")

    def test_invalid_amount_type_rejected(self):
        with pytest.raises(ValidationError):
            FieldValue(raw_value="x", amount_type="invalid_type")  # type: ignore[arg-type]

    def test_invalid_tax_status_rejected(self):
        with pytest.raises(ValidationError):
            FieldValue(raw_value="x", tax_status="invalid_tax")  # type: ignore[arg-type]


# ============================================================
# 测试套件 3：AnnotatedField
# ============================================================


class TestAnnotatedField:
    """标注字段校验。"""

    def test_present_field_requires_values(self):
        with pytest.raises(ValidationError) as exc:
            AnnotatedField(
                field_name=CoreFieldName.AMOUNT,
                gold_status=GoldStatus.PRESENT,
                values=[],
            )
        assert "present" in str(exc.value).lower()

    def test_absent_field_must_have_empty_values(self):
        with pytest.raises(ValidationError) as exc:
            AnnotatedField(
                field_name=CoreFieldName.AMOUNT,
                gold_status=GoldStatus.ABSENT,
                values=[FieldValue(raw_value="100")],
            )
        assert "absent" in str(exc.value).lower()

    def test_not_applicable_allows_empty_values(self):
        f = AnnotatedField(
            field_name=CoreFieldName.WINNER_NAME,
            gold_status=GoldStatus.NOT_APPLICABLE,
            values=[],
        )
        assert f.gold_status == GoldStatus.NOT_APPLICABLE
        assert f.values == []

    def test_invalid_field_name_rejected(self):
        with pytest.raises(ValidationError):
            AnnotatedField(
                field_name="invalid_field_name",  # type: ignore[arg-type]
                gold_status=GoldStatus.ABSENT,
            )

    def test_note_is_stripped(self):
        f = AnnotatedField(
            field_name=CoreFieldName.AMOUNT,
            gold_status=GoldStatus.ABSENT,
            note="  备注空格  ",
        )
        assert f.note == "备注空格"


# ============================================================
# 测试套件 4：AnnotationDocument
# ============================================================


class TestAnnotationDocument:
    """标注文档校验。"""

    def _make_minimal_field(self):
        return AnnotatedField(
            field_name=CoreFieldName.AMOUNT,
            gold_status=GoldStatus.ABSENT,
        )

    def test_valid_document(self):
        doc = AnnotationDocument(
            document_id="version-001",
            annotator_id="annotator-a",
            annotation_version="1.0",
            fields=[self._make_minimal_field()],
        )
        assert doc.document_id == "version-001"
        assert len(doc.fields) == 1

    def test_duplicate_field_name_rejected(self):
        with pytest.raises(ValidationError) as exc:
            AnnotationDocument(
                document_id="version-001",
                annotator_id="annotator-a",
                annotation_version="1.0",
                fields=[
                    AnnotatedField(
                        field_name=CoreFieldName.AMOUNT,
                        gold_status=GoldStatus.ABSENT,
                    ),
                    AnnotatedField(
                        field_name=CoreFieldName.AMOUNT,  # 重复
                        gold_status=GoldStatus.ABSENT,
                    ),
                ],
            )
        assert "重复" in str(exc.value)

    def test_empty_fields_rejected(self):
        with pytest.raises(ValidationError):
            AnnotationDocument(
                document_id="version-001",
                annotator_id="annotator-a",
                annotation_version="1.0",
                fields=[],
            )

    def test_get_field_by_name(self):
        doc = AnnotationDocument(
            document_id="v1",
            annotator_id="A",
            annotation_version="1.0",
            fields=[
                AnnotatedField(field_name=CoreFieldName.AMOUNT, gold_status=GoldStatus.ABSENT),
                AnnotatedField(field_name=CoreFieldName.PUBLISH_DATE, gold_status=GoldStatus.PRESENT,
                               values=[FieldValue(raw_value="2026-07-20")]),
            ],
        )
        amount = doc.get_field(CoreFieldName.AMOUNT)
        assert amount is not None
        assert amount.gold_status == GoldStatus.ABSENT

        publish = doc.get_field(CoreFieldName.PUBLISH_DATE)
        assert publish is not None
        assert publish.gold_status == GoldStatus.PRESENT

        missing = doc.get_field(CoreFieldName.WINNER_NAME)
        assert missing is None

    def test_full_round_trip_json(self):
        """JSON 序列化/反序列化往返测试。"""
        original = AnnotationDocument(
            document_id="v-roundtrip",
            annotator_id="A",
            annotation_version="1.0",
            fields=[
                AnnotatedField(
                    field_name=CoreFieldName.AMOUNT,
                    gold_status=GoldStatus.PRESENT,
                    values=[
                        FieldValue(
                            raw_value="128.50万元",
                            normalized_value="1285000.00",
                            amount_type=AmountType.AWARD,
                            currency="CNY",
                            original_unit="万元",
                            tax_status=TaxStatus.UNKNOWN,
                            lot_id="包1",
                            acceptable_evidence_spans=[
                                EvidenceSpan(
                                    role=EvidenceRole.PRIMARY,
                                    start=100, end=120,
                                    text="中标金额：128.50万元",
                                ),
                            ],
                        ),
                    ],
                    note="测试备注",
                ),
            ],
        )
        json_str = original.model_dump_json()
        restored = AnnotationDocument.model_validate_json(json_str)
        assert restored.document_id == original.document_id
        assert restored.fields[0].values[0].raw_value == "128.50万元"
        assert restored.fields[0].values[0].acceptable_evidence_spans[0].start == 100

    def test_matches_manual_example(self):
        """验证 Schema 与《金标数据标注手册》第七章示例兼容。"""
        sample = {
            "document_id": "notice-version-001",
            "annotator_id": "annotator-a",
            "annotation_version": "1.0",
            "fields": [
                {
                    "field_name": "amount",
                    "gold_status": "present",
                    "values": [
                        {
                            "raw_value": "128.50万元",
                            "normalized_value": "1285000.00",
                            "amount_type": "award",
                            "currency": "CNY",
                            "original_unit": "万元",
                            "tax_status": "unknown",
                            "lot_id": "包1",
                            "acceptable_evidence_spans": [
                                {
                                    "role": "primary",
                                    "start": 1024,
                                    "end": 1040,
                                    "text": "中标金额：128.50万元"
                                },
                                {
                                    "role": "qualifier",
                                    "start": 960,
                                    "end": 968,
                                    "text": "第一包"
                                }
                            ]
                        }
                    ],
                    "note": ""
                }
            ]
        }
        doc = AnnotationDocument.model_validate(sample)
        assert doc.document_id == "notice-version-001"
        assert doc.fields[0].field_name == CoreFieldName.AMOUNT
        assert doc.fields[0].values[0].amount_type == AmountType.AWARD
        assert len(doc.fields[0].values[0].acceptable_evidence_spans) == 2


# ============================================================
# 测试套件 5：LLMExtractionRecord
# ============================================================


class TestLLMExtractionRecord:
    """LLM 抽取记录校验。"""

    def test_success_record(self):
        record = LLMExtractionRecord(
            document_id="v1",
            model_identifier="glm-5.2",
            prompt_hash="a" * 64,
            success=True,
            output=LLMExtractionOutput(
                fields=[
                    LLMExtractedField(
                        field_name=CoreFieldName.AMOUNT,
                        support_level=SupportLevel.DIRECT,
                        values=[
                            LLMExtractedValue(
                                raw_value="128.50万元",
                                normalized_value="1285000.00",
                                amount_type=AmountType.AWARD,
                            ),
                        ],
                    ),
                ]
            ),
        )
        assert record.success is True
        assert record.output is not None
        assert len(record.output.fields) == 1

    def test_failure_record_must_have_error(self):
        with pytest.raises(ValidationError) as exc:
            LLMExtractionRecord(
                document_id="v1",
                model_identifier="glm-5.2",
                prompt_hash="a" * 64,
                success=False,
                # 缺少 error_message
            )
        assert "error_message" in str(exc.value).lower()

    def test_failure_record_must_not_have_output(self):
        with pytest.raises(ValidationError) as exc:
            LLMExtractionRecord(
                document_id="v1",
                model_identifier="glm-5.2",
                prompt_hash="a" * 64,
                success=False,
                error_message="timeout",
                output=LLMExtractionOutput(fields=[]),  # 不允许
            )
        assert "output" in str(exc.value).lower()

    def test_success_record_must_not_have_error(self):
        with pytest.raises(ValidationError):
            LLMExtractionRecord(
                document_id="v1",
                model_identifier="glm-5.2",
                prompt_hash="a" * 64,
                success=True,
                error_message="should be empty",  # 不允许
            )

    def test_temperature_bounds(self):
        with pytest.raises(ValidationError):
            LLMExtractionRecord(
                document_id="v1",
                model_identifier="glm-5.2",
                prompt_hash="a" * 64,
                temperature=-0.1,
            )
        with pytest.raises(ValidationError):
            LLMExtractionRecord(
                document_id="v1",
                model_identifier="glm-5.2",
                prompt_hash="a" * 64,
                temperature=2.5,
            )


# ============================================================
# 测试套件 6：FieldMetrics 计算正确性
# ============================================================


class TestFieldMetrics:
    """评测指标计算。"""

    def test_precision_calculation(self):
        m = FieldMetrics(
            field_name=CoreFieldName.AMOUNT,
            gold_present_count=10,
            system_correct_count=8,
            system_output_count=10,
        )
        assert m.precision == 0.8

    def test_precision_zero_output(self):
        m = FieldMetrics(field_name=CoreFieldName.AMOUNT)
        assert m.precision == 0.0

    def test_recall_calculation(self):
        m = FieldMetrics(
            field_name=CoreFieldName.AMOUNT,
            gold_present_count=10,
            system_correct_count=7,
        )
        assert m.recall == pytest.approx(0.7)

    def test_recall_zero_gold(self):
        m = FieldMetrics(field_name=CoreFieldName.AMOUNT, gold_present_count=0)
        assert m.recall == 0.0

    def test_f1_calculation(self):
        m = FieldMetrics(
            field_name=CoreFieldName.AMOUNT,
            gold_present_count=10,
            system_correct_count=8,
            system_output_count=10,
        )
        # P=0.8, R=0.8 → F1=0.8
        assert m.f1 == pytest.approx(0.8)

    def test_f1_zero_p_r(self):
        m = FieldMetrics(field_name=CoreFieldName.AMOUNT)
        assert m.f1 == 0.0

    def test_false_omission_rate_on_absent(self):
        m = FieldMetrics(
            field_name=CoreFieldName.AMOUNT,
            gold_absent_count=5,
            false_positive_on_absent=2,
        )
        assert m.false_omission_rate_on_absent == pytest.approx(0.4)

    def test_false_omission_zero_absent(self):
        m = FieldMetrics(field_name=CoreFieldName.AMOUNT, gold_absent_count=0)
        assert m.false_omission_rate_on_absent == 0.0

    def test_to_dict_includes_derived_metrics(self):
        m = FieldMetrics(
            field_name=CoreFieldName.AMOUNT,
            gold_present_count=10,
            system_correct_count=8,
            system_output_count=10,
        )
        d = m.to_dict()
        assert d["precision"] == 0.8
        assert d["recall"] == 0.8
        assert d["f1"] == 0.8
        assert "false_omission_rate_on_absent" in d


# ============================================================
# 测试套件 7：EvaluationSummary
# ============================================================


class TestEvaluationSummary:
    """评测汇总。"""

    def test_macro_average(self):
        summary = EvaluationSummary(
            run_id="run-001",
            system_identifier="baseline-llm-v1",
            dataset_split="test",
            document_count=100,
            field_metrics=[
                FieldMetrics(
                    field_name=CoreFieldName.AMOUNT,
                    gold_present_count=10,
                    system_correct_count=8,
                    system_output_count=10,
                ),
                FieldMetrics(
                    field_name=CoreFieldName.PUBLISH_DATE,
                    gold_present_count=10,
                    system_correct_count=9,
                    system_output_count=10,
                ),
            ],
        )
        # (0.8 + 0.9) / 2 = 0.85
        assert summary.macro_precision == pytest.approx(0.85)
        assert summary.macro_recall == pytest.approx(0.85)
        assert summary.macro_f1 == pytest.approx(0.85)

    def test_macro_empty_metrics(self):
        summary = EvaluationSummary(
            run_id="run-002",
            system_identifier="empty",
            dataset_split="dev",
        )
        assert summary.macro_precision == 0.0
        assert summary.macro_recall == 0.0
        assert summary.macro_f1 == 0.0

    def test_to_dict_export(self):
        summary = EvaluationSummary(
            run_id="run-003",
            system_identifier="baseline",
            dataset_split="test",
            document_count=50,
            field_metrics=[
                FieldMetrics(field_name=CoreFieldName.AMOUNT),
            ],
        )
        d = summary.to_dict()
        assert d["run_id"] == "run-003"
        assert d["system_identifier"] == "baseline"
        assert d["dataset_split"] == "test"
        assert d["document_count"] == 50
        assert len(d["fields"]) == 1
        assert "macro_precision" in d
        assert "macro_recall" in d
        assert "macro_f1" in d


# ============================================================
# 测试套件 8：便捷构造函数
# ============================================================


class TestMakeEmptyAnnotationDocument:
    """空标注文档构造。"""

    def test_creates_six_fields(self):
        doc = make_empty_annotation_document(
            document_id="v-empty",
            annotator_id="A",
            annotation_version="1.0",
        )
        assert len(doc.fields) == 6
        field_names = {f.field_name for f in doc.fields}
        assert field_names == set(CoreFieldName.ALL)

    def test_all_fields_absent_by_default(self):
        doc = make_empty_annotation_document("v", "A")
        for f in doc.fields:
            assert f.gold_status == GoldStatus.ABSENT
            assert f.values == []

    def test_default_annotation_version(self):
        doc = make_empty_annotation_document("v", "A")
        assert doc.annotation_version == "1.0"


# ============================================================
# 测试套件 9：导出与序列化
# ============================================================


class TestSerialization:
    """Schema 序列化与导出。"""

    def test_annotation_document_to_dict(self):
        doc = make_empty_annotation_document("v1", "A")
        d = doc.model_dump()
        assert d["document_id"] == "v1"
        assert len(d["fields"]) == 6

    def test_evaluation_summary_to_json(self):
        summary = EvaluationSummary(
            run_id="r1",
            system_identifier="s1",
            dataset_split="test",
        )
        json_str = summary.model_dump_json()
        d = json.loads(json_str)
        assert d["run_id"] == "r1"

    def test_llm_record_round_trip(self):
        record = LLMExtractionRecord(
            document_id="v1",
            model_identifier="glm-5.2",
            prompt_hash="a" * 64,
            success=True,
            output=LLMExtractionOutput(fields=[]),
        )
        json_str = record.model_dump_json()
        restored = LLMExtractionRecord.model_validate_json(json_str)
        assert restored.document_id == record.document_id
        assert restored.model_identifier == record.model_identifier
