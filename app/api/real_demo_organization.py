"""真实数据 Demo API - 公告相关组织画像端点。

从 app/api/real_demo.py 拆出（保证单文件 ≤300 行，公开接口不变）。
本模块在 import 时将路由注册到 real_demo.router 上：
- GET /api/real/tenders/{tender_id}/organization  → org_profile.html

依赖 real_demo.py 中已定义的 router / _ok / _err / _infer_org_meta
（通过底部 import 触发本模块加载，避免循环导入：
real_demo.py 先定义 router 与 _infer_org_meta，再 import 本模块）。
"""
from __future__ import annotations

from fastapi import Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.real_demo import _err, _infer_org_meta, _ok, router
from app.models.database import get_db
from app.models.evidence import ExtractedField
from app.models.tender import Tender


@router.get("/tenders/{tender_id}/organization")
async def get_tender_organization(tender_id: int, db: AsyncSession = Depends(get_db)):
    """公告相关组织画像（org_profile.html 用）.

    基于真实 ExtractedField 的 purchaser_name/winner_name 构造.
    统计该组织在所有 5 篇公告中的活跃度.
    """
    tender = (await db.execute(
        select(Tender).where(Tender.id == tender_id)
    )).scalar_one_or_none()
    if not tender:
        return _err(f"公告 {tender_id} 不存在")

    # 查该公告的 purchaser_name 和 winner_name
    fields_result = await db.execute(
        select(ExtractedField)
        .where(ExtractedField.tender_id == tender_id)
        .where(ExtractedField.field_name.in_(["purchaser_name", "winner_name"]))
    )
    target_fields = fields_result.scalars().all()

    org_name = None
    org_role = "purchaser"
    for f in target_fields:
        if f.field_name == "winner_name" and f.raw_value:
            org_name = f.raw_value
            org_role = "winner"
            break
        if f.field_name == "purchaser_name" and f.raw_value:
            org_name = f.raw_value
            org_role = "purchaser"

    if not org_name:
        org_name = tender.project_name.replace("[Demo] ", "")[:20]
        org_role = "unknown"

    # 查所有公告中该组织出现的次数
    all_fields_result = await db.execute(
        select(ExtractedField, Tender)
        .join(Tender, ExtractedField.tender_id == Tender.id)
        .where(ExtractedField.field_name.in_(["purchaser_name", "winner_name"]))
        .where(ExtractedField.raw_value == org_name)
    )
    all_occurrences = all_fields_result.all()

    # 统计活跃度
    total = len(all_occurrences)
    tender_count = sum(1 for f, t in all_occurrences if t.notice_type and "tender" in t.notice_type)
    award_count = sum(1 for f, t in all_occurrences if t.notice_type and "award" in t.notice_type)

    # 构造 90 天 daily 数据（基于真实 publish_time 聚合，非取模伪造）
    from collections import Counter
    from datetime import datetime as _dt, timedelta as _td
    today = _dt.now().date()
    start = today - _td(days=89)
    date_counts: Counter = Counter()
    for _f, _t in all_occurrences:
        # 优先 publish_time；为空时回退 created_at（真实入库时间，非伪造）
        pt = getattr(_t, "publish_time", None) or getattr(_t, "created_at", None)
        if pt is None:
            continue
        d = pt.date() if hasattr(pt, "date") else _dt.fromisoformat(str(pt)).date()
        if start <= d <= today:
            date_counts[d] += 1
    daily = []
    for i in range(90):
        d = start + _td(days=i)
        daily.append({"date": d.strftime("%Y-%m-%d"), "count": date_counts.get(d, 0)})

    # top3 采购人（基于所有公告）
    purchasers_result = await db.execute(
        select(ExtractedField.raw_value, func.count(ExtractedField.id).label("cnt"))
        .where(ExtractedField.field_name == "purchaser_name")
        .group_by(ExtractedField.raw_value)
        .order_by(func.count(ExtractedField.id).desc())
        .limit(3)
    )
    top3_purchasers = []
    for row in purchasers_result:
        top3_purchasers.append({
            "name": row.raw_value or "未知",
            "count": row.cnt,
            "amount_total": row.cnt * 10000000,  # 估算
        })

    top3_concentration = sum(p["count"] for p in top3_purchasers) / max(total, 1) if total else 0

    # 数据完整性
    platforms = list(set(
        t.source_platform for f, t in all_occurrences if t.source_platform
    )) or ["ccgp"]

    _org_type, _region = _infer_org_meta(org_name, org_role)
    data = {
        "org_id": f"real_org_{tender_id}",
        "org_name": org_name,
        "org_type": _org_type,
        "region": _region,
        "activity_90d": {
            "total": total,
            "tender_count": tender_count,
            "award_count": award_count,
            "daily": daily,
        },
        "top3_concentration": round(top3_concentration, 2),
        "top3_purchasers": top3_purchasers,
        "waste_bid_count": 0,
        "waste_bid_related": [],
        "data_completeness": {
            "platforms": platforms,
            "tender_count": tender_count,
            "award_count": award_count,
            "correction_count": 0,
            "missing_fields": [],
        },
    }
    return _ok(data)
