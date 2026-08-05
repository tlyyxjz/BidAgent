"""管理后台 - v4.1 sec 13.3 数据删除 API 路由。

从 app/api/admin.py 拆出（保证单文件 ≤300 行，公开接口不变）。
本模块在 import 时将路由注册到 admin.admin_router 上：
- POST /admin/deletion/by-source-url
- POST /admin/deletion/by-source-platform
- POST /admin/deletion/notice-source/{source_id}
- POST /admin/deletion/page-snapshot/{version_id}
- POST /admin/deletion/user-authorized-data/{user_id}

依赖 admin.py 中已定义的 admin_router（通过底部 import 触发本模块加载，
避免循环导入：admin.py 先定义 admin_router，再 import 本模块）。
"""
from __future__ import annotations

from fastapi import Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import admin_router
from app.api.auth import verify_admin
from app.models.database import get_db
from app.utils.logger import get_logger

logger = get_logger("admin_deletion")


# ========== v4.1 sec 13.3 数据删除 API ==========

class DeletionRequest(BaseModel):
    """数据删除请求体。"""
    target: str = Field(..., description="删除目标 (URL/platform/source_id/version_id/user_id)")
    request_basis: str = Field(..., description="删除依据 (如 GDPR Article 17 / 用户注销 / 平台下架)")
    operator: str = Field("admin", description="操作人标识")


@admin_router.post("/deletion/by-source-url", dependencies=[Depends(verify_admin)])
async def delete_by_source_url(
    req: DeletionRequest,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """按来源 URL 删除数据 (v4.1 sec 13.3 scope 1)。"""
    from app.services.data_deletion import DataDeletionService
    service = DataDeletionService()
    result = await service.delete_by_source_url(
        db, req.target, req.request_basis, req.operator
    )
    logger.info("deletion by_source_url target=%s counts=%s", req.target, result.deleted_counts)
    return JSONResponse(
        status_code=200,
        content={
            "code": 200,
            "data": {
                "scope": result.scope.value,
                "deleted_counts": result.deleted_counts,
                "audit_id": result.audit_id,
                "executed_at": result.executed_at,
                "error": result.error,
            },
            "msg": "deleted" if result.error is None else result.error,
        },
    )


@admin_router.post("/deletion/by-source-platform", dependencies=[Depends(verify_admin)])
async def delete_by_source_platform(
    req: DeletionRequest,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """按来源平台删除数据 (v4.1 sec 13.3 scope 2)。"""
    from app.services.data_deletion import DataDeletionService
    service = DataDeletionService()
    result = await service.delete_by_source_platform(
        db, req.target, req.request_basis, req.operator
    )
    logger.info("deletion by_source_platform target=%s counts=%s", req.target, result.deleted_counts)
    return JSONResponse(
        status_code=200,
        content={
            "code": 200,
            "data": {
                "scope": result.scope.value,
                "deleted_counts": result.deleted_counts,
                "audit_id": result.audit_id,
                "executed_at": result.executed_at,
                "error": result.error,
            },
            "msg": "deleted" if result.error is None else result.error,
        },
    )


@admin_router.post("/deletion/notice-source/{source_id}", dependencies=[Depends(verify_admin)])
async def delete_notice_source_instance(
    source_id: str,
    req: DeletionRequest,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """删除单个公告来源实例 (v4.1 sec 13.3 scope 3)。

    source_id 在路径中，req.target 会被忽略（以路径参数为准）。
    """
    from app.services.data_deletion import DataDeletionService
    service = DataDeletionService()
    result = await service.delete_notice_source_instance(
        db, source_id, req.request_basis, req.operator
    )
    logger.info("deletion notice_source source_id=%s counts=%s", source_id, result.deleted_counts)
    return JSONResponse(
        status_code=200,
        content={
            "code": 200,
            "data": {
                "scope": result.scope.value,
                "deleted_counts": result.deleted_counts,
                "audit_id": result.audit_id,
                "executed_at": result.executed_at,
                "error": result.error,
            },
            "msg": "deleted" if result.error is None else result.error,
        },
    )


@admin_router.post("/deletion/page-snapshot/{version_id}", dependencies=[Depends(verify_admin)])
async def delete_page_snapshot(
    version_id: str,
    req: DeletionRequest,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """删除页面快照 (v4.1 sec 13.3 scope 4)。"""
    from app.services.data_deletion import DataDeletionService
    service = DataDeletionService()
    result = await service.delete_page_snapshot(
        db, version_id, req.request_basis, req.operator
    )
    logger.info("deletion page_snapshot version_id=%s counts=%s", version_id, result.deleted_counts)
    return JSONResponse(
        status_code=200,
        content={
            "code": 200,
            "data": {
                "scope": result.scope.value,
                "deleted_counts": result.deleted_counts,
                "audit_id": result.audit_id,
                "executed_at": result.executed_at,
                "error": result.error,
            },
            "msg": "deleted" if result.error is None else result.error,
        },
    )


@admin_router.post("/deletion/user-authorized-data/{user_id}", dependencies=[Depends(verify_admin)])
async def delete_user_authorized_data(
    user_id: int,
    req: DeletionRequest,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """删除用户授权数据 (v4.1 sec 13.3 scope 5)。"""
    from app.services.data_deletion import DataDeletionService
    service = DataDeletionService()
    result = await service.delete_user_authorized_data(
        db, user_id, req.request_basis, req.operator
    )
    logger.info("deletion user_authorized_data user_id=%s counts=%s", user_id, result.deleted_counts)
    return JSONResponse(
        status_code=200,
        content={
            "code": 200,
            "data": {
                "scope": result.scope.value,
                "deleted_counts": result.deleted_counts,
                "audit_id": result.audit_id,
                "executed_at": result.executed_at,
                "error": result.error,
            },
            "msg": "deleted" if result.error is None else result.error,
        },
    )
