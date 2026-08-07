"""W3 Demo 按名称查询组织画像 + 5 维度公开活动观察度端点（任务2）。

提供：
- GET /api/demo/orgs/by-name/{name:path}  按名称查询，命中后端组织库并返回 5 维度公开活动观察度

数据诚实性（v4.1「有据可查」原则）：
- 优先查真实数据库（按 purchaser_name/winner_name 匹配）；
- 未命中时 data_source="no_data"，全部指标归零/置空，不再用随机数或样本库伪造；
- 废标数据：当前采集范围不含废标/流标公告，waste_bid_related 恒为空并附说明。
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
)
from app.api.demo_org_query import _query_real_org_by_name

router = APIRouter(tags=["demo"])

# 废标口径说明（当前采集范围事实，随数据源扩展更新）
_WASTE_BID_NOTE = "当前采集范围（ccgp 招标/中标/更正公告）不含废标/流标公告，无关联记录"


def _empty_daily() -> list[dict]:
    """90 天零值 daily（保持前端图表结构可渲染，数值真实为 0）。"""
    today = datetime.now().date()
    return [
        {"date": (today - timedelta(days=89 - i)).strftime("%Y-%m-%d"), "count": 0}
        for i in range(90)
    ]


@router.get("/orgs/by-name/{name:path}", summary="按名称查询组织画像 + 5 维度公开活动观察度")
async def demo_org_by_name(name: str, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """任务2：/api/demo/orgs/by-name/{name} — 按名称查询，命中后端组织库并返回 5 维度公开活动观察度。

    优先查真实数据库（按 purchaser_name/winner_name 匹配）；未命中时 data_source="no_data"，
    全部指标归零/置空（不伪造数字、不使用预置样本库）。
    兼容前端按 /api/org/{name} 习惯调用，见 demo_api.__init__ 里的别名注册。
    """
    # ===== 优先查真实数据库 =====
    real_profile = await _query_real_org_by_name(name, db)
    if real_profile is not None:
        data_source = "real"
        meta = real_profile["meta"]
        activity = real_profile["activity"]
        top3_purchasers = real_profile["top3_purchasers"]
        top3_concentration = real_profile["top3_concentration"]
        completeness = real_profile["completeness"]
        dims = _build_5d_credit(meta)  # 5 维度对齐 observation_signals.py 口径
    else:
        # 真实数据未命中：诚实空态（不伪造活跃度/Top3/完整性）
        data_source = "no_data"
        meta = {
            "org_id": f"org_unknown_{name[:8]}",
            "org_type": "未知类型",
            "region": "未登记区域",
            "total_projects": 0,
            "total_amount_yuan": None,
            "award_win_rate": None,
            "active_days_30d": 0,
            "amount_consistency_score": None,
            "type_coverage_count": 0,
        }
        activity = {
            "total": 0,
            "tender_count": 0,
            "award_count": 0,
            "daily": _empty_daily(),
        }
        top3_purchasers = []
        top3_concentration = None
        completeness = {
            "platforms": [],
            "time_range": "未知",
            "total_notices": 0,
            "tender_count": 0,
            "award_count": 0,
            "correction_count": 0,
            "completeness_score": None,
            "missing_fields": [],
        }
        dims = _build_5d_credit_no_data()

    return JSONResponse(content={
        "code": 200,
        "data": {
            "org_id": meta["org_id"],
            "org_name": name,
            "org_type": meta["org_type"],
            "region": meta["region"],
            # 5 维度公开活动观察度（对齐 observation_signals.py 口径）
            "observation_score": None,  # v4.1 §9.1: 不输出信用评分
            "observation_note": "基于公开招投标数据的观察信号，不输出信用评分（v4.1 §9.1）",
            "credit_dimensions": dims,
            "data_source": data_source,
            # 活动画像（兼容 org_profile.html 原字段）
            "activity_90d": activity,
            "top3_purchasers": top3_purchasers,
            "top3_concentration": (
                round(top3_concentration, 3) if top3_concentration is not None else None
            ),
            "waste_bid_related": [],
            "waste_bid_count": 0,
            "waste_bid_note": _WASTE_BID_NOTE,
            "data_completeness": completeness,
        },
        "msg": "ok",
    })
