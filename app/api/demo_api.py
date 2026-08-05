"""W3 Demo 数据 API（Mock 接口，供前端静态页面使用）。

按功能职责拆分为多个子模块，每个子模块定义自己的 sub-router，
本模块定义统一的 demo_router 并 include 所有 sub-router。

提供：
- GET /api/demo/fields/{field_id}              单个字段的证据详情（带偏移量）
- GET /api/demo/tenders/{tender_id}/fields     招标字段列表（含证据）
- GET /api/demo/sources/{source_id}/versions   版本历史链
- GET /api/demo/organizations/{org_id}         组织实体画像
- GET /api/demo/orgs/by-name/{name:path}       按名称查询组织画像 + 5 维度公开活动观察度
- GET /api/demo/report                          生成 Word 报告
- POST /api/demo/pipeline/start                 启动真实 6 Agent pipeline
- GET /api/demo/pipeline/status                 查询真实 pipeline 阶段进度
- GET /api/demo/collector/status                采集进度聚合数据
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.demo_collector import router as demo_collector_router
from app.api.demo_fields import router as demo_fields_router
from app.api.demo_org_by_name import (
    demo_org_by_name,
    router as demo_org_by_name_router,
)
from app.api.demo_org_profile import router as demo_org_profile_router
from app.api.demo_pipeline import router as demo_pipeline_router
from app.api.demo_report import router as demo_report_router
from app.api.demo_source_versions import router as demo_source_versions_router

router = APIRouter(prefix="/api/demo", tags=["demo"])

# 合并所有子 router（sub-router 路径相对于 /api/demo 前缀）
router.include_router(demo_collector_router)
router.include_router(demo_fields_router)
router.include_router(demo_org_by_name_router)
router.include_router(demo_org_profile_router)
router.include_router(demo_pipeline_router)
router.include_router(demo_report_router)
router.include_router(demo_source_versions_router)

__all__ = ["router", "demo_org_by_name"]
