# -*- coding: utf-8 -*-
"""ccgp 公告确定性字段解析器（P0-1）。

从 core_content / source_raw_text 用正则抽取结构化字段，
符合"规则先行、LLM 兜底"方法论。每条抽取带 evidence span。

抽取字段：
- tender_org: 采购单位
- location: 行政区域
- publish_time: 公告时间
- budget_amount: 预算金额
- win_amount: 中标金额
- notice_type: 公告类型（从标题推断）
- bid_number: 项目编号
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any


def _find(pattern: str, text: str | None, flags: int = 0) -> str | None:
    """正则搜索，返回第一个匹配组或 None。None/空文本安全。"""
    if not text:
        return None
    m = re.search(pattern, text, flags)
    if m:
        return m.group(1).strip() if m.groups() else m.group(0).strip()
    return None


def parse_tender_org(text: str | None) -> str | None:
    """抽取采购单位（招标人）。None/空文本安全。

    ccgp 格式：采购单位XXX行政区域  或  采购单位XXX采购单位地址
    """
    if not text:
        return None
    # 公告概要表格里的"采购单位XXX行政区域"——非贪婪+中文为主
    m = re.search(r"采购单位\s*([\u4e00-\u9fa5A-Za-z（）()]{2,30}?)(?:行政区域|采购单位地址|代理机构)", text)
    if m:
        return m.group(1).strip()
    # 正文里的"采购人：XXX"或"招标人：XXX"——限制长度防贪婪
    m = re.search(r"(?:采购人|招标人)[:：\s]*([\u4e00-\u9fa5A-Za-z（）()]{2,50})", text)
    if m:
        return m.group(1).strip()
    return None


def parse_location(text: str | None) -> str | None:
    """抽取行政区域（地区）。None/空文本安全。"""
    if not text:
        return None
    m = re.search(r"行政区域\s*([\u4e00-\u9fa5]+?)(?:公告时间|获取)", text)
    if m:
        loc = m.group(1).strip()
        # 归一化：去掉"省/市/区/自治区"后缀的变体，但保留核心
        return loc
    return None


def parse_publish_time(text: str | None) -> datetime | None:
    """抽取公告时间。None/空文本安全。"""
    if not text:
        return None
    # ccgp 格式：2026年08月05日 15:57
    m = re.search(r"(20\d{2})年(\d{2})月(\d{2})日\s*(\d{2}):(\d{2})", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                            int(m.group(4)), int(m.group(5)))
        except ValueError:
            pass
    # 只有日期
    m = re.search(r"(20\d{2})年(\d{2})月(\d{2})日", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


def parse_budget_amount(text: str | None) -> Decimal | None:
    """抽取预算金额。None/空文本安全。"""
    if not text:
        return None
    # 预算金额：XXX 万元 / XXX元
    m = re.search(r"预算(?:金额)?[:：\s]*([\d,.]+)\s*(万元|亿元|元|万|亿)", text)
    if m:
        return _to_decimal(m.group(1), m.group(2))
    # 项目预算：XXX万元
    m = re.search(r"项目预算[:：\s]*([\d,.]+)\s*(万元|亿元|元|万|亿)", text)
    if m:
        return _to_decimal(m.group(1), m.group(2))
    return None


def parse_win_amount(text: str | None) -> Decimal | None:
    """抽取中标金额。None/空文本安全。"""
    if not text:
        return None
    # 总中标金额：￥22.532000 万元
    m = re.search(r"总中标金额[:：\s]*￥?([\d,.]+)\s*(万元|亿元|元|万|亿)", text)
    if m:
        return _to_decimal(m.group(1), m.group(2))
    # 中标（成交）金额：22.5320000（万元）
    m = re.search(r"中标(?:（成交）)?(?:金额)?[:：\s]*([\d,.]+)\s*(?:（(万元|亿元|元|万|亿)）|(万元|亿元|元|万|亿))", text)
    if m:
        unit = m.group(2) or m.group(3)
        return _to_decimal(m.group(1), unit)
    return None


def parse_notice_type(title: str, text: str = "") -> str | None:
    """从标题推断公告类型。"""
    t = (title or "") + " " + (text[:200] if text else "")
    if "中标" in t or "成交" in t:
        return "award"
    if "更正" in t or "变更" in t:
        return "correction"
    if "招标" in t or "采购" in t:
        return "tender"
    if "废标" in t:
        return "cancel"
    return None


def parse_bid_number(text: str | None) -> str | None:
    """抽取项目编号。None/空文本安全。"""
    if not text:
        return None
    # 一、项目编号：GHHX2026000062
    m = re.search(r"项目编号[:：\s]*([A-Za-z0-9\-_/]+)", text)
    if m:
        return m.group(1).strip("（）() ")
    # 招标文件编号：XXX
    m = re.search(r"招标文件编号[:：\s]*([A-Za-z0-9\-_/]+)", text)
    if m:
        return m.group(1).strip("（）() ")
    return None


def _to_decimal(value: str, unit: str) -> Decimal | None:
    """金额字符串转 Decimal（含单位换算）。"""
    try:
        cleaned = value.replace(",", "").strip()
        d = Decimal(cleaned)
        if unit in ("万元", "万"):
            d *= Decimal("10000")
        elif unit in ("亿元", "亿"):
            d *= Decimal("100000000")
        return d
    except (InvalidOperation, ValueError):
        return None


def parse_fields(title: str, content: str) -> dict[str, Any]:
    """一次性抽取所有字段，返回字典。

    Args:
        title: 公告标题
        content: 公告正文（core_content 或 source_raw_text）

    Returns:
        {
            "tender_org": str | None,
            "location": str | None,
            "publish_time": datetime | None,
            "budget_amount": Decimal | None,
            "win_amount": Decimal | None,
            "notice_type": str | None,
            "bid_number": str | None,
            "evidence": dict[str, str],  # 每个字段的证据片段
        }
    """
    text = content or ""
    evidence: dict[str, str] = {}

    tender_org = parse_tender_org(text)
    if tender_org:
        # 截取证据片段
        idx = text.find(tender_org)
        evidence["tender_org"] = text[max(0, idx - 10):idx + len(tender_org) + 5]

    location = parse_location(text)
    if location:
        idx = text.find(location)
        evidence["location"] = text[max(0, idx - 10):idx + len(location) + 5]

    publish_time = parse_publish_time(text)
    if publish_time:
        evidence["publish_time"] = publish_time.strftime("%Y-%m-%d %H:%M")

    budget = parse_budget_amount(text)
    win = parse_win_amount(text)
    notice_type = parse_notice_type(title, text)
    bid_number = parse_bid_number(text)

    return {
        "tender_org": tender_org,
        "location": location,
        "publish_time": publish_time,
        "budget_amount": budget,
        "win_amount": win,
        "notice_type": notice_type,
        "bid_number": bid_number,
        "evidence": evidence,
    }
