"""Tender 入库辅助工具函数。

C-1 修复（第四轮）：从 tender_ingestor.py 拆出纯函数，让主文件 ≤ 300 行。

包含：
- _hash_contact: 联系人 SHA256
- _parse_decimal: 金额解析（万/亿元单位换算）
- _parse_datetime: 多格式日期解析
- _infer_platform: URL/模板名推断来源平台
- _build_tender: 从采集 item 构建 Tender ORM 对象

m-1 修复（第五轮）：删除未使用的 _hamming_distance（统一用 simhash.hamming_distance）
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

from app.models.tender import Tender


def _hash_contact(value: str | None) -> str | None:
    """SHA256 哈希联系人信息（隐私保护）。"""
    if not value or not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _parse_decimal(value: Any) -> Decimal | None:
    """从字符串/数字解析金额（支持 万/亿元 单位），失败返回 None。

    C-3 修复：原逻辑直接 replace("万","").replace("元","") 导致 "100万元" → 100，
              实际应为 1,000,000。现按单位乘以对应倍率。
    """
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
    if isinstance(value, str):
        cleaned = value.replace(",", "").strip()
        if not cleaned:
            return None
        multiplier = Decimal(1)
        if "亿元" in cleaned:
            multiplier = Decimal("100000000")
            cleaned = cleaned.replace("亿元", "")
        elif "亿" in cleaned:
            multiplier = Decimal("100000000")
            cleaned = cleaned.replace("亿", "")
        elif "万元" in cleaned:
            multiplier = Decimal("10000")
            cleaned = cleaned.replace("万元", "")
        elif "万" in cleaned:
            multiplier = Decimal("10000")
            cleaned = cleaned.replace("万", "")
        elif "元" in cleaned:
            cleaned = cleaned.replace("元", "")
        cleaned = cleaned.strip()
        if not cleaned:
            return None
        try:
            return Decimal(cleaned) * multiplier
        except (InvalidOperation, ValueError):
            return None
    return None


def _parse_datetime(value: Any) -> datetime | None:
    """解析日期字符串，支持多种格式。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d",
        "%Y年%m月%d日 %H:%M",
        "%Y年%m月%d日",
        "%Y.%m.%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _infer_platform(url: str, template: str | None = None) -> str:
    """从 URL 或模板名推断来源平台。"""
    if template:
        return template
    try:
        host = urlparse(url).hostname or ""
    except Exception:  # noqa: BLE001
        return "unknown"
    host = host.lower()
    if "ccgp" in host:
        return "ccgp"
    if "chinabidding" in host or "bidchance" in host:
        return "chinabidding"
    if "ggzy" in host:
        return "ggzy"
    if "qianlima" in host:
        return "qianlima"
    return host.split(".")[0] or "unknown"


def _build_tender(
    item: dict[str, Any],
    source_url: str,
    source_platform: str,
    simhash_value: int | None,
) -> Tender:
    """从采集 item 字典构建 Tender ORM 对象。

    字段映射规则（兼容多种字段名）：
        project_name / title / 标题
        bid_number / 招标编号
        budget_amount / budget / 预算
        location / region / 地区
        publish_time / publish_date / 发布时间
        deadline / 截止时间
        tender_org / 招标人
        agency / 代理机构
        contact_name / 联系人
        contact_phone / phone / 联系电话
        contact_email / email / 联系邮箱
        notice_type / 公告类型
        core_content / content / 核心内容
        attachment_url / attachment / 附件链接
    """
    def pick(*keys: str) -> Any:
        for k in keys:
            if k in item and item[k] not in (None, ""):
                return item[k]
        return None

    return Tender(
        project_name=str(pick("project_name", "title", "标题") or "")[:500] or "未命名",
        bid_number=str(pick("bid_number", "招标编号") or "")[:100] or None,
        budget_amount=_parse_decimal(pick("budget_amount", "budget", "预算")),
        location=str(pick("location", "region", "地区") or "")[:200] or None,
        publish_time=_parse_datetime(pick("publish_time", "publish_date", "发布时间")),
        deadline=_parse_datetime(pick("deadline", "截止时间")),
        tender_org=str(pick("tender_org", "招标人") or "")[:300] or None,
        agency=str(pick("agency", "代理机构") or "")[:300] or None,
        contact_name=str(pick("contact_name", "联系人") or "")[:100] or None,
        contact_phone=_hash_contact(pick("contact_phone", "phone", "联系电话")),
        contact_email=_hash_contact(pick("contact_email", "email", "联系邮箱")),
        notice_type=str(pick("notice_type", "公告类型") or "")[:50] or None,
        source_platform=source_platform,
        source_url=str(pick("source_url", "url") or source_url or "")[:500] or None,
        core_content=pick("core_content", "content", "核心内容") or "",
        # C-2 修复：保存原始页面文本，反幻觉校验时比对
        source_raw_text=pick("source_raw_text", "raw_text", "source_text") or "",
        attachment_url=pick("attachment_url", "attachment", "附件链接"),
        simhash=simhash_value,
    )


def _classify_source_role(
    source_url: str,
    title: str,
    notice_type: str | None = None,
    core_content: str | None = None,
) -> str:
    """计算来源角色 (v4.1 第八章 8.2 节).

    接入 app.processors.source_lineage, 不修改 schema.
    用于在入库时标记来源角色, 写入 source_platform 字段.

    Args:
        source_url: 来源 URL
        title: 公告标题
        notice_type: 公告类型
        core_content: 正文内容 (可选, 用于计算 SimHash)

    Returns:
        来源角色字符串 (official_original / official_repost / commercial_repost / index_only / unknown)
    """
    try:
        from app.processors.source_lineage import (
            SOURCE_ROLE_UNKNOWN,
            build_lineage_features,
            classify_source_role,
        )
        feats = build_lineage_features(
            url=source_url or "",
            title=title or "",
            notice_type=notice_type or "other",
            content_text=core_content[:5000] if core_content else None,
        )
        return classify_source_role(feats)
    except Exception:
        # source_lineage 不可用时降级, 不阻塞入库
        return SOURCE_ROLE_UNKNOWN
