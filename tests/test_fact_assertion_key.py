"""事实断言键单元测试 (v4.1 第 6.5 节).

覆盖:
- 不可比场景: project_id / field_name / amount_type / lot_id / entity_role 不同
- 可比场景: 完全相同 / notice_type 不同 / semantic_role 不同 / effective_time 不同
- budget vs award 不可比 (核心规则)
- 冲突原因字符串
"""
from __future__ import annotations

import pytest

from app.processors.fact_assertion_key import (
    FactAssertionKey,
    RULE_VERSION,
    assert_keys_compatible,
    build_assertion_key,
)


class TestRuleVersion:
    def test_rule_version(self):
        assert RULE_VERSION == "fact_assertion_key_v1.0"


class TestFactAssertionKeyConstruction:
    def test_build_assertion_key(self):
        k = build_assertion_key(
            project_id="P001", notice_type="award", field_name="amount",
            amount_type="award", lot_id="1",
        )
        assert k.project_id == "P001"
        assert k.field_name == "amount"
        assert k.amount_type == "award"
        assert k.lot_id == "1"

    def test_frozen_dataclass(self):
        """FactAssertionKey 应为不可变."""
        k = build_assertion_key("P001", "award", "amount")
        with pytest.raises(Exception):
            k.project_id = "P002"

    def test_optional_fields_default_none(self):
        k = build_assertion_key("P001", "award", "amount")
        assert k.semantic_role is None
        assert k.amount_type is None
        assert k.lot_id is None
        assert k.effective_time is None
        assert k.entity_role is None


# ==== 可比性判定 ====

class TestIsCompatible:
    def test_identical_keys_compatible(self):
        k1 = build_assertion_key("P001", "award", "amount", amount_type="award")
        k2 = build_assertion_key("P001", "award", "amount", amount_type="award")
        assert k1.is_compatible(k2) is True

    def test_different_project_not_compatible(self):
        k1 = build_assertion_key("P001", "award", "amount")
        k2 = build_assertion_key("P002", "award", "amount")
        assert k1.is_compatible(k2) is False

    def test_different_field_not_compatible(self):
        k1 = build_assertion_key("P001", "award", "amount")
        k2 = build_assertion_key("P001", "award", "date")
        assert k1.is_compatible(k2) is False

    def test_budget_vs_award_not_compatible(self):
        """核心规则: budget vs award 不可比."""
        k1 = build_assertion_key("P001", "tender", "amount", amount_type="budget")
        k2 = build_assertion_key("P001", "award", "amount", amount_type="award")
        assert k1.is_compatible(k2) is False

    def test_different_lot_not_compatible(self):
        k1 = build_assertion_key("P001", "award", "amount", lot_id="1")
        k2 = build_assertion_key("P001", "award", "amount", lot_id="2")
        assert k1.is_compatible(k2) is False

    def test_different_entity_role_not_compatible(self):
        k1 = build_assertion_key("P001", "award", "winner", entity_role="winner")
        k2 = build_assertion_key("P001", "award", "winner", entity_role="purchaser")
        assert k1.is_compatible(k2) is False

    def test_none_amount_type_compatible_with_none(self):
        """两个 amount_type 都是 None 时可比（同字段同项目）."""
        k1 = build_assertion_key("P001", "award", "date")
        k2 = build_assertion_key("P001", "award", "date")
        assert k1.is_compatible(k2) is True


# ==== 可比但允许不同的字段 ====

class TestCompatibleWithDifferences:
    def test_different_notice_type_compatible(self):
        """不同 notice_type 但同项目同字段可比 (版本差异)."""
        k1 = build_assertion_key("P001", "tender", "amount", amount_type="budget")
        k2 = build_assertion_key("P001", "correction", "amount", amount_type="budget")
        assert k1.is_compatible(k2) is True

    def test_different_semantic_role_compatible(self):
        k1 = build_assertion_key("P001", "award", "amount", semantic_role="primary")
        k2 = build_assertion_key("P001", "award", "amount", semantic_role="secondary")
        assert k1.is_compatible(k2) is True

    def test_different_effective_time_compatible(self):
        k1 = build_assertion_key("P001", "award", "amount", effective_time="2026-01-01")
        k2 = build_assertion_key("P001", "award", "amount", effective_time="2026-02-01")
        assert k1.is_compatible(k2) is True


# ==== 多值字段硬约束（project_memory: 至少 3 项测试）====

class TestMultiValueFields:
    """多值字段: 同一项目同一字段可能有多个值（多包/多中标人）."""

    def test_multi_lot_same_field_different_lot_not_compatible(self):
        """同一项目同一字段, 不同分包的值不可比."""
        k1 = build_assertion_key("P001", "award", "amount", amount_type="award", lot_id="1")
        k2 = build_assertion_key("P001", "award", "amount", amount_type="award", lot_id="2")
        assert k1.is_compatible(k2) is False

    def test_multi_winner_same_field_different_entity_not_compatible(self):
        """同一项目同一字段, 不同中标人不可比."""
        k1 = build_assertion_key(
            "P001", "award", "winner_name",
            entity_role="winner", lot_id="1",
        )
        k2 = build_assertion_key(
            "P001", "award", "winner_name",
            entity_role="consortium_member", lot_id="1",
        )
        assert k1.is_compatible(k2) is False

    def test_multi_value_same_lot_same_entity_compatible(self):
        """同一项目同一字段同一分包同一实体, 多个值可比 (联合体多成员场景特殊处理)."""
        k1 = build_assertion_key(
            "P001", "award", "amount",
            amount_type="award", lot_id="1", entity_role="winner",
        )
        k2 = build_assertion_key(
            "P001", "award", "amount",
            amount_type="award", lot_id="1", entity_role="winner",
        )
        assert k1.is_compatible(k2) is True


# ==== 冲突原因 ====

class TestConflictReason:
    def test_no_conflict_returns_none(self):
        k1 = build_assertion_key("P001", "award", "amount", amount_type="award")
        k2 = build_assertion_key("P001", "award", "amount", amount_type="award")
        assert k1.conflict_reason(k2) is None

    def test_different_project_reason(self):
        k1 = build_assertion_key("P001", "award", "amount")
        k2 = build_assertion_key("P002", "award", "amount")
        assert k1.conflict_reason(k2) == "different_project"

    def test_different_field_reason(self):
        k1 = build_assertion_key("P001", "award", "amount")
        k2 = build_assertion_key("P001", "award", "date")
        assert k1.conflict_reason(k2) == "different_field"

    def test_different_amount_type_reason(self):
        k1 = build_assertion_key("P001", "award", "amount", amount_type="award")
        k2 = build_assertion_key("P001", "award", "amount", amount_type="budget")
        reason = k1.conflict_reason(k2)
        assert reason is not None
        assert "different_amount_type" in reason
        assert "award" in reason
        assert "budget" in reason

    def test_different_lot_reason(self):
        k1 = build_assertion_key("P001", "award", "amount", lot_id="1")
        k2 = build_assertion_key("P001", "award", "amount", lot_id="2")
        reason = k1.conflict_reason(k2)
        assert reason is not None
        assert "different_lot" in reason

    def test_different_entity_role_reason(self):
        k1 = build_assertion_key("P001", "award", "name", entity_role="winner")
        k2 = build_assertion_key("P001", "award", "name", entity_role="purchaser")
        reason = k1.conflict_reason(k2)
        assert reason is not None
        assert "different_entity_role" in reason


# ==== assert_keys_compatible ====

class TestAssertKeysCompatible:
    def test_compatible_returns_true(self):
        k1 = build_assertion_key("P001", "award", "amount", amount_type="award")
        k2 = build_assertion_key("P001", "award", "amount", amount_type="award")
        assert assert_keys_compatible(k1, k2) is True

    def test_incompatible_raises(self):
        k1 = build_assertion_key("P001", "tender", "amount", amount_type="budget")
        k2 = build_assertion_key("P001", "award", "amount", amount_type="award")
        with pytest.raises(ValueError, match="断言键不可比"):
            assert_keys_compatible(k1, k2)


# ==== is_identical ====

class TestIsIdentical:
    def test_identical_keys(self):
        k1 = build_assertion_key("P001", "award", "amount", amount_type="award", lot_id="1")
        k2 = build_assertion_key("P001", "award", "amount", amount_type="award", lot_id="1")
        assert k1.is_identical(k2) is True

    def test_different_notice_type_not_identical(self):
        """不同 notice_type 可比但不完全相同."""
        k1 = build_assertion_key("P001", "tender", "amount", amount_type="budget")
        k2 = build_assertion_key("P001", "correction", "amount", amount_type="budget")
        assert k1.is_compatible(k2) is True
        assert k1.is_identical(k2) is False
