"""v4.1 抽取结果组装逻辑（从 v41_extract.py 拆出）。

职责：
- _build_result_from_tender：从 DB 中已存在的 Tender + ExtractedField + Evidence 组装抽取结果
- _extraction_to_payload：把 ExtractionResult 转为字段 payload（容错处理）
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.evidence import Evidence, ExtractedField, FieldEvidenceLink
from app.models.tender import Tender


async def _build_result_from_tender(tender: Tender, db: AsyncSession) -> dict[str, Any]:
    """从 DB 中已存在的 Tender + ExtractedField + Evidence 组装结果。"""
    fields_rows = (await db.execute(
        select(ExtractedField).where(ExtractedField.tender_id == tender.id)
        .order_by(ExtractedField.id)
    )).scalars().all()

    fields_payload: list[dict[str, Any]] = []
    evidences_count = 0
    for f in fields_rows:
        links = (await db.execute(
            select(FieldEvidenceLink, Evidence)
            .join(Evidence, FieldEvidenceLink.evidence_id == Evidence.id)
            .where(FieldEvidenceLink.field_id == f.id)
            .order_by(FieldEvidenceLink.sequence)
        )).all()
        evidences = [{
            "evidence_id": f"{f.id}_{link.sequence}",
            "text": ev.evidence_text,
            "raw_start": ev.raw_start,
            "raw_end": ev.raw_end,
            "role": link.evidence_role,
            "match_method": ev.match_method,
            "confidence": ev.confidence,
            "verified": ev.verified,
        } for link, ev in links]
        evidences_count += len(evidences)
        fields_payload.append({
            "field_name": f.field_name,
            "field_status": f.field_status,
            "raw_value": f.raw_value,
            "normalized_value": f.normalized_value,
            "amount_type": f.amount_type,
            "support_level": f.support_level,
            "evidences": evidences,
        })

    # 即便无字段也算 succeeded（DB 命中但未抽取过字段，是真实状态）
    return {
        "_status": "succeeded",
        "_error": None,
        "tender_id": tender.id,
        "project_name": tender.project_name,
        "source_platform": tender.source_platform,
        "fields": fields_payload,
        "fields_count": len(fields_payload),
        "evidences_count": evidences_count,
        "extraction_source": "db_cached",
    }


def _extraction_to_payload(extraction: Any) -> list[dict[str, Any]]:
    """把 ExtractionResult 转为字段 payload（容错处理）。"""
    if extraction is None:
        return []
    fields = getattr(extraction, "fields", None)
    if fields is None and isinstance(extraction, dict):
        fields = extraction.get("fields")
    if not fields:
        return []
    payload = []
    for f in fields:
        if hasattr(f, "model_dump"):
            f = f.model_dump()
        if not isinstance(f, dict):
            continue
        payload.append({
            "field_name": f.get("field_name") or f.get("name") or "",
            "field_status": f.get("field_status") or "present",
            "raw_value": f.get("raw_value") or f.get("value"),
            "normalized_value": f.get("normalized_value"),
            "amount_type": f.get("amount_type"),
            "support_level": f.get("support_level") or "unsupported",
            "evidences": f.get("candidate_evidences") or f.get("evidences") or [],
        })
    return payload
