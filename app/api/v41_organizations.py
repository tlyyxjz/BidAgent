"""v4.1 组织画像端点（从 v41_extract.py 拆出）。

提供端点：
- GET /api/organizations/search：搜索组织实体
- GET /api/organizations/{org_id}：获取组织实体公开活动画像
"""
from __future__ import annotations

import hashlib

from fastapi import Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import get_db
from app.models.evidence import ExtractedField
from app.models.tender import Tender

from app.api.v41_common import _err, _ok


# ==== 8. GET /api/organizations/search ====

async def organizations_search(
    keyword: str | None = Query(None, description="组织名关键词"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """搜索组织实体（v4.1 第12节）。聚合 purchaser_name/winner_name 及 Tender 三字段。"""
    org_count: dict[str, int] = {}
    # 从 ExtractedField 聚合
    rows = (await db.execute(
        select(ExtractedField.raw_value, ExtractedField.field_name,
               func.count(ExtractedField.id).label("cnt"))
        .where(ExtractedField.field_name.in_(["purchaser_name", "winner_name"]))
        .group_by(ExtractedField.raw_value, ExtractedField.field_name)
    )).all()
    for r in rows:
        if r.raw_value:
            org_count[r.raw_value] = org_count.get(r.raw_value, 0) + r.cnt
    # 从 Tender 表三字段聚合
    trows = (await db.execute(
        select(Tender.tender_org, Tender.win_company, Tender.agency)
    )).all()
    for tr in trows:
        for name in (tr.tender_org, tr.win_company, tr.agency):
            if name:
                org_count[name] = org_count.get(name, 0) + 1
    items = []
    for name, cnt in org_count.items():
        if keyword and keyword not in name:
            continue
        items.append({
            "org_id": f"org_{hashlib.sha1(name.encode('utf-8')).hexdigest()[:12]}",
            "org_name": name,
            "occurrence_count": cnt,
        })
    items.sort(key=lambda x: -x["occurrence_count"])
    total = len(items)
    page_items = items[(page - 1) * page_size: page * page_size]
    return _ok({"items": page_items, "total": total, "page": page, "page_size": page_size})


# ==== 9. GET /api/organizations/{org_id} ====

async def get_organization(org_id: str, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """获取组织实体公开活动画像（v4.1 第12节，调用 observation_signals 模块）。

    org_id 支持两种格式：
    - org_{sha1前12位}：通过哈希反查组织名
    - 直接传组织名：URL 编码后传入
    """
    org_name = await _resolve_org_name(org_id, db)
    if not org_name:
        return _err(f"组织 {org_id} 不存在")
    win_records = await _collect_org_records(org_name, db)
    from app.processors.observation_signals import analyze_observation_signals
    result = analyze_observation_signals(
        org_id=org_id, org_name=org_name, win_records=win_records,
    )
    signals_payload = [
        {"signal_name": s.signal_name, "observed_value": s.observed_value,
         "observation_period": s.observation_period, "coverage_note": s.coverage_note,
         "details": s.details, "disclaimer": s.disclaimer}
        for s in result.signals
    ]
    data_completeness = {
        "coverage_platforms": result.coverage_platforms,
        "coverage_time_range": result.coverage_time_range,
        "valid_notice_count": result.valid_notice_count,
        "entity_resolution_status": result.entity_resolution_status,
        "signal_caliber": result.signal_caliber,
    }
    profile_payload = None
    if result.profile is not None:
        prof = result.profile
        profile_payload = {
            "win_count": prof.win_count,
            "total_win_amount": prof.total_win_amount,
            "main_purchasers": prof.main_purchasers,
            "main_agencies": prof.main_agencies,
            "active_regions": prof.active_regions,
            "first_win_date": prof.first_win_date,
            "last_win_date": prof.last_win_date,
        }
    return _ok({
        "org_id": org_id,
        "org_name": org_name,
        "normalized_name": result.normalized_name,
        # 数据完整性字段（v4.1 第 9.4 节）：同时保留扁平字段以兼容现有测试
        "coverage_platforms": result.coverage_platforms,
        "coverage_time_range": result.coverage_time_range,
        "valid_notice_count": result.valid_notice_count,
        "entity_resolution_status": result.entity_resolution_status,
        "signal_caliber": result.signal_caliber,
        "data_completeness": data_completeness,
        "profile": profile_payload,
        "signals": signals_payload,
        "summary": result.summary,
        "analyzed_at": result.analyzed_at,
    })


async def _resolve_org_name(org_id: str, db: AsyncSession) -> str | None:
    """根据 org_id 反查组织名，支持哈希 ID 与直接名称两种格式。"""
    if org_id.startswith("org_") and len(org_id) == 16:
        rows = (await db.execute(
            select(ExtractedField.raw_value).where(
                ExtractedField.field_name.in_(["purchaser_name", "winner_name"])
            )
        )).scalars().all()
        trows = (await db.execute(
            select(Tender.tender_org, Tender.win_company, Tender.agency)
        )).all()
        names = set(n for n in rows if n)
        for tr in trows:
            for n in (tr.tender_org, tr.win_company, tr.agency):
                if n:
                    names.add(n)
        for name in names:
            if f"org_{hashlib.sha1(name.encode('utf-8')).hexdigest()[:12]}" == org_id:
                return name
        return None
    return org_id


async def _collect_org_records(org_name: str, db: AsyncSession) -> list[dict]:
    """收集某组织作为中标人的公开记录，用于 observation_signals 信号计算。"""
    rows = (await db.execute(
        select(ExtractedField, Tender)
        .join(Tender, ExtractedField.tender_id == Tender.id)
        .where(ExtractedField.field_name == "winner_name")
        .where(ExtractedField.raw_value == org_name)
    )).all()
    records = []
    for _f, t in rows:
        records.append({
            "purchaser": t.tender_org or "",
            "region": t.location or "",
            "win_amount": float(t.win_amount) if t.win_amount else 0,
            "win_date": t.publish_time.isoformat() if t.publish_time else "",
            "award_date": t.publish_time.isoformat() if t.publish_time else "",
            "source_platform": t.source_platform or "",
            "notice_title": t.project_name or "",
        })
    return records
