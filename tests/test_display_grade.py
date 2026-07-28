"""展示等级单元测试 (v4.1 第 6.6 节).

覆盖:
- HIGH/REVIEW/LOW 三级判定
- 单一官方原始来源 + 直接证据 = HIGH (核心规则)
- 推导字段一律 REVIEW
- 无证据/冲突/来源未知 一律 LOW
- 输入校验
"""
from __future__ import annotations

import pytest

from app.processors.display_grade import (
    CROSS_VERIFY_STATUSES,
    DisplayGrade,
    OFFICIAL_SOURCES,
    RULE_VERSION,
    SOURCE_QUALITIES,
    SUPPORT_LEVELS,
    compute_display_grade,
    validate_inputs,
)


class TestRuleVersion:
    def test_rule_version(self):
        assert RULE_VERSION == "display_grade_v1.0"


class TestEnums:
    def test_display_grade_values(self):
        assert DisplayGrade.HIGH.value == "high"
        assert DisplayGrade.REVIEW.value == "review"
        assert DisplayGrade.LOW.value == "low"

    def test_support_levels_complete(self):
        expected = {
            "direct", "equivalent", "inferred",
            "unsupported", "contradicted",
        }
        assert SUPPORT_LEVELS == expected

    def test_source_qualities_complete(self):
        expected = {
            "official_original", "official_repost", "authorized_original",
            "commercial_repost", "index_only", "unknown",
        }
        assert SOURCE_QUALITIES == expected

    def test_cross_verify_statuses_complete(self):
        expected = {
            "independent", "consistent_unknown", "same_origin",
            "version_difference", "conflict", "single_source",
        }
        assert CROSS_VERIFY_STATUSES == expected

    def test_official_sources_subset(self):
        assert OFFICIAL_SOURCES.issubset(SOURCE_QUALITIES)


# ==== LOW 判定 ====

class TestDisplayGradeLow:
    @pytest.mark.parametrize("support", ["unsupported", "contradicted"])
    def test_unsupported_or_contradicted_is_low(self, support):
        grade = compute_display_grade(support, "official_original", "independent")
        assert grade == DisplayGrade.LOW

    def test_conflict_is_low(self):
        grade = compute_display_grade("direct", "official_original", "conflict")
        assert grade == DisplayGrade.LOW

    def test_unknown_source_is_low(self):
        grade = compute_display_grade("direct", "unknown", "independent")
        assert grade == DisplayGrade.LOW

    def test_low_priority_over_review(self):
        """LOW 优先于 REVIEW: unsupported + 商业转载仍为 LOW."""
        grade = compute_display_grade("unsupported", "commercial_repost", "same_origin")
        assert grade == DisplayGrade.LOW


# ==== REVIEW 判定 ====

class TestDisplayGradeReview:
    def test_inferred_is_review(self):
        """推导字段一律 REVIEW (即使来源官方、独立验证)."""
        grade = compute_display_grade("inferred", "official_original", "independent")
        assert grade == DisplayGrade.REVIEW

    def test_commercial_repost_is_review(self):
        grade = compute_display_grade("direct", "commercial_repost", "independent")
        assert grade == DisplayGrade.REVIEW

    def test_index_only_is_review(self):
        grade = compute_display_grade("direct", "index_only", "independent")
        assert grade == DisplayGrade.REVIEW

    def test_same_origin_is_review(self):
        """同源转载（独立性不足）一律 REVIEW."""
        grade = compute_display_grade("direct", "official_original", "same_origin")
        assert grade == DisplayGrade.REVIEW

    def test_inferred_overrides_official_source(self):
        """inferred 即使是官方来源, 仍是 REVIEW (不能 HIGH)."""
        grade = compute_display_grade("inferred", "official_original", "single_source")
        assert grade == DisplayGrade.REVIEW


# ==== HIGH 判定（核心规则）====

class TestDisplayGradeHigh:
    @pytest.mark.parametrize("support", ["direct", "equivalent"])
    @pytest.mark.parametrize("source", ["official_original", "official_repost", "authorized_original"])
    @pytest.mark.parametrize("cross", ["independent", "consistent_unknown", "single_source", "version_difference"])
    def test_high_grade_combinations(self, support, source, cross):
        """direct/equivalent + 官方来源 + 无冲突 = HIGH."""
        grade = compute_display_grade(support, source, cross)
        assert grade == DisplayGrade.HIGH, \
            f"预期 HIGH: {support}+{source}+{cross}, 实际 {grade}"

    def test_single_source_official_original_direct_is_high(self):
        """核心规则: 单一官方原始来源 + 直接证据 = HIGH.

        v4.1 第 6.4 节: 独立跨源验证是增强项, 非必要条件.
        """
        grade = compute_display_grade("direct", "official_original", "single_source")
        assert grade == DisplayGrade.HIGH

    def test_version_difference_with_official_source_is_high(self):
        """版本差异（如更正公告）+ 官方来源 + 直接证据 = HIGH."""
        grade = compute_display_grade("direct", "official_original", "version_difference")
        assert grade == DisplayGrade.HIGH


# ==== 兜底 ====

class TestDisplayGradeFallback:
    def test_unknown_combination_returns_review(self):
        """兜底: 未匹配任何规则的组合返回 REVIEW (待人工复核)."""
        # 构造一个不会命中任何规则的组合
        # direct + unknown_source (LOW 命中) - 已被 LOW 覆盖
        # direct + official + conflict (LOW 命中) - 已被 LOW 覆盖
        # direct + commercial_repost (REVIEW 命中) - 已被 REVIEW 覆盖
        # 这里测试: equivalent + 官方来源 + 同源转载 -> REVIEW (走 REVIEW same_origin 分支)
        grade = compute_display_grade("equivalent", "official_original", "same_origin")
        assert grade == DisplayGrade.REVIEW


# ==== 输入校验 ====

class TestValidateInputs:
    def test_valid_inputs_no_errors(self):
        errors = validate_inputs("direct", "official_original", "single_source")
        assert errors == []

    def test_invalid_support_level(self):
        errors = validate_inputs("invalid", "official_original", "independent")
        assert len(errors) == 1
        assert "support_level" in errors[0]

    def test_invalid_source_quality(self):
        errors = validate_inputs("direct", "invalid", "independent")
        assert len(errors) == 1
        assert "source_quality" in errors[0]

    def test_invalid_cross_verify(self):
        errors = validate_inputs("direct", "official_original", "invalid")
        assert len(errors) == 1
        assert "cross_verify_status" in errors[0]

    def test_all_invalid(self):
        errors = validate_inputs("a", "b", "c")
        assert len(errors) == 3
