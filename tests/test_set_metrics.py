"""set_metrics.py unit tests (v4.1 sec 7.4)."""

from __future__ import annotations

import pytest

from app.eval.set_metrics import (
    MultiValueMetrics,
    compute_multi_value_field_metrics,
    is_multi_value_field,
    normalize_value,
    set_level_precision_recall_f1,
)


class TestNormalizeValue:
    """normalize_value() tests."""

    def test_none_returns_empty(self) -> None:
        assert normalize_value(None) == ""

    def test_empty_string_returns_empty(self) -> None:
        assert normalize_value("") == ""

    def test_whitespace_only_returns_empty(self) -> None:
        assert normalize_value("   ") == ""

    def test_trims_whitespace(self) -> None:
        assert normalize_value("  hello  ") == "hello"

    def test_lowercases_english(self) -> None:
        assert normalize_value("HelloWorld") == "helloworld"

    def test_fullwidth_to_halfwidth_digits(self) -> None:
        # Fullwidth digits 0-9: U+FF10-U+FF19
        assert normalize_value("\uff10\uff11\uff12") == "012"

    def test_fullwidth_to_halfwidth_letters(self) -> None:
        # Fullwidth A: U+FF21
        assert normalize_value("\uff21\uff22\uff23") == "abc"

    def test_fullwidth_space_to_halfwidth(self) -> None:
        assert normalize_value("\u3000hello\u3000") == "hello"

    def test_drops_thousands_separator_pure_number(self) -> None:
        assert normalize_value("1,234.56") == "1234.56"

    def test_preserves_chinese_chars(self) -> None:
        assert normalize_value("北京大学") == "北京大学"

    def test_preserves_units(self) -> None:
        # "100万元" is not pure numeric, so comma (if any) preserved
        assert normalize_value("100万元") == "100万元"

    def test_preserves_mixed_content(self) -> None:
        assert normalize_value("ABC公司") == "abc公司"


class TestSetLevelPRF:
    """set_level_precision_recall_f1() tests."""

    def test_perfect_match(self) -> None:
        p, r, f1 = set_level_precision_recall_f1({"a", "b"}, {"a", "b"})
        assert p == 1.0
        assert r == 1.0
        assert f1 == 1.0

    def test_partial_match(self) -> None:
        # pred={a,b,c}, gold={b,c,d} -> TP=2, FP=1, FN=1
        p, r, f1 = set_level_precision_recall_f1({"a", "b", "c"}, {"b", "c", "d"})
        assert p == pytest.approx(2 / 3)
        assert r == pytest.approx(2 / 3)
        assert f1 == pytest.approx(2 / 3)

    def test_no_overlap(self) -> None:
        p, r, f1 = set_level_precision_recall_f1({"a", "b"}, {"c", "d"})
        assert p == 0.0
        assert r == 0.0
        assert f1 == 0.0

    def test_pred_subset_of_gold(self) -> None:
        # pred={a}, gold={a,b,c} -> TP=1, FP=0, FN=2
        p, r, f1 = set_level_precision_recall_f1({"a"}, {"a", "b", "c"})
        assert p == 1.0
        assert r == pytest.approx(1 / 3)
        assert f1 == pytest.approx(0.5)

    def test_pred_superset_of_gold(self) -> None:
        # pred={a,b,c}, gold={a} -> TP=1, FP=2, FN=0
        p, r, f1 = set_level_precision_recall_f1({"a", "b", "c"}, {"a"})
        assert p == pytest.approx(1 / 3)
        assert r == 1.0
        assert f1 == pytest.approx(0.5)

    def test_both_empty_returns_ones(self) -> None:
        p, r, f1 = set_level_precision_recall_f1(set(), set())
        assert p == 1.0
        assert r == 1.0
        assert f1 == 1.0

    def test_pred_empty_gold_nonempty(self) -> None:
        p, r, f1 = set_level_precision_recall_f1(set(), {"a"})
        assert p == 0.0
        assert r == 0.0
        assert f1 == 0.0

    def test_pred_nonempty_gold_empty(self) -> None:
        # All predictions are false positives
        p, r, f1 = set_level_precision_recall_f1({"a"}, set())
        assert p == 0.0
        assert r == 1.0  # convention: all 0 golds are "predicted"
        assert f1 == 0.0

    def test_single_element_match(self) -> None:
        p, r, f1 = set_level_precision_recall_f1({"a"}, {"a"})
        assert p == 1.0
        assert r == 1.0
        assert f1 == 1.0

    def test_duplicates_in_sets_dont_affect_result(self) -> None:
        # Sets naturally deduplicate
        p1, r1, f1_1 = set_level_precision_recall_f1({"a", "a"}, {"a"})
        p2, r2, f1_2 = set_level_precision_recall_f1({"a"}, {"a"})
        assert (p1, r1, f1_1) == (p2, r2, f1_2)


class TestComputeMultiValueFieldMetrics:
    """compute_multi_value_field_metrics() tests."""

    def test_perfect_match(self) -> None:
        m = compute_multi_value_field_metrics(["a", "b"], ["a", "b"])
        assert m.precision == 1.0
        assert m.recall == 1.0
        assert m.f1 == 1.0
        assert m.true_positive == 2
        assert m.false_positive == 0
        assert m.false_negative == 0
        assert m.pred_count == 2
        assert m.gold_count == 2

    def test_partial_match(self) -> None:
        m = compute_multi_value_field_metrics(["a", "b", "c"], ["b", "c", "d"])
        assert m.true_positive == 2
        assert m.false_positive == 1
        assert m.false_negative == 1
        assert m.pred_count == 3
        assert m.gold_count == 3

    def test_none_values_filtered(self) -> None:
        m = compute_multi_value_field_metrics(["a", None, ""], ["a", None])
        assert m.pred_count == 1
        assert m.gold_count == 1
        assert m.precision == 1.0

    def test_whitespace_values_filtered(self) -> None:
        m = compute_multi_value_field_metrics(["  ", "a"], ["a", "  "])
        assert m.pred_count == 1
        assert m.gold_count == 1
        assert m.f1 == 1.0

    def test_none_inputs(self) -> None:
        m = compute_multi_value_field_metrics(None, None)
        assert m.precision == 1.0
        assert m.recall == 1.0
        assert m.f1 == 1.0
        assert m.pred_count == 0
        assert m.gold_count == 0

    def test_normalization_applied(self) -> None:
        # Fullwidth vs halfwidth should match
        m = compute_multi_value_field_metrics(
            ["\uff21\uff22"],  # Fullwidth AB
            ["AB"],
        )
        assert m.true_positive == 1
        assert m.f1 == 1.0

    def test_case_insensitive_match(self) -> None:
        m = compute_multi_value_field_metrics(["Hello", "World"], ["hello", "world"])
        assert m.f1 == 1.0

    def test_duplicates_deduplicated(self) -> None:
        m = compute_multi_value_field_metrics(["a", "a", "b"], ["a", "b"])
        assert m.pred_count == 2
        assert m.gold_count == 2
        assert m.f1 == 1.0

    def test_to_dict(self) -> None:
        m = compute_multi_value_field_metrics(["a"], ["a", "b"])
        d = m.to_dict()
        assert "precision" in d
        assert "recall" in d
        assert "f1" in d
        assert "true_positive" in d
        assert "false_positive" in d
        assert "false_negative" in d
        assert d["true_positive"] == 1
        assert d["false_negative"] == 1

    def test_chinese_winner_names(self) -> None:
        """Real-world scenario: multiple Chinese winner names."""
        m = compute_multi_value_field_metrics(
            ["中国建筑有限公司", "中铁隧道局集团"],
            ["中国建筑有限公司", "中铁隧道局集团", "中交第一公路工程局"],
        )
        # pred=2/3 gold, all pred correct
        assert m.precision == 1.0
        assert m.recall == pytest.approx(2 / 3)
        assert m.false_negative == 1

    def test_amounts_with_units(self) -> None:
        """Amounts with units should be compared as-is (no semantic normalization)."""
        m = compute_multi_value_field_metrics(
            ["128.50万元", "256.00万元"],
            ["128.50万元", "256.00万元"],
        )
        assert m.f1 == 1.0

    def test_thousands_separator_normalized(self) -> None:
        """1,234.56 and 1234.56 should match."""
        m = compute_multi_value_field_metrics(["1,234.56"], ["1234.56"])
        assert m.f1 == 1.0


class TestIsMultiValueField:
    """is_multi_value_field() tests."""

    def test_winner_name_is_multi_value(self) -> None:
        assert is_multi_value_field("winner_name") is True

    def test_amount_is_multi_value(self) -> None:
        assert is_multi_value_field("amount") is True

    def test_project_identifier_is_multi_value(self) -> None:
        assert is_multi_value_field("project_identifier") is True

    def test_bid_deadline_is_multi_value(self) -> None:
        assert is_multi_value_field("bid_deadline") is True

    def test_purchaser_name_is_single_value(self) -> None:
        assert is_multi_value_field("purchaser_name") is False

    def test_publish_date_is_single_value(self) -> None:
        assert is_multi_value_field("publish_date") is False

    def test_unknown_field_is_single_value(self) -> None:
        assert is_multi_value_field("unknown_field") is False

    def test_empty_string_is_single_value(self) -> None:
        assert is_multi_value_field("") is False


class TestMultiValueMetricsDataclass:
    """MultiValueMetrics dataclass tests."""

    def test_dataclass_fields(self) -> None:
        m = MultiValueMetrics(
            precision=0.5,
            recall=0.75,
            f1=0.6,
            pred_count=4,
            gold_count=3,
            true_positive=2,
            false_positive=2,
            false_negative=1,
        )
        assert m.precision == 0.5
        assert m.recall == 0.75
        assert m.f1 == 0.6
        assert m.pred_count == 4
        assert m.gold_count == 3
        assert m.true_positive == 2
        assert m.false_positive == 2
        assert m.false_negative == 1

    def test_to_dict_has_all_fields(self) -> None:
        m = MultiValueMetrics(
            precision=1.0, recall=1.0, f1=1.0,
            pred_count=1, gold_count=1,
            true_positive=1, false_positive=0, false_negative=0,
        )
        d = m.to_dict()
        assert len(d) == 8
        assert set(d.keys()) == {
            "precision", "recall", "f1",
            "pred_count", "gold_count",
            "true_positive", "false_positive", "false_negative",
        }
