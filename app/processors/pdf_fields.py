"""PDF 关键字段提取（正则 + 金额/日期归一化）。

从 pdf_parser.py 拆分而来，包含：
- _FIELD_PATTERNS：项目名称/编号/预算/截止时间/招标人等正则
- _parse_decimal / _parse_datetime：金额、日期归一化
- _extract_fields：从全文文本提取关键字段

主解析流程（pdfplumber 调用、线程池封装、DB 回写）仍由 pdf_parser 负责。
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any


# 关键字段正则（复用 hallucination_checker 的模式）
_FIELD_PATTERNS: dict[str, re.Pattern[str]] = {
    "project_name": re.compile(
        r"(?:项目名称|项目名|工程名称|采购项目名)\s*[:：]\s*([^\n\r，。；]{2,100})"
    ),
    "bid_number": re.compile(
        r"(?:招标编号|项目编号|采购编号|标段编号)\s*[:：]\s*([A-Z0-9\-/]{6,40})"
    ),
    "budget_amount": re.compile(
        r"(?:预算金额|项目预算|采购预算|控制价)\s*[:：]\s*"
        r"(\d+(?:\.\d+)?\s*(?:万元|亿元|元|万|亿))"
    ),
    "deadline": re.compile(
        r"(?:投标截止时间|报名截止时间|截止时间|投标截止日期)\s*[:：]\s*"
        r"(\d{4}年\d{1,2}月\d{1,2}日(?:\s*\d{1,2}:\d{1,2})?)"
    ),
    "tender_org": re.compile(
        r"(?:招标人|采购人|招标单位|采购单位)\s*[:：]\s*([^\n\r，。；]{2,50})"
    ),
    "agency": re.compile(
        r"(?:代理机构|招标代理|采购代理)\s*[:：]\s*([^\n\r，。；]{2,50})"
    ),
    "contact_name": re.compile(
        r"(?:联系人|项目联系人)\s*[:：]\s*([^\n\r，。；]{2,30})"
    ),
    "contact_phone": re.compile(
        r"(?:联系电话|电话|联系方式)\s*[:：]\s*(\d{3,4}-?\d{7,8})"
    ),
}


def _parse_decimal(value: str | None) -> Decimal | None:
    """金额字符串转 Decimal。

    支持：
    - "500000" / "500,000" - 纯数字
    - "50万元" - 50 万 = 500000
    - "1.5亿元" - 1.5 亿 = 150000000
    - "50万" / "1.5亿" - 单位简写
    """
    if not value:
        return None
    cleaned = value.replace(",", "").strip()
    # 优先处理"亿元"（先于"万"）
    if "亿元" in cleaned:
        num_str = cleaned.replace("亿元", "").replace("亿", "").replace("元", "").strip()
        try:
            return Decimal(num_str) * Decimal("100000000")
        except (InvalidOperation, ValueError):
            return None
    if "万元" in cleaned:
        num_str = cleaned.replace("万元", "").replace("万", "").replace("元", "").strip()
        try:
            return Decimal(num_str) * Decimal("10000")
        except (InvalidOperation, ValueError):
            return None
    if "亿" in cleaned:
        num_str = cleaned.replace("亿", "").replace("元", "").strip()
        try:
            return Decimal(num_str) * Decimal("100000000")
        except (InvalidOperation, ValueError):
            return None
    if "万" in cleaned:
        num_str = cleaned.replace("万", "").replace("元", "").strip()
        try:
            return Decimal(num_str) * Decimal("10000")
        except (InvalidOperation, ValueError):
            return None
    cleaned = cleaned.replace("元", "").strip()
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def _parse_datetime(value: str | None) -> datetime | None:
    """日期字符串转 datetime。"""
    if not value:
        return None
    s = value.strip()
    for fmt in ("%Y年%m月%d日 %H:%M", "%Y年%m月%d日", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _extract_fields(text: str) -> dict[str, Any]:
    """从全文文本提取关键字段。"""
    fields: dict[str, Any] = {}
    for field_name, pattern in _FIELD_PATTERNS.items():
        match = pattern.search(text)
        if not match:
            continue
        raw_value = match.group(1).strip()
        if field_name == "budget_amount":
            dec = _parse_decimal(raw_value)
            if dec is not None:
                fields[field_name] = float(dec)
                fields["budget_raw"] = raw_value
        elif field_name == "deadline":
            dt = _parse_datetime(raw_value)
            if dt is not None:
                fields[field_name] = dt.isoformat()
        else:
            fields[field_name] = raw_value
    return fields
