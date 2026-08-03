"""v4.1 §10.3 金标字段状态测试。

覆盖：
- GOLD_FIELD_STATUSES 6 种状态完整性
- _classify_gold_status 分类逻辑
- absent/not_applicable 等价处理
- attachment_only correct=None
- unreadable correct=None
"""
from __future__ import annotations

import pytest

from app.llm.extraction_schemas import GOLD_FIELD_STATUSES
from scripts.eval_ablation import (
    GoldField,
    GroupResult,
    _classify_gold_status,
)


# ========== 枚举完整性测试 ==========


class TestGoldFieldStatuses:
    """GOLD_FIELD_STATUSES 6 种状态。"""

    def test_six_statuses(self):
        """必须有 6 种状态。"""
        assert len(GOLD_FIELD_STATUSES) == 6

    def test_required_statuses_present(self):
        """必需的 6 种状态都在。"""
        for status in [
            "present",
            "absent",
            "not_applicable",
            "ambiguous",
            "attachment_only",
            "unreadable",
        ]:
            assert status in GOLD_FIELD_STATUSES

    def test_descriptions_non_empty(self):
        """每种状态描述非空。"""
        for status, desc in GOLD_FIELD_STATUSES.items():
            assert isinstance(desc, str)
            assert len(desc) > 0


# ========== _classify_gold_status 测试 ==========


class TestClassifyGoldStatus:
    """金标状态分类逻辑。"""

    @pytest.mark.parametrize(
        "status,expected",
        [
            ("present", "should_have_value"),
            ("ambiguous", "should_have_value"),
            ("multi_value", "should_have_value"),
            ("absent", "should_not_have_value"),
            ("not_applicable", "should_not_have_value"),
            ("attachment_only", "attachment_only"),
            ("unreadable", "unreadable"),
        ],
    )
    def test_classification(self, status, expected):
        """7 种状态正确分类到 4 类口径。"""
        assert _classify_gold_status(status) == expected

    def test_unknown_status_defaults_to_should_have_value(self):
        """未知状态默认按 should_have_value 处理。"""
        assert _classify_gold_status("unknown_status") == "should_have_value"


# ========== correct 判定逻辑测试 ==========


class TestCorrectJudgment:
    """不同金标状态下的 correct 判定。"""

    def test_absent_no_value_correct(self):
        """absent 状态，系统未输出值 → correct=True。"""
        result = GroupResult(
            group="A", doc_id="d1", field_name="winner_name",
            gold_status="absent", pred_status="absent",
            has_value=False, has_evidence=False, evidence_verified=False,
            field_validated=False, unjustified=False, correct=True,
        )
        assert result.correct is True

    def test_not_applicable_no_value_correct(self):
        """not_applicable 状态，系统未输出值 → correct=True（与 absent 等价）。"""
        result = GroupResult(
            group="A", doc_id="d1", field_name="winner_name",
            gold_status="not_applicable", pred_status="absent",
            has_value=False, has_evidence=False, evidence_verified=False,
            field_validated=False, unjustified=False, correct=True,
        )
        assert result.correct is True

    def test_attachment_only_correct_is_none(self):
        """attachment_only 状态 → correct=None（无法判定）。"""
        result = GroupResult(
            group="A", doc_id="d1", field_name="amount",
            gold_status="attachment_only", pred_status="present",
            has_value=True, has_evidence=False, evidence_verified=False,
            field_validated=False, unjustified=True, correct=None,
        )
        assert result.correct is None

    def test_unreadable_correct_is_none(self):
        """unreadable 状态 → correct=None（无法判定）。"""
        result = GroupResult(
            group="A", doc_id="d1", field_name="publish_date",
            gold_status="unreadable", pred_status="present",
            has_value=True, has_evidence=False, evidence_verified=False,
            field_validated=False, unjustified=True, correct=None,
        )
        assert result.correct is None
