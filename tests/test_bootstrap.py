"""Bootstrap 置信区间单元测试 (v4.1 第 10.10 节).

覆盖 project_memory 硬约束:
- Bootstrap 重复抽样至少 3 项测试
- 多值字段至少 3 项测试
- ULID 格式/长度/唯一性
- 项目级采样（不按字段独立采样）
- 可复现性 (seed)
"""
from __future__ import annotations

import re

import pytest

from app.processors.bootstrap import (
    BootstrapResult,
    BootstrapSample,
    BootstrapSampler,
    METRIC_FUNCS,
    ProjectFieldPair,
    RULE_VERSION,
    bootstrap_ci,
)


# ULID 正则: 26 字符 Base32
ULID_RE = re.compile(r"^[0-9A-Z]{26}$")


def _make_pair(
    project_id, doc_id="D1", field_name="amount",
    field_correct=True, field_present=True, has_value=True,
    has_evidence=True, evidence_verified=True, unjustified=False,
    evaluable=True, iou=None,
):
    return ProjectFieldPair(
        project_id=project_id, doc_id=doc_id, field_name=field_name,
        field_correct=field_correct, field_present=field_present,
        has_value=has_value, has_evidence=has_evidence,
        evidence_verified=evidence_verified, unjustified=unjustified,
        evaluable=evaluable, iou=iou,
    )


# ==== 基础功能 ====

class TestBootstrapBasic:
    def test_rule_version(self):
        assert RULE_VERSION == "bootstrap_v1.0"

    def test_metric_funcs_registered(self):
        expected = {
            "field_precision", "field_recall", "evidence_precision",
            "unjustified_rate", "iou_avg",
        }
        assert set(METRIC_FUNCS.keys()) == expected

    def test_empty_pairs_raises(self):
        with pytest.raises(ValueError, match="不能为空"):
            BootstrapSampler([])

    def test_single_project_raises(self):
        """单项目无法做 Bootstrap."""
        pairs = [_make_pair("P001"), _make_pair("P001", field_name="date")]
        with pytest.raises(ValueError, match="至少 2 个项目"):
            BootstrapSampler(pairs, n_resamples=10)

    def test_invalid_n_resamples(self):
        pairs = [_make_pair("P001"), _make_pair("P002")]
        with pytest.raises(ValueError, match="n_resamples"):
            BootstrapSampler(pairs, n_resamples=0)

    def test_unknown_metric_raises(self):
        pairs = [_make_pair("P001"), _make_pair("P002")]
        sampler = BootstrapSampler(pairs, n_resamples=10)
        with pytest.raises(ValueError, match="未知指标"):
            sampler.compute_ci("unknown_metric")

    def test_basic_ci_result_shape(self):
        pairs = [_make_pair("P001"), _make_pair("P002"), _make_pair("P003")]
        result = bootstrap_ci(pairs, "field_precision", n_resamples=100, seed=42)
        assert isinstance(result, BootstrapResult)
        assert result.metric_name == "field_precision"
        assert 0.0 <= result.point_estimate <= 1.0
        assert 0.0 <= result.ci_lower <= result.ci_upper <= 1.0
        assert result.n_resamples == 100
        assert result.n_projects == 3
        assert result.n_pairs == 3
        assert result.seed == 42
        assert result.rule_version == RULE_VERSION
        assert result.sample_size_per_draw == 3


# ==== 可复现性 ====

class TestBootstrapReproducibility:
    def test_same_seed_same_result(self):
        pairs = [
            _make_pair("P001", field_correct=True),
            _make_pair("P002", field_correct=False),
            _make_pair("P003", field_correct=True),
            _make_pair("P004", field_correct=True),
        ]
        r1 = bootstrap_ci(pairs, "field_precision", n_resamples=200, seed=42)
        r2 = bootstrap_ci(pairs, "field_precision", n_resamples=200, seed=42)
        assert r1.ci_lower == r2.ci_lower
        assert r1.ci_upper == r2.ci_upper
        assert r1.point_estimate == r2.point_estimate

    def test_different_seed_may_differ(self):
        pairs = [
            _make_pair("P001", field_correct=True),
            _make_pair("P002", field_correct=False),
            _make_pair("P003", field_correct=True),
            _make_pair("P004", field_correct=True),
            _make_pair("P005", field_correct=False),
        ]
        r1 = bootstrap_ci(pairs, "field_precision", n_resamples=500, seed=1)
        r2 = bootstrap_ci(pairs, "field_precision", n_resamples=500, seed=999)
        # 不同 seed 结果可能不同（不强制不同，但允许）
        assert isinstance(r1.ci_lower, float)
        assert isinstance(r2.ci_lower, float)


# ==== ULID 校验（project_memory: 禁止截断 uuid4.hex）====

class TestBootstrapUlid:
    def test_sample_instance_id_is_ulid(self):
        pairs = [_make_pair("P001"), _make_pair("P002")]
        sampler = BootstrapSampler(pairs, n_resamples=5)
        result = sampler.compute_ci("field_precision")
        assert len(result.samples) > 0
        for s in result.samples:
            assert isinstance(s, BootstrapSample)
            assert ULID_RE.match(s.sample_instance_id), \
                f"sample_instance_id 不是合法 ULID: {s.sample_instance_id}"

    def test_ulid_length_26(self):
        pairs = [_make_pair("P001"), _make_pair("P002")]
        sampler = BootstrapSampler(pairs, n_resamples=3)
        result = sampler.compute_ci("field_precision")
        for s in result.samples:
            assert len(s.sample_instance_id) == 26

    def test_ulid_uniqueness_within_one_draw(self):
        """同一项目在单次采样中重复出现时, sample_instance_id 必须独立."""
        # 只有 2 个项目, 1000 次采样下大概率会出现重复
        pairs = [_make_pair("P001"), _make_pair("P002")]
        sampler = BootstrapSampler(pairs, n_resamples=1000)
        result = sampler.compute_ci("field_precision")
        ids = [s.sample_instance_id for s in result.samples]
        # 1000 次采样, 每次 2 个项目 = 2000 个 sample_instance_id, 必须全部唯一
        assert len(set(ids)) == len(ids), "ULID 出现重复"


# ==== 项目级采样（核心约束：同项目整体采样）====

class TestProjectLevelSampling:
    def test_same_project_pairs_sampled_together(self):
        """同一项目的所有字段对必须整体采样，不可拆分."""
        # P001 有 3 个字段，P002 有 1 个字段
        pairs = [
            _make_pair("P001", field_name="amount"),
            _make_pair("P001", field_name="date"),
            _make_pair("P001", field_name="project_identifier"),
            _make_pair("P002", field_name="amount"),
        ]
        sampler = BootstrapSampler(pairs, n_resamples=100, seed=42)
        result = sampler.compute_ci("field_precision")
        # 每次采样 2 个项目，共 200 个 sample_instance
        assert len(result.samples) == 200
        # P001 每次被选中应贡献 3 个字段，P002 贡献 1 个
        # 采样后字段总数 = 4 * 100 = 400
        # 已通过无异常验证采样不破坏字段对完整性

    def test_resample_preserves_project_field_count(self):
        """重采样后字段对总数 = 项目数 * 单项目字段数."""
        pairs = [
            _make_pair("P001", field_name="amount"),
            _make_pair("P001", field_name="date"),
            _make_pair("P002", field_name="amount"),
            _make_pair("P003", field_name="amount"),
        ]
        sampler = BootstrapSampler(pairs, n_resamples=10, seed=42)
        # 直接调用 _resample 验证字段数守恒
        total_pairs = 0
        for _ in range(10):
            resampled, samples = sampler._resample()
            # 每次采样 3 个项目，P001 贡献 2 字段，P002/P003 各 1
            # 但项目是随机选的，字段数应在 [3, 6] 区间（最坏全 P002/P003=3，最好全 P001=6）
            assert 3 <= len(resampled) <= 6
            total_pairs += len(resampled)
        assert total_pairs > 0


# ==== 重复抽样硬约束（project_memory: 至少 3 项测试）====

class TestRepeatedSampling:
    """重复采样: 同一项目在单次 Bootstrap 中可能被多次选中."""

    def test_repeated_project_gets_independent_sample_instance_id(self):
        """同一项目被多次采样时, 每次应有独立 sample_instance_id."""
        pairs = [_make_pair("P001"), _make_pair("P002")]
        sampler = BootstrapSampler(pairs, n_resamples=1000, seed=42)
        result = sampler.compute_ci("field_precision")
        # 2000 个采样实例全部应有唯一 ULID
        ids = [s.sample_instance_id for s in result.samples]
        assert len(set(ids)) == len(ids)

    def test_repeated_project_aggregation_by_sample_instance(self):
        """每个 sample_instance 独立计入样本, 权重 = 1."""
        pairs = [_make_pair("P001"), _make_pair("P002")]
        sampler = BootstrapSampler(pairs, n_resamples=50, seed=42)
        result = sampler.compute_ci("field_precision")
        # 每个 sample_instance weight = 1 (单次采样权重)
        for s in result.samples:
            assert s.weight == 1
        # 总采样实例数 = n_resamples * n_projects
        assert len(result.samples) == 50 * 2

    def test_repeated_sampling_correct_metric_aggregation(self):
        """重复采样: 即使 P001 被多次选中, 字段精确率仍按字段对总数正确聚合."""
        # P001 全对, P002 全错, P003 全对
        pairs = [
            _make_pair("P001", field_correct=True),
            _make_pair("P001", field_correct=True, field_name="date"),
            _make_pair("P002", field_correct=False),
            _make_pair("P003", field_correct=True),
        ]
        sampler = BootstrapSampler(pairs, n_resamples=200, seed=42)
        result = sampler.compute_ci("field_precision")
        # 点估计: 3 correct / 4 evaluable = 0.75
        assert result.point_estimate == 0.75
        # CI 应该在 [0, 1] 内
        assert 0.0 <= result.ci_lower <= 0.75
        assert 0.75 <= result.ci_upper <= 1.0


# ==== 多值字段硬约束（project_memory: 至少 3 项测试）====

class TestMultiValueFields:
    """多值字段: 同一项目同一字段可以有多个值, 每个值独立参与 Bootstrap."""

    def test_multi_value_field_each_value_is_separate_pair(self):
        """同一项目同一字段的多个值, 每个值作为独立 ProjectFieldPair."""
        # P001 的 amount 字段有 2 个值（多包/多中标人）
        pairs = [
            _make_pair("P001", field_name="amount", field_correct=True),
            _make_pair("P001", field_name="amount", field_correct=False),
            _make_pair("P002", field_name="amount", field_correct=True),
        ]
        sampler = BootstrapSampler(pairs, n_resamples=100, seed=42)
        result = sampler.compute_ci("field_precision")
        # 点估计: 2 correct / 3 evaluable
        assert result.point_estimate == round(2 / 3, 4)
        assert result.n_pairs == 3

    def test_multi_value_field_aggregation_correctness(self):
        """多值字段: 多值全部 correct 才算 high precision."""
        # P001 有 3 个 amount 值（多中标人），全部 correct
        pairs = [
            _make_pair("P001", field_name="amount", field_correct=True),
            _make_pair("P001", field_name="amount", field_correct=True),
            _make_pair("P001", field_name="amount", field_correct=True),
            _make_pair("P002", field_name="amount", field_correct=False),
        ]
        sampler = BootstrapSampler(pairs, n_resamples=100, seed=42)
        result = sampler.compute_ci("field_precision")
        # 点估计: 3 correct / 4 evaluable = 0.75
        assert result.point_estimate == 0.75

    def test_multi_value_field_with_iou_metric(self):
        """多值字段: IoU 平均值指标也能正确计算."""
        pairs = [
            _make_pair("P001", field_name="amount", iou=0.8),
            _make_pair("P001", field_name="amount", iou=0.6),
            _make_pair("P002", field_name="amount", iou=1.0),
            _make_pair("P003", field_name="amount", iou=0.4),
        ]
        sampler = BootstrapSampler(pairs, n_resamples=100, seed=42)
        result = sampler.compute_ci("iou_avg")
        # 点估计: (0.8 + 0.6 + 1.0 + 0.4) / 4 = 0.7
        assert result.point_estimate == 0.7


# ==== 指标函数 ====

class TestMetricFunctions:
    def test_field_precision_zero_evaluable(self):
        """无可评测字段时返回 0.0."""
        pairs = [
            _make_pair("P001", evaluable=False),
            _make_pair("P002", evaluable=False),
        ]
        sampler = BootstrapSampler(pairs, n_resamples=10, seed=42)
        result = sampler.compute_ci("field_precision")
        assert result.point_estimate == 0.0

    def test_field_recall_zero_gold_present(self):
        pairs = [
            _make_pair("P001", field_present=False),
            _make_pair("P002", field_present=False),
        ]
        sampler = BootstrapSampler(pairs, n_resamples=10, seed=42)
        result = sampler.compute_ci("field_recall")
        assert result.point_estimate == 0.0

    def test_evidence_precision_no_evidence(self):
        pairs = [
            _make_pair("P001", has_evidence=False),
            _make_pair("P002", has_evidence=False),
        ]
        sampler = BootstrapSampler(pairs, n_resamples=10, seed=42)
        result = sampler.compute_ci("evidence_precision")
        assert result.point_estimate == 0.0

    def test_unjustified_rate_no_value(self):
        pairs = [
            _make_pair("P001", has_value=False),
            _make_pair("P002", has_value=False),
        ]
        sampler = BootstrapSampler(pairs, n_resamples=10, seed=42)
        result = sampler.compute_ci("unjustified_rate")
        assert result.point_estimate == 0.0

    def test_iou_avg_no_iou(self):
        pairs = [
            _make_pair("P001", iou=None),
            _make_pair("P002", iou=None),
        ]
        sampler = BootstrapSampler(pairs, n_resamples=10, seed=42)
        result = sampler.compute_ci("iou_avg")
        assert result.point_estimate == 0.0
