"""v4.1 数据质量统计端点（从 v41_extract.py 拆出）。

提供端点：
- GET /api/stats/quality：获取数据质量和评测统计
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import get_db
from app.models.evidence import Evidence, ExtractedField
from app.models.tender import Tender

from app.api.v41_common import _ok


# ==== 12. GET /api/stats/quality ====

async def get_stats_quality(db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """获取数据质量和评测统计（v4.1 第12节）。"""
    tender_count = (await db.execute(select(func.count(Tender.id)))).scalar() or 0
    field_count = (await db.execute(select(func.count(ExtractedField.id)))).scalar() or 0
    evidence_count = (await db.execute(select(func.count(Evidence.id)))).scalar() or 0
    sl_rows = (await db.execute(
        select(ExtractedField.support_level, func.count(ExtractedField.id))
        .group_by(ExtractedField.support_level)
    )).all()
    fs_rows = (await db.execute(
        select(ExtractedField.field_status, func.count(ExtractedField.id))
        .group_by(ExtractedField.field_status)
    )).all()
    mm_rows = (await db.execute(
        select(Evidence.match_method, func.count(Evidence.id))
        .group_by(Evidence.match_method)
    )).all()
    verified_count = (await db.execute(
        select(func.count(Evidence.id)).where(Evidence.verified.is_(True))
    )).scalar() or 0
    return _ok({
        "tender_count": tender_count,
        "field_count": field_count,
        "evidence_count": evidence_count,
        "verified_evidence_count": verified_count,
        "support_level_distribution": {r[0]: r[1] for r in sl_rows},
        "field_status_distribution": {r[0]: r[1] for r in fs_rows},
        "match_method_distribution": {r[0]: r[1] for r in mm_rows},
        "verification_rate": round(verified_count / evidence_count, 4) if evidence_count else 0.0,
        "server_time": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    })
