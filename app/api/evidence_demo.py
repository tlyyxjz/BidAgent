"""证据展示 API（Demo 用）.

对应 v4.1 第十一章 11.2 核心交互：点击字段高亮原文证据.

路由：
- GET /api/tenders/{tender_id}/evidence  返回字段列表+证据+偏移量+原文
- GET /api/tenders/{tender_id}/fields     返回字段列表（简化版）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import get_db
from app.models.tender import Tender
from app.models.evidence import ExtractedField, Evidence, FieldEvidenceLink

router = APIRouter(prefix="/api/tenders", tags=["evidence_demo"])


class EvidenceResponse(BaseModel):
    """单条证据响应."""
    evidence_id: int
    evidence_text: str
    context_before: str | None = None
    context_after: str | None = None
    raw_start: int
    raw_end: int
    match_method: str
    verified: bool
    evidence_role: str
    sequence: int
    is_required: bool


class FieldResponse(BaseModel):
    """单条字段响应."""
    field_id: int
    field_name: str
    field_status: str
    raw_value: str | None = None
    support_level: str
    support_reason: str | None = None
    amount_type: str | None = None
    lot_id: str | None = None
    evidences: list[EvidenceResponse] = []


class TenderEvidenceResponse(BaseModel):
    """公告+字段+证据完整响应."""
    tender_id: int
    project_name: str
    notice_type: str | None = None
    source_platform: str | None = None
    source_url: str | None = None
    core_content: str  # 原文（用于前端高亮）
    fields: list[FieldResponse] = []


@router.get("/{tender_id}/evidence", response_model=TenderEvidenceResponse)
async def get_tender_evidence(
    tender_id: int,
    db: AsyncSession = Depends(get_db),
) -> TenderEvidenceResponse:
    """获取公告的字段和证据完整数据（含原文，用于前端高亮）."""
    # 查 Tender
    tender = (await db.execute(
        select(Tender).where(Tender.id == tender_id)
    )).scalar_one_or_none()
    if not tender:
        raise HTTPException(status_code=404, detail="公告不存在")

    # 查所有字段
    fields_result = await db.execute(
        select(ExtractedField)
        .where(ExtractedField.tender_id == tender_id)
        .order_by(ExtractedField.id)
    )
    fields = fields_result.scalars().all()

    field_responses = []
    for f in fields:
        # 查该字段的证据关联
        links_result = await db.execute(
            select(FieldEvidenceLink, Evidence)
            .join(Evidence, FieldEvidenceLink.evidence_id == Evidence.id)
            .where(FieldEvidenceLink.field_id == f.id)
            .order_by(FieldEvidenceLink.sequence)
        )
        evidences = []
        for link, ev in links_result:
            evidences.append(EvidenceResponse(
                evidence_id=ev.id,
                evidence_text=ev.evidence_text,
                context_before=ev.context_before,
                context_after=ev.context_after,
                raw_start=ev.raw_start,
                raw_end=ev.raw_end,
                match_method=ev.match_method,
                verified=ev.verified,
                evidence_role=link.evidence_role,
                sequence=link.sequence,
                is_required=link.is_required,
            ))

        field_responses.append(FieldResponse(
            field_id=f.id,
            field_name=f.field_name,
            field_status=f.field_status,
            raw_value=f.raw_value,
            support_level=f.support_level,
            support_reason=f.support_reason,
            amount_type=f.amount_type,
            lot_id=f.lot_id,
            evidences=evidences,
        ))

    return TenderEvidenceResponse(
        tender_id=tender.id,
        project_name=tender.project_name,
        notice_type=tender.notice_type,
        source_platform=tender.source_platform,
        source_url=tender.source_url,
        core_content=tender.core_content or "",
        fields=field_responses,
    )


@router.get("/{tender_id}/fields", response_model=list[FieldResponse])
async def get_tender_fields(
    tender_id: int,
    db: AsyncSession = Depends(get_db),
) -> list[FieldResponse]:
    """获取公告的字段列表（不含原文，轻量版）."""
    fields_result = await db.execute(
        select(ExtractedField)
        .where(ExtractedField.tender_id == tender_id)
        .order_by(ExtractedField.id)
    )
    fields = fields_result.scalars().all()
    if not fields:
        raise HTTPException(status_code=404, detail="公告无字段或不存在")

    field_responses = []
    for f in fields:
        links_result = await db.execute(
            select(FieldEvidenceLink, Evidence)
            .join(Evidence, FieldEvidenceLink.evidence_id == Evidence.id)
            .where(FieldEvidenceLink.field_id == f.id)
            .order_by(FieldEvidenceLink.sequence)
        )
        evidences = []
        for link, ev in links_result:
            evidences.append(EvidenceResponse(
                evidence_id=ev.id,
                evidence_text=ev.evidence_text,
                context_before=ev.context_before,
                context_after=ev.context_after,
                raw_start=ev.raw_start,
                raw_end=ev.raw_end,
                match_method=ev.match_method,
                verified=ev.verified,
                evidence_role=link.evidence_role,
                sequence=link.sequence,
                is_required=link.is_required,
            ))

        field_responses.append(FieldResponse(
            field_id=f.id,
            field_name=f.field_name,
            field_status=f.field_status,
            raw_value=f.raw_value,
            support_level=f.support_level,
            support_reason=f.support_reason,
            amount_type=f.amount_type,
            lot_id=f.lot_id,
            evidences=evidences,
        ))

    return field_responses
