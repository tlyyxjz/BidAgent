# -*- coding: utf-8 -*-
"""award 公告中标金额（win_amount）确定性解析器（大件一-③）。

设计原则（同 award_company_parser：规则先行、宁可少给不可编造）：
- 锚点必须命中 "中标/成交金额" 标签，且标签后紧跟冒号，或标签带单位括号
  （金额(万元)）——杜绝把压平表头 "中标（成交）金额评审总得分1" 误当金额
- 单位换算：万/亿 倍率展开，统一落 元
- 结果带 evidence span，可回原文定位
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# 金额标签：中标（成交）金额 / 中标金额 / 成交金额（括号全半角兼容）
_LABEL = r"中标[（(]?成交[)）]?金额|中标金额|成交金额"
# 单位后缀（可选）：(万元) / （元）
_UNIT_PAREN = r"(?:\s*[（(]\s*(亿元|万元|亿|万|元)\s*[)）])?"
_NUM = r"(\d{1,3}(?:[,，]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
# 数字后自由单位：万元/亿元/元
_UNIT_FREE = r"\s*(亿元|万元|亿|万|元)?"

_PATTERN = re.compile(
    rf"(?:{_LABEL}){_UNIT_PAREN}\s*[:：]\s*{_NUM}{_UNIT_FREE}"
)

_UNIT_MAP = {
    "亿元": Decimal("100000000"),
    "亿": Decimal("100000000"),
    "万元": Decimal("10000"),
    "万": Decimal("10000"),
    "元": Decimal("1"),
}


@dataclass(frozen=True)
class WinAmountResult:
    """中标金额抽取结果（元为单位，带证据 span）。"""

    value: Decimal
    raw: str  # 原文匹配片段（可观测）
    span_start: int
    span_end: int


def parse_win_amount(text: str | None) -> WinAmountResult | None:
    """从 award 公告正文抽取中标金额（元）。None/空文本安全。

    Returns:
        WinAmountResult 或 None（宁缺毋滥：锚点不命中/数字解析失败均返回 None）
    """
    if not text:
        return None
    m = _PATTERN.search(text)
    if not m:
        return None
    num_str = m.group(2).replace(",", "").replace("，", "")
    unit = m.group(1) or m.group(3)  # 括号单位优先于数字后单位
    multiplier = _UNIT_MAP.get(unit, Decimal("1")) if unit else Decimal("1")
    try:
        value = Decimal(num_str) * multiplier
    except (InvalidOperation, ValueError):
        return None
    if value <= 0:
        return None
    return WinAmountResult(
        value=value,
        raw=m.group(0),
        span_start=m.start(),
        span_end=m.end(),
    )
