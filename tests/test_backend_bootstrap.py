"""BidAgent v4.1 Bootstrap 置信区间测试（W1-07 补丁）。

覆盖：
- _percentile：百分位计算
- _resample_docs：重抽样行为
- bootstrap_evaluate：基本流程、CI 范围合理性
- bootstrap_evaluate_async：异步执行不阻塞事件循环
- 异常输入处理
- 可复现性（同 seed 同结果）
"""
from __future__ import annotations

import asyncio

import pytest

from backend.bootstrap import (
    BootstrapResult,
    ConfidenceInterval,
    bootstrap_evaluate,
    bootstrap_evaluate_async,
    _percentile,
    _resample_docs,
)
from backend.enums import (
    AmountType,
    CoreFieldName,
    EvidenceRole,
    GoldStatus,
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


# ============================================================
# 工具
# ============================================================


def _make_gold(doc_id: str, amount_present: bool = True) -> AnnotationDocument:
    """构造金标文档，包含全部 6 个核心字段。

    amount 按 amount_present 设置，其他 5 个字段统一为 absent（金标无值）。
    这样宏平均分母才完整。
    """
    if amount_present:
        amount_field = AnnotatedField(
            field_name=CoreFieldName.AMOUNT,
            gold_status=GoldStatus.PRESENT,
            values=[
                FieldValue(
                    raw_value="100万元",
                    normalized_value="1000000.00",
                    amount_type=AmountType.BUDGET,
                    acceptable_evidence_spans=[
                        EvidenceSpan(
                            role=EvidenceRole.PRIMARY, start=0, end=5,
                            text="100万元",
                        ),
                    ],
                )
            ],
        )
    else:
        amount_field = AnnotatedField(
            field_name=CoreFieldName.AMOUNT, gold_status=GoldStatus.ABSENT
        )
    # 其他 5 个字段全部 absent
    other_fields = [
        AnnotatedField(field_name=name, gold_status=GoldStatus.ABSENT)
        for name in CoreFieldName.ALL
        if name != CoreFieldName.AMOUNT
    ]
    return AnnotationDocument(
        document_id=doc_id,
        annotator_id="A",
        annotation_version="1.0",
        fields=[amount_field] + other_fields,
    )


def _make_system(doc_id: str, success: bool = True, correct: bool = True) -> LLMExtractionRecord:
    if not success:
        return LLMExtractionRecord(
            document_id=doc_id,
            model_identifier="stub",
            prompt_hash="a" * 64,
            success=False,
            error_message="test failure",
        )
    fields: list[LLMExtractedField] = []
    if correct:
        fields.append(
            LLMExtractedField(
                field_name=CoreFieldName.AMOUNT,
                values=[
                    LLMExtractedValue(
                        raw_value="100万元",
                        normalized_value="1000000.00",
                        amount_type=AmountType.BUDGET,
                    )
                ],
            )
        )
    return LLMExtractionRecord(
        document_id=doc_id,
        model_identifier="stub",
        prompt_hash="a" * 64,
        success=True,
        output=LLMExtractionOutput(fields=fields),
    )


# ============================================================
# 测试套件 1：_percentile
# ============================================================


class TestPercentile:
    def test_single_value(self):
        assert _percentile([42.0], 50) == 42.0
        assert _percentile([42.0], 2.5) == 42.0
        assert _percentile([42.0], 97.5) == 42.0

    def test_empty_returns_zero(self):
        assert _percentile([], 50) == 0.0

    def test_median_odd_count(self):
        # 1, 2, 3, 4, 5 → 中位数 3
        assert _percentile([3.0, 1.0, 5.0, 2.0, 4.0], 50) == 3.0

    def test_median_even_count_linear_interpolation(self):
        # 1, 2, 3, 4 → 中位数 2.5
        result = _percentile([1.0, 2.0, 3.0, 4.0], 50)
        assert 2.4 <= result <= 2.6

    def test_extreme_percentiles(self):
        values = [float(i) for i in range(100)]
        # 2.5% 分位数应在 0-3 之间
        lower = _percentile(values, 2.5)
        upper = _percentile(values, 97.5)
        assert 0 <= lower <= 5
        assert 94 <= upper <= 99

    def test_sorted_input_unchanged(self):
        values = [5.0, 3.0, 1.0, 4.0, 2.0]
        _percentile(values, 50)
        # 函数不应修改原列表
        assert values == [5.0, 3.0, 1.0, 4.0, 2.0]


# ============================================================
# 测试套件 2：_resample_docs
# ============================================================


class TestResampleDocs:
    def test_returns_same_length(self):
        import random
        rng = random.Random(42)
        docs = [_make_gold(f"d{i}") for i in range(10)]
        resampled = _resample_docs(docs, rng)
        assert len(resampled) == 10

    def test_empty_input(self):
        import random
        rng = random.Random(42)
        assert _resample_docs([], rng) == []

    def test_resample_with_replacement(self):
        """重抽样允许重复（bootstrap 核心特性）。"""
        import random
        rng = random.Random(42)
        # 单个文档重抽样 100 次，应全部是同一个
        docs = [_make_gold("only-one")]
        resampled = _resample_docs(docs, rng)
        # 1 个文档重抽样 1 次，结果是该文档
        assert len(resampled) == 1
        assert resampled[0].document_id == "only-one"

    def test_resample_reproducible_with_seed(self):
        import random
        docs = [_make_gold(f"d{i}") for i in range(20)]
        rng1 = random.Random(123)
        rng2 = random.Random(123)
        r1 = _resample_docs(docs, rng1)
        r2 = _resample_docs(docs, rng2)
        assert [d.document_id for d in r1] == [d.document_id for d in r2]


# ============================================================
# 测试套件 3：bootstrap_evaluate 基本流程
# ============================================================


class TestBootstrapEvaluate:
    def test_perfect_dataset_narrow_ci(self):
        """完全匹配的数据集，CI 应非常窄（接近 1.0）。

        评测规则：金标 absent + 系统未输出 → 不计入 P/R 分母（按 v4.1 §10.3），
        但 FieldMetrics.precision 在 system_output_count=0 时返回 0。
        因此用 amount 字段单独验证完美匹配场景。
        """
        docs = [_make_gold(f"d{i}") for i in range(20)]
        records = [_make_system(f"d{i}") for i in range(20)]
        result = bootstrap_evaluate(
            gold_docs=docs,
            system_records=records,
            system_identifier="perfect",
            n_bootstrap=100,
            random_seed=42,
        )
        assert isinstance(result, BootstrapResult)
        assert result.bootstrap_samples == 100
        # amount 字段完美匹配，F1 应接近 1.0
        amount_f1 = result.field_f1[CoreFieldName.AMOUNT]
        assert amount_f1.point_estimate == pytest.approx(1.0, abs=0.01)
        assert amount_f1.ci_lower >= 0.99
        assert amount_f1.ci_upper <= 1.0 + 0.001

    def test_zero_dataset_zero_ci(self):
        """完全错的数据集，amount 字段 CI 应接近 0。"""
        docs = [_make_gold(f"d{i}") for i in range(20)]
        # 系统全部输出错误值
        records = []
        for i in range(20):
            record = LLMExtractionRecord(
                document_id=f"d{i}",
                model_identifier="stub",
                prompt_hash="a" * 64,
                success=True,
                output=LLMExtractionOutput(
                    fields=[
                        LLMExtractedField(
                            field_name=CoreFieldName.AMOUNT,
                            values=[
                                LLMExtractedValue(
                                    raw_value="999万元",  # 错误值
                                    normalized_value="9990000.00",
                                )
                            ],
                        )
                    ]
                ),
            )
            records.append(record)
        result = bootstrap_evaluate(
            gold_docs=docs,
            system_records=records,
            system_identifier="zero",
            n_bootstrap=100,
            random_seed=42,
        )
        # amount 字段：precision=0（输出值都不对），recall=0（没有匹配金标）
        amount_f1 = result.field_f1[CoreFieldName.AMOUNT]
        assert amount_f1.point_estimate == pytest.approx(0.0, abs=0.01)
        assert amount_f1.ci_upper <= 0.1

    def test_field_level_ci_populated(self):
        """每字段的 CI 都应被填充。"""
        docs = [_make_gold(f"d{i}") for i in range(10)]
        records = [_make_system(f"d{i}") for i in range(10)]
        result = bootstrap_evaluate(
            gold_docs=docs,
            system_records=records,
            system_identifier="stub",
            n_bootstrap=50,
        )
        assert CoreFieldName.AMOUNT in result.field_precision
        assert CoreFieldName.AMOUNT in result.field_recall
        assert CoreFieldName.AMOUNT in result.field_f1

        amount_f1 = result.field_f1[CoreFieldName.AMOUNT]
        assert isinstance(amount_f1, ConfidenceInterval)
        assert amount_f1.ci_lower <= amount_f1.point_estimate
        assert amount_f1.ci_upper >= amount_f1.point_estimate
        assert amount_f1.bootstrap_samples == 50

    def test_ci_bounds_logical(self):
        """CI 下界不超上界，点估计在区间内或附近。"""
        docs = [_make_gold(f"d{i}", amount_present=(i % 2 == 0)) for i in range(20)]
        records = [_make_system(f"d{i}", correct=(i % 2 == 0)) for i in range(20)]
        result = bootstrap_evaluate(
            gold_docs=docs,
            system_records=records,
            system_identifier="stub",
            n_bootstrap=200,
        )
        for ci in [result.macro_precision, result.macro_recall, result.macro_f1]:
            if ci is None:
                continue
            assert ci.ci_lower <= ci.ci_upper + 1e-9  # 容许浮点误差
            assert 0.0 <= ci.ci_lower <= 1.0 + 1e-9
            assert 0.0 <= ci.ci_upper <= 1.0 + 1e-9


# ============================================================
# 测试套件 4：可复现性
# ============================================================


class TestReproducibility:
    def test_same_seed_same_result(self):
        docs = [_make_gold(f"d{i}") for i in range(15)]
        records = [_make_system(f"d{i}", correct=(i % 3 != 0)) for i in range(15)]

        r1 = bootstrap_evaluate(
            gold_docs=docs, system_records=records,
            system_identifier="s", n_bootstrap=50, random_seed=99,
        )
        r2 = bootstrap_evaluate(
            gold_docs=docs, system_records=records,
            system_identifier="s", n_bootstrap=50, random_seed=99,
        )
        assert r1.macro_f1 is not None
        assert r2.macro_f1 is not None
        assert r1.macro_f1.point_estimate == r2.macro_f1.point_estimate
        assert r1.macro_f1.ci_lower == r2.macro_f1.ci_lower
        assert r1.macro_f1.ci_upper == r2.macro_f1.ci_upper

    def test_different_seed_may_differ(self):
        """不同 seed 结果可能略有差异（统计特性）。"""
        docs = [_make_gold(f"d{i}") for i in range(15)]
        records = [_make_system(f"d{i}", correct=(i % 3 != 0)) for i in range(15)]

        r1 = bootstrap_evaluate(
            gold_docs=docs, system_records=records,
            system_identifier="s", n_bootstrap=50, random_seed=1,
        )
        r2 = bootstrap_evaluate(
            gold_docs=docs, system_records=records,
            system_identifier="s", n_bootstrap=50, random_seed=2,
        )
        # CI 可能略有不同（不强制必须不同，但允许）
        assert r1.macro_f1 is not None
        assert r2.macro_f1 is not None


# ============================================================
# 测试套件 5：异常输入
# ============================================================


class TestBootstrapErrors:
    def test_empty_gold_rejected(self):
        with pytest.raises(ValueError, match="gold_docs"):
            bootstrap_evaluate(
                gold_docs=[], system_records=[],
                system_identifier="s", n_bootstrap=10,
            )

    def test_too_few_bootstrap_rejected(self):
        docs = [_make_gold("d1")]
        with pytest.raises(ValueError, match="n_bootstrap"):
            bootstrap_evaluate(
                gold_docs=docs, system_records=[],
                system_identifier="s", n_bootstrap=1,
            )

    def test_invalid_confidence_level_rejected(self):
        docs = [_make_gold("d1")]
        with pytest.raises(ValueError, match="confidence_level"):
            bootstrap_evaluate(
                gold_docs=docs, system_records=[],
                system_identifier="s", n_bootstrap=10,
                confidence_level=0.0,
            )
        with pytest.raises(ValueError, match="confidence_level"):
            bootstrap_evaluate(
                gold_docs=docs, system_records=[],
                system_identifier="s", n_bootstrap=10,
                confidence_level=1.5,
            )


# ============================================================
# 测试套件 6：异步版本
# ============================================================


class TestBootstrapAsync:
    @pytest.mark.asyncio
    async def test_async_returns_same_result_as_sync(self):
        docs = [_make_gold(f"d{i}") for i in range(10)]
        records = [_make_system(f"d{i}") for i in range(10)]

        sync_result = bootstrap_evaluate(
            gold_docs=docs, system_records=records,
            system_identifier="s", n_bootstrap=30, random_seed=7,
        )
        async_result = await bootstrap_evaluate_async(
            gold_docs=docs, system_records=records,
            system_identifier="s", n_bootstrap=30, random_seed=7,
        )
        assert sync_result.macro_f1 is not None
        assert async_result.macro_f1 is not None
        assert sync_result.macro_f1.point_estimate == async_result.macro_f1.point_estimate
        assert sync_result.macro_f1.ci_lower == async_result.macro_f1.ci_lower

    @pytest.mark.asyncio
    async def test_async_does_not_block_event_loop(self):
        """异步版本应能让其他协程并发执行。"""
        docs = [_make_gold(f"d{i}") for i in range(10)]
        records = [_make_system(f"d{i}") for i in range(10)]

        # 启动 bootstrap 任务
        task = asyncio.create_task(
            bootstrap_evaluate_async(
                gold_docs=docs, system_records=records,
                system_identifier="s", n_bootstrap=50,
            )
        )
        # 同时让另一个协程运行
        counter = 0
        while not task.done():
            await asyncio.sleep(0)
            counter += 1
            if counter > 1000:
                break
        result = await task
        assert result.macro_f1 is not None
        # counter > 1 证明事件循环没被阻塞
        assert counter > 1


# ============================================================
# 测试套件 7：导出
# ============================================================


class TestBootstrapExport:
    def test_to_dict_serializable(self):
        docs = [_make_gold(f"d{i}") for i in range(5)]
        records = [_make_system(f"d{i}") for i in range(5)]
        result = bootstrap_evaluate(
            gold_docs=docs, system_records=records,
            system_identifier="s", n_bootstrap=20,
        )
        d = result.to_dict()
        assert "field_precision" in d
        assert "field_recall" in d
        assert "field_f1" in d
        assert "macro_precision" in d
        assert "macro_recall" in d
        assert "macro_f1" in d
        assert d["bootstrap_samples"] == 20

    def test_confidence_interval_to_dict(self):
        ci = ConfidenceInterval(
            point_estimate=0.85,
            ci_lower=0.75,
            ci_upper=0.92,
            bootstrap_samples=100,
        )
        d = ci.to_dict()
        assert d["point_estimate"] == 0.85
        assert d["ci_lower"] == 0.75
        assert d["ci_upper"] == 0.92
        assert d["bootstrap_samples"] == 100
        assert d["confidence_level"] == 0.95
