"""数据迁移工具：将扁平 Tender 表数据迁移到四层实体表。

对应《标小智 项目总体规划 v4.1》第四章四层聚合结构。

迁移映射（每条 Tender → 四层链）：
    Tender.project_name      → TenderProject.canonical_name
    Tender.notice_type       → TenderNotice.notice_type（经 _map_notice_type 映射）
    Tender.project_name      → TenderNotice.canonical_title
    Tender.publish_time      → TenderNotice.publish_date
    Tender.source_url        → NoticeSource.source_url（兼作幂等去重键）
    Tender.source_platform   → NoticeSource.source_platform
    Tender.core_content      → NoticeVersion.content_sha256
    Tender.source_raw_text   → NoticeVersion.raw_text_sha256

幂等性：以 NoticeSource.source_url 为去重键，重复运行跳过已迁移记录。
ULID 主键由各实体类的 default=_new_ulid 自动生成。
"""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tender import Tender
from app.models.tender_project import (
    NoticeSource,
    NoticeVersion,
    TenderNotice,
    TenderProject,
    _new_ulid,
)
from app.utils.logger import get_logger

logger = get_logger("data_migration")


def _content_sha256(text: str | None) -> str:
    """计算文本的 SHA256 十六进制摘要（64 字符）。"""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# Tender.notice_type → TenderNotice.notice_type 映射表
_NOTICE_TYPE_MAP: dict[str, str] = {
    "tender": "tender",
    "招标": "tender",
    "award": "award",
    "中标": "award",
    "correction": "correction",
    "更正": "correction",
    "clarification": "clarification",
    "澄清": "clarification",
    "cancellation": "cancellation",
    "废标": "cancellation",
    "contract": "contract",
    "合同": "contract",
}


def _map_notice_type(raw: str | None) -> str:
    """将 Tender.notice_type 映射到四层模型 notice_type 枚举。

    未知类型统一归为 other（v4.1 第四章 4.3 节 notice_type 枚举）。
    """
    if not raw:
        return "tender"
    return _NOTICE_TYPE_MAP.get(raw, "other")


def _map_platform_type(platform: str | None) -> str:
    """将 source_platform 映射到 platform_type 枚举。"""
    if not platform:
        return "unknown"
    government_keywords = ("ccgp", "gov", "ggzy")
    if any(kw in platform.lower() for kw in government_keywords):
        return "government"
    return "commercial"


async def _get_existing_source_urls(db: AsyncSession) -> set[str]:
    """查询 notice_sources 表中已存在的 source_url 集合（幂等去重键）。"""
    result = await db.execute(select(NoticeSource.source_url))
    return {row[0] for row in result.all()}


async def migrate_tender_to_four_layer(db: AsyncSession) -> dict:
    """将现有 Tender 表数据迁移到四层实体表。

    每条 Tender 记录创建对应的四层链：
    TenderProject → TenderNotice → NoticeSource → NoticeVersion

    幂等性：以 source_url 为去重键，重复运行跳过已迁移记录。
    source_url 为空时使用合成 URL ``migrated://tender/{id}``。

    Args:
        db: 异步数据库 session（函数内部会 commit）

    Returns:
        迁移结果 dict::

            {
                "total": int,     # Tender 表总记录数
                "migrated": int,  # 本次新迁移数
                "skipped": int,   # 跳过（已迁移）数
            }
    """
    result = {"total": 0, "migrated": 0, "skipped": 0}

    tenders = (await db.execute(select(Tender))).scalars().all()
    result["total"] = len(tenders)

    existing_urls = await _get_existing_source_urls(db)

    for tender in tenders:
        source_url = tender.source_url or f"migrated://tender/{tender.id}"
        if source_url in existing_urls:
            result["skipped"] += 1
            continue

        # 1. 创建 TenderProject（四层聚合根）
        project = TenderProject(
            canonical_name=tender.project_name or f"未命名项目-{tender.id}",
            industry_category="other",
            resolution_status="resolved",
        )
        db.add(project)
        await db.flush()

        # 2. 创建 TenderNotice（业务公告）
        notice = TenderNotice(
            project_id=project.project_id,
            notice_type=_map_notice_type(tender.notice_type),
            canonical_title=tender.project_name or f"未命名公告-{tender.id}",
            publish_date=tender.publish_time,
            status="active",
        )
        db.add(notice)
        await db.flush()

        # 3. 创建 NoticeSource（来源页面）
        source = NoticeSource(
            notice_id=notice.notice_id,
            source_url=source_url,
            source_platform=tender.source_platform or "unknown",
            platform_type=_map_platform_type(tender.source_platform),
            publication_role="original",
            source_quality="unknown",
            source_group=f"tender-{tender.id}",
        )
        db.add(source)
        await db.flush()

        # 4. 创建 NoticeVersion（初始抓取版本）
        raw_text = tender.core_content or ""
        version = NoticeVersion(
            notice_source_id=source.notice_source_id,
            http_status=200,
            content_sha256=_content_sha256(raw_text),
            raw_text_sha256=_content_sha256(tender.source_raw_text or raw_text),
            change_type="initial",
        )
        db.add(version)

        existing_urls.add(source_url)
        result["migrated"] += 1

    await db.commit()
    logger.info(
        "迁移完成: total=%d migrated=%d skipped=%d",
        result["total"], result["migrated"], result["skipped"],
    )
    return result
