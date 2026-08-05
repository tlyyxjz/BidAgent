"""Evidence verification utility function."""
from __future__ import annotations

from typing import Tuple


def verify_evidence(
    raw_text: str,
    evidence_text: str,
    start: int,
    end: int,
) -> Tuple[bool, str]:
    """验证证据偏移量是否正确。

    Args:
        raw_text: 原始文本
        evidence_text: 证据文本
        start: 起始偏移（含）
        end: 结束偏移（不含）

    Returns:
        (valid, message)
    """
    if not raw_text:
        return (False, "raw_text is empty")

    if not evidence_text:
        return (False, "evidence_text is empty")

    if not (isinstance(start, int) and isinstance(end, int)):
        return (False, f"start/end must be int, got {type(start)}/{type(end)}")

    if start < 0 or end > len(raw_text) or start >= end:
        return (False, f"offset out of bounds: [{start},{end}], len={len(raw_text)}")

    actual = raw_text[start:end]

    # 完全匹配
    if actual == evidence_text:
        return (True, "exact match")

    # 容忍尾部换行符差异
    if actual.rstrip("\n") == evidence_text.rstrip("\n"):
        return (True, "match after trailing newline trim")

    # 容忍首尾空白差异
    if actual.strip() == evidence_text.strip():
        return (True, "match after strip")

    return (
        False,
        f"slice mismatch: expected='{evidence_text[:30]}...', actual='{actual[:30]}...'",
    )
