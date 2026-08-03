"""Set-level Precision / Recall / F1 for multi-value fields (v4.1 sec 7.4).

v4.1 sec 7.4 requirements:
- Multi-value fields (multiple winners, consortium partners, multiple lot amounts,
  multiple project identifiers, multiple bid deadlines) MUST be stored as arrays,
  not collapsed into a single string.
- Evaluation MUST use set-level Precision, Recall and F1 for multi-value fields.

This module provides:
- set_level_precision_recall_f1(pred_set, gold_set) -> (P, R, F1)
- compute_multi_value_field_metrics(pred_values, gold_values) -> MultiValueMetrics
- normalize_value(value) -> str  normalize string for set comparison
- is_multi_value_field(field_name) -> bool

Design principles:
- Does NOT modify eval scripts: this module is a utility, eval scripts call it
- Set-level: deduplicate values, order-independent
- Tolerant: ignore None / empty string / pure whitespace
- Normalization: trim whitespace, lowercase, fullwidth->halfwidth, drop thousands separator
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


def _build_fullwidth_table() -> dict[int, str]:
    """Build fullwidth -> halfwidth mapping table."""
    table = {}
    # Fullwidth space U+3000 -> halfwidth space
    table[0x3000] = 0x20
    # Fullwidth chars U+FF01..U+FF5E -> halfwidth U+0021..U+007E
    for i in range(0xFF01, 0xFF5F):
        table[i] = i - 0xFEE0
    return table


_FULLWIDTH_TO_HALFWIDTH_TABLE = _build_fullwidth_table()


def normalize_value(value: str | None) -> str:
    """Normalize a single field value for set comparison.

    - None / empty string / pure whitespace -> "" (filtered out in set comparison)
    - Trim leading/trailing whitespace
    - Fullwidth chars -> halfwidth (digits, letters, punctuation)
    - English letters lowercased
    - Pure numeric strings: drop thousands separators ("1,234.56" -> "1234.56")

    Note: This function does NOT do semantic normalization (e.g., "wan yuan" -> "yuan").
    That requires amount-specific logic. This function only does string normalization
    sufficient for set-level comparison.
    """
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    # Fullwidth -> halfwidth
    s = s.translate(_FULLWIDTH_TO_HALFWIDTH_TABLE)
    # Lowercase English
    s = s.lower()
    # Drop thousands separator for pure numeric strings (with optional decimal point)
    if re.fullmatch(r"[\d,]+\.?\d*", s):
        s = s.replace(",", "")
    return s


def _to_normalized_set(values: Iterable[str | None]) -> set[str]:
    """Convert value list to normalized non-empty set."""
    result = set()
    for v in values:
        n = normalize_value(v)
        if n:
            result.add(n)
    return result


@dataclass
class MultiValueMetrics:
    """Multi-value field set-level evaluation result."""

    precision: float
    recall: float
    f1: float
    pred_count: int
    gold_count: int
    true_positive: int
    false_positive: int
    false_negative: int

    def to_dict(self) -> dict[str, float | int]:
        return {
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "pred_count": self.pred_count,
            "gold_count": self.gold_count,
            "true_positive": self.true_positive,
            "false_positive": self.false_positive,
            "false_negative": self.false_negative,
        }


def set_level_precision_recall_f1(
    pred_set: set[str], gold_set: set[str]
) -> tuple[float, float, float]:
    """Set-level Precision / Recall / F1.

    Args:
        pred_set: prediction set (normalized, non-empty)
        gold_set: gold set (normalized, non-empty)

    Returns:
        (precision, recall, f1)
        - Both empty -> (1.0, 1.0, 1.0) (convention: correctly predicted "no value")
        - pred empty, gold non-empty -> (0.0, 0.0, 0.0)
        - pred non-empty, gold empty -> (0.0, 1.0, 0.0)
          (P=0 since no TP, R=1 since all 0 golds are predicted, F1=0.
          This is the standard set-level P/R/F1 definition.)
    """
    if not pred_set and not gold_set:
        return 1.0, 1.0, 1.0
    if not pred_set:
        return 0.0, 0.0, 0.0
    if not gold_set:
        # pred non-empty but gold empty: all predictions are FP
        return 0.0, 1.0, 0.0

    tp = len(pred_set & gold_set)
    fp = len(pred_set - gold_set)
    fn = len(gold_set - pred_set)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )
    return precision, recall, f1


def compute_multi_value_field_metrics(
    pred_values: list[str | None] | None,
    gold_values: list[str | None] | None,
) -> MultiValueMetrics:
    """Compute set-level P/R/F1 for a multi-value field.

    Args:
        pred_values: prediction value list (e.g., 3 winner names from LLM)
        gold_values: gold value list (annotated multi-values)

    Returns:
        MultiValueMetrics dataclass
    """
    pred_set = _to_normalized_set(pred_values or [])
    gold_set = _to_normalized_set(gold_values or [])

    p, r, f1 = set_level_precision_recall_f1(pred_set, gold_set)

    tp = len(pred_set & gold_set)
    fp = len(pred_set - gold_set)
    fn = len(gold_set - pred_set)

    return MultiValueMetrics(
        precision=p,
        recall=r,
        f1=f1,
        pred_count=len(pred_set),
        gold_count=len(gold_set),
        true_positive=tp,
        false_positive=fp,
        false_negative=fn,
    )


def is_multi_value_field(field_name: str) -> bool:
    """Check if a field is a multi-value field per v4.1 sec 7.4.

    Multi-value fields:
    - winner_name: multiple winners
    - amount: multiple lot amounts
    - project_identifier: multiple project numbers
    - bid_deadline: multiple bid deadlines
    - consortium_partners: as sub-structure of winner_name

    Single-value fields:
    - purchaser_name: usually single
    - publish_date: usually single
    """
    multi_value_fields = {
        "winner_name",
        "amount",
        "project_identifier",
        "bid_deadline",
    }
    return field_name in multi_value_fields
