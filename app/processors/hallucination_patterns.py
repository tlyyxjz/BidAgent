"""反幻觉校验的正则模式与归一化工具。

从 hallucination_checker.py 拆分而来，包含：
- _AMOUNT_RE / _DATE_RE：金额、日期匹配正则
- _PATTERNS：关键事实模式列表（金额/日期/百分比/数量/电话/邮箱/招标编号）
- _normalize_amount / _normalize_date：金额、日期归一化

校验主流程（Fact/CheckReport/extract_facts/check_content/check_items）
仍由 hallucination_checker 负责。

M-4 修复：招标编号正则进一步收紧，要求字母+数字混合或常见前缀。
M-5 修复：金额/日期归一化后比对，避免 "1 万元" vs "10000 元" 误判。
m-1 修复：数量正则去掉重复的"套"。
"""

from __future__ import annotations

import re
from datetime import datetime


# M-5 修复：金额归一化 → 转换为"元"为单位的纯数字字符串
# 新-3 修复：必须带单位（万元/亿元/亿/万/元），纯数字不归一化（避免误匹配年份/编号）
# m-3 修复（第四轮）：单一来源，_PATTERNS 复用 _AMOUNT_RE，避免两个正则不一致
_AMOUNT_RE = re.compile(r"\s*(\d+(?:\.\d+)?)\s*(亿元|亿|万元|万|元)\s*")


# 关键事实模式：数字 + 单位/日期/金额
# S-5 + M-4 修复：招标编号正则进一步收紧
#   - 必须含字母 + 必须含数字
#   - 必须含连字符（- 或 _）
#   - 总长度 6-30 字符
#   - 或匹配常见项目编号前缀（SH-/ZB-/GG-/BJ-/GD- 等）
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # m-3 修复：金额正则复用 _AMOUNT_RE（单一来源）
    ("金额", _AMOUNT_RE),
    ("日期", re.compile(r"\d{4}年\d{1,2}月\d{1,2}日|\d{4}-\d{1,2}-\d{1,2}|\d{4}/\d{1,2}/\d{1,2}")),
    ("百分比", re.compile(r"\d+(?:\.\d+)?\s*%")),
    # m-1 修复：去掉重复的"套"
    ("数量", re.compile(r"\d+\s*(?:台|套|个|批|项|份|辆)")),
    ("联系电话", re.compile(r"\d{3,4}-?\d{7,8}")),
    ("邮箱", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    # 招标编号：常见前缀（SH-/ZB-/GG-/BJ-/GD-/JS/ZJ/FZ/XM/CG/GS/GZ-）或 字母+数字混合+连字符
    ("招标编号", re.compile(
        r"(?:(?:SH|ZB|GG|BJ|GD|JS|ZJ|FZ|XM|CG|GS|GZ)-[A-Z0-9-]{4,28})"
        r"|(?:[A-Z]{2,}\d{4,}[A-Z0-9]*(?:-[A-Z0-9]+)?)"
    )),
]


def _normalize_amount(value: str) -> str | None:
    """金额归一化：'1万元' / '10000元' 都返回 '10000'。

    新-3 修复：要求必须带单位，纯数字（如 '2026'）返回 None，避免误匹配。
    """
    m = _AMOUNT_RE.match(value)
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2)
    if "亿" in unit:
        num *= 100_000_000
    elif "万" in unit:
        num *= 10_000
    # 元：保持原值
    if num == int(num):
        return str(int(num))
    return f"{num:.2f}".rstrip("0").rstrip(".")


# M-5 修复：日期归一化 → 转换为 YYYY-MM-DD
# 新-6 修复：增加点号格式 YYYY.MM.DD 支持
_DATE_RE = re.compile(
    r"\s*(\d{4})[年\-/.](\d{1,2})[月\-/.](\d{1,2})日?\s*"
)


def _normalize_date(value: str) -> str | None:
    """日期归一化：'2024年1月1日' / '2024/1/1' / '2024-01-01' / '2024.1.1' 都返回 '2024-01-01'。

    新-6 修复：增加点号分隔格式支持。
    """
    m = _DATE_RE.match(value)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return datetime(y, mo, d).strftime("%Y-%m-%d")
    except ValueError:
        return None
