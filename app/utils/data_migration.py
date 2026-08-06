"""数据迁移工具：将扁平 Tender 表数据迁移到四层实体表。

对应《标小智 项目总体规划 v4.1》第四章四层聚合结构。
实现委托给 app.processors.entity_sync.upsert_tender_entities
（含组织实体与参与方关系同步），本模块只负责全量遍历与汇总。

迁移映射（每条 Tender → 四层链 + 组织关系）：
    Tender.project_name    → TenderProject.canonical_name
    Tender.notice_type     → TenderNotice.notice_type（经映射）
    Tender.source_url      → NoticeSource.source_url（兼作幂等去重键）
    Tender.source_platform → NoticeSource.source_platform
    Tender.core_content    → NoticeVersion.content_sha256
    Tender.tender_org      → Organization + PartyRole(purchaser)
    Tender.agency          → Organization + PartyRole(agency)
    Tender.win_company     → Organization + PartyRole(winner)

幂等性：以 NoticeSource.source_url 为去重键，重复运行跳过已迁移记录。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tender import Tender
from app.processors.entity_sync import (
    sync_identifiers_from_fields,
    sync_participants_from_fields,
    upsert_tender_entities,
)
from app.utils.logger import get_logger

logger = get_logger("data_migration")


async def migrate_tender_to_four_layer(db: AsyncSession) -> dict:
    """将现有 Tender 表数据迁移到四层实体表。

    每条 Tender 记录创建对应的四层链与组织/参与方关系：
    TenderProject → TenderNotice → NoticeSource → NoticeVersion
    + Organization / PartyRole / NoticeParticipant

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

    for tender in tenders:
        created = await upsert_tender_entities(db, tender)
        result["migrated" if created else "skipped"] += 1

    await db.commit()
    logger.info(
        "迁移完成: total={} migrated={} skipped={}",
        result["total"], result["migrated"], result["skipped"],
    )
    return result


async def migrate_participants_from_fields(db: AsyncSession) -> dict:
    """第二遍迁移：从 extracted_fields 回填参与方关系。

    历史数据的组织名存在抽取字段表（purchaser_name/winner_name）而非
    tenders 表组织列，本函数在四层链建立后补齐
    Organization / PartyRole / NoticeParticipant。

    幂等：重复运行跳过已存在的参与方记录。

    Returns:
        {"tenders": int, "participants": int}
    """
    tenders = (await db.execute(select(Tender))).scalars().all()
    total = 0
    total_ids = 0
    for tender in tenders:
        total += await sync_participants_from_fields(db, tender)
        total_ids += await sync_identifiers_from_fields(db, tender)
    await db.commit()
    logger.info(
        "参与方回填完成: tenders={} participants={} identifiers={}",
        len(tenders), total, total_ids,
    )
    return {
        "tenders": len(tenders),
        "participants": total,
        "identifiers": total_ids,
    }
