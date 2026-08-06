"""Tender → 四层实体 + 组织/参与方同步器（v4.1 第四章）。

职责：把一条 Tender 记录同步为四层聚合链与组织关系：

    TenderProject → TenderNotice → NoticeSource → NoticeVersion
    Organization + PartyRole + NoticeParticipant

幂等性：以 NoticeSource.source_url 为去重键，已存在则整体跳过。
本模块只 flush 不 commit，事务边界由调用方控制
（入库管线统一 commit；回填脚本自行 commit）。
"""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models._extracted_field import ExtractedField
from app.models.organization import (
    Organization,
    PartyRole,
    ROLE_AGENCY,
    ROLE_PURCHASER,
    ROLE_WINNER,
    infer_org_type,
    normalize_org_name,
)
from app.models.tender import Tender
from app.models.tender_project import (
    NoticeParticipant,
    NoticeSource,
    NoticeVersion,
    ProjectIdentifier,
    TenderNotice,
    TenderProject,
    _new_ulid,
)
from app.utils.logger import get_logger

logger = get_logger("entity_sync")

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


def _content_sha256(text: str | None) -> str:
    """计算文本的 SHA256 十六进制摘要（64 字符）。"""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def map_notice_type(raw: str | None) -> str:
    """将 Tender.notice_type 映射到四层模型 notice_type 枚举。

    未知类型统一归为 other（v4.1 第四章 4.3 节 notice_type 枚举）。
    """
    if not raw:
        return "tender"
    return _NOTICE_TYPE_MAP.get(raw, "other")


def map_platform_type(platform: str | None) -> str:
    """将 source_platform 映射到 platform_type 枚举。"""
    if not platform:
        return "unknown"
    government_keywords = ("ccgp", "gov", "ggzy")
    base = platform.lower().split(":")[0]  # 兼容 "平台:来源角色" 格式
    if any(kw in base for kw in government_keywords):
        return "government"
    return "commercial"


async def _get_or_create_org(db: AsyncSession, raw_name: str) -> Organization:
    """按 normalized_name 幂等获取/创建组织实体（v4.1 4.4）。"""
    normalized = normalize_org_name(raw_name) or raw_name.strip()
    result = await db.execute(
        select(Organization).where(Organization.normalized_name == normalized)
    )
    org = result.scalar_one_or_none()
    if org is None:
        org = Organization(
            organization_id=_new_ulid(),
            raw_name=raw_name.strip()[:200],
            normalized_name=normalized[:200],
            org_type=infer_org_type(raw_name),
            disambiguation_confidence=80,
        )
        db.add(org)
        await db.flush()
    return org


async def _add_party(
    db: AsyncSession,
    tender: Tender,
    notice: TenderNotice,
    raw_name: str,
    role: str,
) -> None:
    """写入一对 PartyRole + NoticeParticipant（幂等：唯一索引兜底）。"""
    org = await _get_or_create_org(db, raw_name)

    result = await db.execute(
        select(PartyRole).where(
            PartyRole.organization_id == org.organization_id,
            PartyRole.tender_id == tender.id,
            PartyRole.role == role,
        )
    )
    if result.scalar_one_or_none() is None:
        db.add(
            PartyRole(
                organization_id=org.organization_id,
                tender_id=tender.id,
                role=role,
                raw_name_in_notice=raw_name.strip()[:200],
            )
        )

    result = await db.execute(
        select(NoticeParticipant).where(
            NoticeParticipant.notice_id == notice.notice_id,
            NoticeParticipant.organization_id == org.organization_id,
            NoticeParticipant.participant_role == role,
        )
    )
    if result.scalar_one_or_none() is None:
        db.add(
            NoticeParticipant(
                notice_id=notice.notice_id,
                organization_id=org.organization_id,
                raw_name=raw_name.strip()[:300],
                normalized_name=org.normalized_name[:300],
                participant_role=role,
                resolution_status="resolved",
            )
        )


async def upsert_tender_entities(db: AsyncSession, tender: Tender) -> bool:
    """把一条 Tender 同步为四层实体链 + 组织/参与方关系。

    幂等：source_url 已在 notice_sources 中则整体跳过。
    只 flush 不 commit，事务边界由调用方控制。

    Args:
        db: 异步 session
        tender: 已持久化（有 id）的 Tender 记录

    Returns:
        True=本次新建了实体链；False=已存在跳过
    """
    source_url = tender.source_url or f"migrated://tender/{tender.id}"
    result = await db.execute(
        select(NoticeSource.notice_source_id).where(
            NoticeSource.source_url == source_url
        )
    )
    if result.first() is not None:
        return False

    # 1. TenderProject（四层聚合根）
    project = TenderProject(
        canonical_name=tender.project_name or f"未命名项目-{tender.id}",
        industry_category="other",
        resolution_status="resolved",
    )
    db.add(project)
    await db.flush()

    # 2. TenderNotice（业务公告）
    notice = TenderNotice(
        project_id=project.project_id,
        notice_type=map_notice_type(tender.notice_type),
        canonical_title=tender.project_name or f"未命名公告-{tender.id}",
        publish_date=tender.publish_time,
        status="active",
    )
    db.add(notice)
    await db.flush()

    # 3. NoticeSource（来源页面）
    source = NoticeSource(
        notice_id=notice.notice_id,
        source_url=source_url,
        source_platform=(tender.source_platform or "unknown").split(":")[0],
        platform_type=map_platform_type(tender.source_platform),
        publication_role="original",
        source_quality="unknown",
        source_group=f"tender-{tender.id}",
    )
    db.add(source)
    await db.flush()

    # 4. NoticeVersion（初始抓取版本）
    raw_text = tender.core_content or ""
    db.add(
        NoticeVersion(
            notice_source_id=source.notice_source_id,
            http_status=200,
            content_sha256=_content_sha256(raw_text),
            raw_text_sha256=_content_sha256(tender.source_raw_text or raw_text),
            change_type="initial",
        )
    )

    # 5. 组织实体 + 参与方关系（v4.1 4.4/4.5）
    if tender.tender_org:
        await _add_party(db, tender, notice, tender.tender_org, ROLE_PURCHASER)
    if tender.agency:
        await _add_party(db, tender, notice, tender.agency, ROLE_AGENCY)
    if tender.win_company:
        await _add_party(db, tender, notice, tender.win_company, ROLE_WINNER)

    return True


# 抽取字段 → 参与方角色映射（v4.1 4.5）
_FIELD_ROLE_MAP: dict[str, str] = {
    "purchaser_name": ROLE_PURCHASER,
    "winner_name": ROLE_WINNER,
}


async def sync_participants_from_fields(db: AsyncSession, tender: Tender) -> int:
    """从 extracted_fields 同步参与方关系到 PartyRole + NoticeParticipant。

    覆盖 tenders 表组织列为空的历史数据（实际组织值存在抽取字段表中）。
    幂等：_add_party 内部按 (notice, org, role) 查重。
    只 flush 不 commit，事务边界由调用方控制。

    Args:
        db: 异步 session
        tender: 已持久化的 Tender 记录

    Returns:
        本次处理的参与方条数（含已存在被跳过的）
    """
    # 找到该 tender 对应的公告
    result = await db.execute(
        select(NoticeSource.notice_id).where(
            NoticeSource.source_url
            == (tender.source_url or f"migrated://tender/{tender.id}")
        )
    )
    row = result.first()
    if row is None:
        return 0  # 四层链尚未建立，先跑 migrate_tender_to_four_layer
    notice_id = row[0]

    result = await db.execute(
        select(ExtractedField).where(
            ExtractedField.tender_id == tender.id,
            ExtractedField.field_name.in_(list(_FIELD_ROLE_MAP)),
            ExtractedField.field_status == "present",
            ExtractedField.is_current.is_(True),
        )
    )
    fields = result.scalars().all()

    notice = await db.get(TenderNotice, notice_id)
    if notice is None:
        return 0

    count = 0
    for f in fields:
        raw = (f.raw_value or "").strip()
        if not raw:
            continue
        await _add_party(db, tender, notice, raw, _FIELD_ROLE_MAP[f.field_name])
        count += 1
    return count


async def sync_identifiers_from_fields(db: AsyncSession, tender: Tender) -> int:
    """从 extracted_fields 同步项目标识到 ProjectIdentifier（v4.1 4.2）。

    幂等：按 (project_id, raw_value) 查重。
    只 flush 不 commit，事务边界由调用方控制。

    Returns:
        本次处理的标识条数（含已存在被跳过的）
    """
    result = await db.execute(
        select(TenderNotice.project_id)
        .join(NoticeSource, NoticeSource.notice_id == TenderNotice.notice_id)
        .where(
            NoticeSource.source_url
            == (tender.source_url or f"migrated://tender/{tender.id}")
        )
    )
    row = result.first()
    if row is None:
        return 0
    project_id = row[0]

    result = await db.execute(
        select(ExtractedField).where(
            ExtractedField.tender_id == tender.id,
            ExtractedField.field_name == "project_identifier",
            ExtractedField.field_status == "present",
            ExtractedField.is_current.is_(True),
        )
    )
    fields = result.scalars().all()

    count = 0
    for f in fields:
        raw = (f.raw_value or "").strip()
        if not raw:
            continue
        existing = await db.execute(
            select(ProjectIdentifier.identifier_id).where(
                ProjectIdentifier.project_id == project_id,
                ProjectIdentifier.raw_value == raw[:200],
            )
        )
        if existing.first() is None:
            db.add(
                ProjectIdentifier(
                    project_id=project_id,
                    identifier_type="procurement",
                    raw_value=raw[:200],
                    normalized_value=raw[:200],
                )
            )
        count += 1
    return count
