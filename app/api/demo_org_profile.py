"""W3 Demo 组织实体画像端点（按 org_id）。

提供：
- GET /api/demo/organizations/{org_id}  组织实体画像（活跃度 + Top3 采购人集中度 + 废标关联 + 数据完整性）

org_id 支持三种格式：
1. 组织实体 ULID（organizations.organization_id）
2. org_{sha1前12位} 哈希 ID（与 v4.1 第 12 节一致）
3. 直接传组织名（URL 编码）

数据诚实性（v4.1「有据可查」原则）：
- 全部指标来自真实 DB 聚合；未命中时 data_source="no_data"，指标归零/置空；
- 废标：当前采集范围不含废标/流标公告，waste_bid_related 恒为空并附说明。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import get_db
from app.models.organization import Organization
from app.api.demo_org_by_name import _WASTE_BID_NOTE, _empty_daily
from app.api.demo_org_query import _query_real_org_by_name
from app.api.v41_organizations import _resolve_org_name

router = APIRouter(tags=["demo"])


async def _resolve_name(org_id: str, db: AsyncSession) -> str | None:
    """org_id → 组织名：ULID（organizations 表）→ 哈希/直接名称（v41 反查）。"""
    # 1) ULID：26 位大写字母数字（四层实体 organization_id）
    if len(org_id) == 26 and org_id.isalnum() and org_id.isupper():
        row = (await db.execute(
            select(Organization.normalized_name)
            .where(Organization.organization_id == org_id)
        )).scalar_one_or_none()
        if row:
            return row
    # 2) org_{sha1-12} 哈希 或 3) 直接名称
    return await _resolve_org_name(org_id, db)


@router.get("/organizations/{org_id}")
async def demo_org_profile(org_id: str, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """Demo: 组织实体画像（真实 DB 聚合；未命中返回诚实空态）。"""
    name = await _resolve_name(org_id, db)
    real = await _query_real_org_by_name(name, db) if name else None

    if real is not None:
        meta = real["meta"]
        data = {
            "org_id": org_id,
            "org_name": name,
            "org_type": meta["org_type"],
            "region": meta["region"],
            "data_source": "real",
            "activity_90d": real["activity"],
            "top3_purchasers": real["top3_purchasers"],
            "top3_concentration": round(real["top3_concentration"], 3),
            "waste_bid_related": [],
            "waste_bid_count": 0,
            "waste_bid_note": _WASTE_BID_NOTE,
            "data_completeness": real["completeness"],
        }
    else:
        data = {
            "org_id": org_id,
            "org_name": name or org_id,
            "org_type": "未知类型",
            "region": "未登记区域",
            "data_source": "no_data",
            "activity_90d": {
                "total": 0,
                "tender_count": 0,
                "award_count": 0,
                "daily": _empty_daily(),
            },
            "top3_purchasers": [],
            "top3_concentration": None,
            "waste_bid_related": [],
            "waste_bid_count": 0,
            "waste_bid_note": _WASTE_BID_NOTE,
            "data_completeness": {
                "platforms": [],
                "time_range": "未知",
                "total_notices": 0,
                "tender_count": 0,
                "award_count": 0,
                "correction_count": 0,
                "completeness_score": None,
                "missing_fields": [],
            },
        }
    return JSONResponse(content={"code": 200, "data": data, "msg": "ok"})
