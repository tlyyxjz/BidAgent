"""W3 Demo 组织实体画像端点（按 org_id）。

提供：
- GET /api/demo/organizations/{org_id}  组织实体画像（中标活跃度 + Top3 采购人集中度 + 废标关联 + 数据完整性）
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["demo"])


@router.get("/organizations/{org_id}")
async def demo_org_profile(org_id: str) -> JSONResponse:
    """Demo: 组织实体画像（中标活跃度 + Top3 采购人集中度 + 废标关联 + 数据完整性）。"""
    org_name_map = {
        "org_001": "北第三医院",
        "org_002": "中国科学院计算技术研究所",
        "org_003": "北京市教育委员会",
    }
    org_name = org_name_map.get(org_id, f"组织 {org_id}")
    today = datetime(2026, 7, 27)
    days_90 = []
    for i in range(90):
        d = today - timedelta(days=89 - i)
        days_90.append({
            "date": d.strftime("%Y-%m-%d"),
            "count": 2 + (i * 7 % 5),
        })
    top3_purchasers = [
        {"name": "北京大学第三医院", "count": 156, "ratio": 0.45},
        {"name": "北京市海淀区卫健委", "count": 89, "ratio": 0.26},
        {"name": "中国医学科学院", "count": 52, "ratio": 0.15},
    ]
    waste_bids = [
        {"project_name": "医疗设备采购项目", "waste_date": "2026-06-15", "reason": "有效投标人不足3家"},
        {"project_name": "信息化系统建设", "waste_date": "2026-05-20", "reason": "资格审查不通过"},
    ]
    data_completeness = {
        "platforms": ["中国政府采购网", "北京市政府采购网", "全国公共资源交易平台"],
        "time_range": "2025-01-01 至 2026-07-27",
        "total_notices": 347,
        "tender_count": 210,
        "award_count": 120,
        "correction_count": 17,
        "completeness_score": 87.5,
        "missing_fields": ["联系人电话（隐私脱敏）", "代理机构联系方式"],
    }
    return JSONResponse(content={
        "code": 200,
        "data": {
            "org_id": org_id,
            "org_name": org_name,
            "org_type": "医疗机构",
            "region": "北京市海淀区",
            "activity_90d": {
                "total": 42,
                "tender_count": 28,
                "award_count": 14,
                "daily": days_90,
            },
            "top3_purchasers": top3_purchasers,
            "top3_concentration": 0.86,
            "waste_bid_related": waste_bids,
            "waste_bid_count": len(waste_bids),
            "data_completeness": data_completeness,
        },
        "msg": "ok",
    })
