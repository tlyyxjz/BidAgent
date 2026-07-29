"""W3-07 选择性输出策略（v4.1 第十章 10.7 节）.

策略一览:
  strict  = 仅输出 high (严格用户可见)
  default = 输出 high + 满足条件的 review (high + review 中 support_level=STRONG 的)
  loose   = 输出 high + 全部 review (审核视图 / 内部用户)
  audit   = 输出所有字段，含 low (审计视图 / 质量分析)

说明:
  - 「条件」: review 级字段必须 support_level ∈ STRONG (direct/equivalent)，视为"基本正确但来源较弱"
  - 纯函数实现，不绑定数据库类型；入参元素只需带 display_grade / support_level 两个属性
"""
from __future__ import annotations

from typing import Any, Iterable, Union

from app.processors.display_grade import (
    GRADE_HIGH,
    GRADE_LOW,
    GRADE_REVIEW,
    _STRENGTH_HIGH,
)


VALID_STRATEGIES = ("strict", "default", "loose", "audit")


def _g(field: Any) -> str:
    if hasattr(field, "display_grade"):
        return str(field.display_grade or GRADE_REVIEW).lower()
    if isinstance(field, dict):
        return str(field.get("display_grade", GRADE_REVIEW) or GRADE_REVIEW).lower()
    return GRADE_REVIEW


def _sl(field: Any) -> str:
    if hasattr(field, "support_level"):
        return str(field.support_level or "").lower()
    if isinstance(field, dict):
        return str(field.get("support_level", "") or "").lower()
    return ""


def filter_by_strategy(
    fields: Iterable[Any],
    strategy: str = "default",
) -> list[Any]:
    """按输出策略过滤字段.

    Args:
        fields: 字段集合（每条可为 ORM 对象或 dict，需提供 display_grade/support_level）
        strategy: strict/default/loose/audit

    Returns:
        过滤后的字段列表（保持原顺序）.
    """
    strat = (strategy or "default").lower()
    if strat not in VALID_STRATEGIES:
        raise ValueError(
            f"Unknown strategy '{strategy}'. Valid: {VALID_STRATEGIES}"
        )

    result: list[Any] = []
    for f in fields:
        grade = _g(f)

        if strat == "strict":
            if grade == GRADE_HIGH:
                result.append(f)
            continue

        if strat == "audit":
            # 包含所有 (high/review/low)
            result.append(f)
            continue

        if grade == GRADE_LOW:
            # loose/default 都不包含 low
            continue

        if grade == GRADE_HIGH:
            result.append(f)
            continue

        # grade == REVIEW 分支
        if strat == "loose":
            result.append(f)
        else:  # default: review 需满足 support_level ∈ STRONG
            if _sl(f) in _STRENGTH_HIGH:
                result.append(f)

    return result
