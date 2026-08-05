"""W2-05 证据查询接口。

从 evidence_repository.py 拆分而来，承载只读查询：
- get_field_with_evidence：查询字段及其关联证据
- get_tender_fields：查询 Tender 的所有字段
"""
from __future__ import annotations

from typing import List, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.evidence import (
    Evidence,
    ExtractedField,
    FieldEvidenceLink,
)


# ========== 查询接口 ==========


async def get_field_with_evidence(
    db: AsyncSession, field_id: int
) -> Tuple[ExtractedField, List[Tuple[Evidence, FieldEvidenceLink]]]:
    """查询字段及其关联证据。

    Returns:
        (field, [(evidence, link), ...]) 按 sequence 排序
    """
    result = await db.execute(
        select(ExtractedField).where(ExtractedField.id == field_id)
    )
    field_obj = result.scalars().first()
    if not field_obj:
        raise ValueError(f"ExtractedField not found: id={field_id}")

    result = await db.execute(
        select(Evidence, FieldEvidenceLink)
        .join(FieldEvidenceLink, FieldEvidenceLink.evidence_id == Evidence.id)
        .where(FieldEvidenceLink.field_id == field_id)
        .order_by(FieldEvidenceLink.sequence)
    )
    evidence_links = [
        (evidence, link) for evidence, link in result.all()
    ]

    return field_obj, evidence_links


async def get_tender_fields(
    db: AsyncSession, tender_id: int, only_current: bool = True
) -> List[ExtractedField]:
    """查询 Tender 的所有字段。

    Args:
        db: 异步数据库 session
        tender_id: Tender ID
        only_current: 是否只返回当前版本
    """
    stmt = select(ExtractedField).where(ExtractedField.tender_id == tender_id)
    if only_current:
        stmt = stmt.where(ExtractedField.is_current == True)  # noqa: E712
    stmt = stmt.order_by(ExtractedField.field_name, ExtractedField.version_id.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())
