"""W3 Demo 按名称查询组织画像 + 5 维度公开活动观察度端点（任务2）。

提供：
- GET /api/demo/orgs/by-name/{name:path}  按名称查询，命中后端组织库并返回 5 维度公开活动观察度

优先查真实数据库（按 purchaser_name/winner_name 匹配）；未命中时维度评分返回 null
（data_source="no_data"），不再用哈希伪造评分。
兼容前端按 /api/org/{name} 习惯调用，见 demo_api.py 里的别名注册。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import get_db
from app.api.demo_5d_credit import (
    _build_5d_credit,
    _build_5d_credit_no_data,
    _find_org_meta,
)
from app.api.demo_org_query import _query_real_org_by_name

router = APIRouter(tags=["demo"])


@router.get("/orgs/by-name/{name:path}", summary="按名称查询组织画像 + 5 维度公开活动观察度")
async def demo_org_by_name(name: str, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """任务2：/api/demo/orgs/by-name/{name} — 按名称查询，命中后端组织库并返回 5 维度公开活动观察度。

    优先查真实数据库（按 purchaser_name/winner_name 匹配）；未命中时维度评分返回 null
    （data_source="no_data"），不再用哈希伪造评分。
    兼容前端按 /api/org/{name} 习惯调用，见 demo_api.__init__ 里的别名注册。
    """
    # ===== 优先查真实数据库（_query_real_org_by_name 逻辑保持不变）=====
    real_profile = await _query_real_org_by_name(name, db)
    if real_profile is not None:
        data_source = "real"
        meta = real_profile["meta"]
        real_activity = real_profile["activity"]
        real_top3 = real_profile["top3_purchasers"]
        real_concentration = real_profile["top3_concentration"]
        real_platforms = real_profile["platforms"]
    else:
        # 真实数据未命中：data_source=no_data，维度评分返回 null（不伪造数字）
        data_source = "no_data"
        real_activity = None
        real_top3 = None
        real_concentration = None
        real_platforms = None
        meta = _find_org_meta(name)
        if meta is None:
            # 未命中真实数据与样本库：占位元数据保证其余演示字段可渲染（不伪造评分）
            meta = {
                "org_id": f"org_unknown_{name[:8]}",
                "org_type": "未知类型",
                "region": "未登记区域",
                "total_projects": 0,
                "total_amount_yuan": 0,
                "award_win_rate": 0.0,
                "active_days_30d": 0,
                "amount_consistency_score": 0.0,
                "type_coverage_count": 0,
            }
    if data_source == "real":
        # 5 维度对齐 observation_signals.py 口径（集中度25/金额20/频率20/地域15/采购人20）
        dims = _build_5d_credit(meta)
    else:
        # 真实数据未命中：每个维度 score 为 None，不伪造数字
        dims = _build_5d_credit_no_data()
    # v4.1 §9.1: 不输出信用评分/综合评分
    overall = None
    # 复用原 demo/organizations/{org_id} 返回的画像字段，再叠加 5 维度 + overall
    org_id = meta["org_id"]
    if real_activity is not None:
        # 真实数据库聚合的活跃度数据
        days_90 = real_activity["daily"]
        activity_total = real_activity["total"]
        activity_tender = real_activity["tender_count"]
        activity_award = real_activity["award_count"]
    else:
        # 样本数据 fallback
        today = datetime(2026, 7, 27)
        days_90 = []
        import random as _r
        _r.seed(hash(org_id) & 0x7FFFFFFF)
        for i in range(90):
            d = today - timedelta(days=89 - i)
            days_90.append({
                "date": d.strftime("%Y-%m-%d"),
                "count": 1 + _r.randint(0, 5),
            })
        activity_total = sum(d["count"] for d in days_90)
        activity_tender = int(activity_total * 0.6)
        activity_award = int(activity_total * 0.35)

    if real_top3 is not None:
        top3_purchasers = real_top3
        top3_concentration = real_concentration
    else:
        top3_purchasers = [
            {"name": meta.get("org_type", "组织") + " 内部采购部", "count": max(20, meta["total_projects"] // 8), "ratio": 0.42},
            {"name": meta.get("region", "本区") + " 政府采购中心", "count": max(10, meta["total_projects"] // 15), "ratio": 0.27},
            {"name": "第三方代理机构（通用）", "count": max(5, meta["total_projects"] // 30), "ratio": 0.14},
        ]
        top3_concentration = sum(p["count"] for p in top3_purchasers) / max(meta["total_projects"], 1)
    waste_bids = [
        {"project_name": meta["org_type"] + " 设备采购废标示例①", "waste_date": "2026-06-15", "reason": "有效投标人不足3家"},
        {"project_name": meta["org_type"] + " 信息化项目废标示例②", "waste_date": "2026-05-20", "reason": "资格审查不通过"},
    ]
    return JSONResponse(content={
        "code": 200,
        "data": {
            "org_id": org_id,
            "org_name": name,
            "org_type": meta["org_type"],
            "region": meta["region"],
            # 5 维度公开活动观察度（对齐 observation_signals.py 口径）
            "observation_score": None,  # v4.1 §9.1: 不输出信用评分
            "observation_note": "基于公开招投标数据的观察信号，不输出信用评分（v4.1 §9.1）",
            "credit_dimensions": dims,
            "data_source": data_source,
            # 活动画像（兼容 org_profile.html 原字段）
            "activity_90d": {
                "total": activity_total,
                "tender_count": activity_tender,
                "award_count": activity_award,
                "daily": days_90,
            },
            "top3_purchasers": top3_purchasers,
            "top3_concentration": round(top3_concentration, 3) if top3_concentration else round(sum(p["ratio"] for p in top3_purchasers), 3),
            "waste_bid_related": waste_bids,
            "waste_bid_count": len(waste_bids),
            "data_completeness": {
                "platforms": real_platforms or ["中国政府采购网", "全国公共资源交易平台", f"{meta.get('region', '本地')}采购网"],
                "time_range": "2025-01-01 至 2026-07-27",
                "total_notices": meta["total_projects"],
                "tender_count": int(meta["total_projects"] * 0.6),
                "award_count": int(meta["total_projects"] * 0.35),
                "correction_count": int(meta["total_projects"] * 0.05),
                "completeness_score": meta["amount_consistency_score"],
                "missing_fields": ["联系人电话（隐私脱敏）", "代理机构联系方式"],
            },
        },
        "msg": "ok",
    })
