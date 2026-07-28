"""展示等级计算 (v4.1 第 6.6 节).

输入三个质量维度:
- support_level: direct / equivalent / inferred / unsupported / contradicted
- source_quality: official_original / official_repost / authorized_original
                  / commercial_repost / index_only / unknown
- cross_verify_status: independent / consistent_unknown / same_origin
                       / version_difference / conflict / single_source

输出展示等级:
- high: 可直接展示给用户
- review: 需人工复核
- low: 默认过滤或显著提示

规则要点 (v4.1 第 6.4 节):
- 单一官方原始来源 + 直接原文证据可构成 high
  (独立跨源验证是增强项, 非必要条件)
- 推导字段 (inferred) 一律 review
- 无证据 / 冲突 / 来源未知 一律 low
"""
from __future__ import annotations

from enum import Enum


RULE_VERSION = "display_grade_v1.0"


class DisplayGrade(str, Enum):
    HIGH = "high"
    REVIEW = "review"
    LOW = "low"


# 合法输入枚举（用于输入校验和测试）
SUPPORT_LEVELS = {
    "direct", "equivalent", "inferred", "unsupported", "contradicted",
}
SOURCE_QUALITIES = {
    "official_original", "official_repost", "authorized_original",
    "commercial_repost", "index_only", "unknown",
}
CROSS_VERIFY_STATUSES = {
    "independent", "consistent_unknown", "same_origin",
    "version_difference", "conflict", "single_source",
}

# 官方来源集合（high 候选）
OFFICIAL_SOURCES = {
    "official_original", "official_repost", "authorized_original",
}


def compute_display_grade(support_level, source_quality, cross_verify_status):
    """根据三个质量维度计算展示等级.

    规则 (v4.1 第 6.6 节):
    - LOW 优先: 无证据 / 冲突 / 来源未知
    - REVIEW: 推导 / 商业转载 / 索引 / 转载链 / 独立性未知
    - HIGH: direct/equivalent + 官方来源 + 无冲突
      (single_source + 官方原始来源 + 直接证据 = high)
    """
    # LOW 优先判定
    if support_level in ("unsupported", "contradicted"):
        return DisplayGrade.LOW
    if cross_verify_status == "conflict":
        return DisplayGrade.LOW
    if source_quality == "unknown":
        return DisplayGrade.LOW

    # REVIEW 判定: 推导
    if support_level == "inferred":
        return DisplayGrade.REVIEW
    # REVIEW 判定: 商业转载 / 索引
    if source_quality in ("commercial_repost", "index_only"):
        return DisplayGrade.REVIEW
    # REVIEW 判定: 同源转载（独立性不足）
    if cross_verify_status == "same_origin":
        return DisplayGrade.REVIEW

    # HIGH 判定: direct/equivalent + 官方来源 + 无冲突
    if support_level in ("direct", "equivalent"):
        if source_quality in OFFICIAL_SOURCES:
            # single_source + 官方原始来源 + 直接证据 = high
            # independent / consistent_unknown / version_difference 也算 high
            if cross_verify_status in (
                "independent", "consistent_unknown",
                "single_source", "version_difference",
            ):
                return DisplayGrade.HIGH
            return DisplayGrade.REVIEW
        return DisplayGrade.REVIEW

    # 兜底：未匹配任何规则，标记待复核
    return DisplayGrade.REVIEW


def validate_inputs(support_level, source_quality, cross_verify_status):
    """校验输入值是否在合法枚举内（用于测试和调试）."""
    errors = []
    if support_level not in SUPPORT_LEVELS:
        errors.append(
            f"support_level={support_level!r} 不在 {SUPPORT_LEVELS}"
        )
    if source_quality not in SOURCE_QUALITIES:
        errors.append(
            f"source_quality={source_quality!r} 不在 {SOURCE_QUALITIES}"
        )
    if cross_verify_status not in CROSS_VERIFY_STATUSES:
        errors.append(
            f"cross_verify_status={cross_verify_status!r} 不在 {CROSS_VERIFY_STATUSES}"
        )
    return errors
