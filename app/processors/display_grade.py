"""W3-07 display_grade 展示等级计算。

对应总规划 v4.1 第八章「展示等级」与第十章 10.7「选择性输出策略」。

判定规则（v4.1 第八章，命题提示词）:
  HIGH = "high"      → 可直接对外输出
  REVIEW = "review"  → 复核后输出（默认）
  LOW = "low"        → 仅审计视图可见

输入说明：
  support_level（app.processors.evidence_locator.SupportLevel）:
    DIRECT    → 强证据（原文精确出现，v4.1 STRONG）
    EQUIVALENT → 强证据（规范化后匹配，v4.1 STRONG）
    INFERRED  → 中证据（L3/L4 推导，v4.1 MEDIUM）
    UNSUPPORTED → 弱证据（无依据，v4.1 WEAK）
    CONTRADICTED → 弱证据（冲突，v4.1 WEAK）
  source_role（app.llm.extraction_schemas 中 lineage）:
    official_original / official_repost / commercial_repost / unknown
  cross_verified:
    是否被多源交叉验证
  field_status:
    present / absent / ambiguous / unreadable / multi_value
"""
from __future__ import annotations

from typing import Any, Union

from app.processors.evidence_locator import SupportLevel

GRADE_HIGH = "high"
GRADE_REVIEW = "review"
GRADE_LOW = "low"
VALID_GRADES = (GRADE_HIGH, GRADE_REVIEW, GRADE_LOW)

# v4.1 §10.12 展示等级规则版本
DISPLAY_RULE_VERSION = "v1.0-frozen"

# SupportLevel → 强度映射（v4.1 第八章 STRONG/MEDIUM/WEAK）
_STRENGTH_HIGH = {SupportLevel.DIRECT.value, SupportLevel.EQUIVALENT.value}
_STRENGTH_MID = {SupportLevel.INFERRED.value}
_STRENGTH_LOW = {SupportLevel.UNSUPPORTED.value, SupportLevel.CONTRADICTED.value}

# 可视为"无数据 / 应降级"的字段状态
_FIELD_STATUS_LOW = {"absent", "ambiguous", "unreadable"}


# 兼容传入 SupportLevel 枚举或原始字符串
def _sl_value(sl: Union[str, SupportLevel, None]) -> str:
    if sl is None:
        return SupportLevel.UNSUPPORTED.value
    if isinstance(sl, SupportLevel):
        return sl.value
    return str(sl)


def cross_verify_status_to_bool(cross_verify_status: str) -> bool:
    """将 cross_verify_status 6 态 enum 转换为布尔值（向后兼容）。

    Args:
        cross_verify_status: 6 态 enum 之一

    Returns:
        True 如果是 independent 或 consistent_unknown（视为已交叉验证）
    """
    return cross_verify_status in {"independent", "consistent_unknown"}


def compute_display_grade(
    support_level: Union[str, SupportLevel],
    source_role: str,
    cross_verified: bool = False,
    field_status: str = "present",
) -> str:
    """计算字段展示等级.

    判定顺序（v4.1 第八章「先降级规则，后升级」）:
    1. 先判断 LOW 规则（排除性规则优先级最高）:
       - field_status ∈ (absent, ambiguous, unreadable) → low
       - support_level ∈ WEAK (unsupported, contradicted) → low
       - source_role == "unknown" → low
    2. 再判断 HIGH 规则:
       - (support_level ∈ STRONG: direct/equivalent) AND
         (source_role == "official_original") → high
         （cross_verified=True 为 bonus，不改变 high 判定结果）
    3. 其余 → review:
       - support_level ∈ MEDIUM (inferred)
       - source_role ∈ (official_repost, commercial_repost)
       - support_level=STRONG 但 source_role=commercial_repost
       - 任何 HIGH/LOW 边界之外的混合

    Args:
        support_level: SupportLevel 枚举或字符串值（direct/equivalent/...）
        source_role: 来源角色 (official_original/official_repost/commercial_repost/unknown)
        cross_verified: 是否被多源交叉验证（当前为 bonus 标记，不改变 grade）
        field_status: 字段状态 present/absent/ambiguous/unreadable

    Returns:
        "high" | "review" | "low"
    """
    sl = _sl_value(support_level).lower()
    src = (source_role or "unknown").lower()
    fs = (field_status or "present").lower()

    # Step 1: LOW (排除规则)
    if fs in _FIELD_STATUS_LOW:
        return GRADE_LOW
    if sl in _STRENGTH_LOW:
        return GRADE_LOW
    if src == "unknown":
        return GRADE_LOW

    # Step 2: HIGH
    if sl in _STRENGTH_HIGH and src == "official_original":
        return GRADE_HIGH

    # Step 3: REVIEW (其余全部)
    # - sl=MEDIUM (inferred)
    # - sl=STRONG + official_repost
    # - sl=STRONG + commercial_repost
    return GRADE_REVIEW
