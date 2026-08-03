"""管理后台路由（admin CRUD）。

S-7 拆分：从 app/api/auth.py 拆出，auth.py 只保留认证工具与依赖。

工程规范：
- admin_router 不受 API key 中间件影响（在 main.py 中独立挂载）。
- 所有路由用 Depends(verify_admin) 校验 X-Admin-Secret 头。
- 统一错误响应 {code, data, msg}。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import generate_api_key, hash_api_key, verify_admin
from app.models.database import get_db
from app.models.user import (
    ApiKey,
    PLAN_FREE,
    VALID_PLANS,
    User,
)
from app.utils.logger import get_logger

logger = get_logger("admin_api")

admin_router = APIRouter(prefix="/admin", tags=["admin"])


# ==== 请求/响应模型 ====

class CreateUserRequest(BaseModel):
    """创建用户请求体。"""
    email: EmailStr
    plan: str = Field(default=PLAN_FREE)


class CreateApiKeyRequest(BaseModel):
    """创建 API key 请求体。"""
    name: str = Field(default="default", max_length=128)


class UserResponse(BaseModel):
    """用户响应。"""
    id: int
    email: str
    plan: str
    is_active: bool
    created_at: datetime


class ApiKeyResponse(BaseModel):
    """API key 创建响应（含明文 key，仅创建时返回一次）。"""
    id: int
    name: str
    api_key: str  # 明文，仅此一次
    user_id: int
    created_at: datetime


# ==== 路由 ====

@admin_router.get("/users", dependencies=[Depends(verify_admin)])
async def list_users(db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """列出所有用户。"""
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    users = result.scalars().all()
    return JSONResponse(
        status_code=200,
        content={
            "code": 200,
            "data": [
                {
                    "id": u.id,
                    "email": u.email,
                    "plan": u.plan,
                    "is_active": u.is_active,
                    "created_at": u.created_at.isoformat() if u.created_at else None,
                }
                for u in users
            ],
            "msg": "ok",
        },
    )


@admin_router.post("/users", dependencies=[Depends(verify_admin)])
async def create_user(
    payload: CreateUserRequest, db: AsyncSession = Depends(get_db)
) -> JSONResponse:
    """创建新用户。"""
    if payload.plan not in VALID_PLANS:
        return JSONResponse(
            status_code=400,
            content={"code": 400, "data": None, "msg": f"plan 必须是 {VALID_PLANS} 之一"},
        )

    # 检查 email 重复
    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none() is not None:
        return JSONResponse(
            status_code=409,
            content={"code": 409, "data": None, "msg": "email 已存在"},
        )

    user = User(email=payload.email, plan=payload.plan)
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return JSONResponse(
        status_code=201,
        content={
            "code": 201,
            "data": {
                "id": user.id,
                "email": user.email,
                "plan": user.plan,
                "is_active": user.is_active,
                "created_at": user.created_at.isoformat() if user.created_at else None,
            },
            "msg": "ok",
        },
    )


@admin_router.post(
    "/users/{user_id}/api-keys",
    dependencies=[Depends(verify_admin)],
)
async def create_api_key(
    user_id: int,
    payload: CreateApiKeyRequest,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """为指定用户生成新的 API key（明文仅返回一次）。"""
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        return JSONResponse(
            status_code=404,
            content={"code": 404, "data": None, "msg": "user not found"},
        )

    raw_key = generate_api_key()
    api_key = ApiKey(
        user_id=user_id,
        key_hash=hash_api_key(raw_key),
        name=payload.name,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)

    return JSONResponse(
        status_code=201,
        content={
            "code": 201,
            "data": {
                "id": api_key.id,
                "name": api_key.name,
                "api_key": raw_key,  # 明文，仅此一次
                "user_id": api_key.user_id,
                "created_at": api_key.created_at.isoformat() if api_key.created_at else None,
            },
            "msg": "ok",
        },
    )


@admin_router.get(
    "/users/{user_id}/api-keys",
    dependencies=[Depends(verify_admin)],
)
async def list_api_keys(
    user_id: int, db: AsyncSession = Depends(get_db)
) -> JSONResponse:
    """列出指定用户的所有 API key（不含明文）。"""
    result = await db.execute(
        select(ApiKey)
        .where(ApiKey.user_id == user_id)
        .order_by(ApiKey.created_at.desc())
    )
    keys = result.scalars().all()
    return JSONResponse(
        status_code=200,
        content={
            "code": 200,
            "data": [
                {
                    "id": k.id,
                    "name": k.name,
                    "is_active": k.is_active,
                    "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
                    "created_at": k.created_at.isoformat() if k.created_at else None,
                }
                for k in keys
            ],
            "msg": "ok",
        },
    )




# ==== v4.1 §5.2 source whitelist management ====

class AddSourceRequest(BaseModel):
    """Add a source to the whitelist."""
    domain: str = Field(..., description="domain or URL, auto-normalized")
    platform_name: str = Field(..., max_length=200)
    platform_type: str = Field(default="commercial", description="government/authorized/commercial/unknown")
    notes: str = Field(default="", max_length=500)


class DecommissionRequest(BaseModel):
    """Decommission a source."""
    reason: str = Field(..., min_length=1, max_length=500, description="decommission reason, required")


@admin_router.get("/sources/whitelist")
async def list_whitelist_sources(
    status: str | None = None,
    _: None = Depends(verify_admin),
) -> JSONResponse:
    """List all sources in the whitelist (filterable by status)."""
    from app.core.source_whitelist import source_whitelist

    sources = source_whitelist.list_sources(status=status)
    return JSONResponse(
        status_code=200,
        content={"code": 200, "data": sources, "msg": "ok"},
    )


@admin_router.post("/sources/whitelist")
async def add_whitelist_source(
    req: AddSourceRequest,
    _: None = Depends(verify_admin),
) -> JSONResponse:
    """Add a new source to the whitelist."""
    from app.core.source_whitelist import source_whitelist

    try:
        entry = await source_whitelist.add_source(
            domain=req.domain,
            platform_name=req.platform_name,
            platform_type=req.platform_type,
            notes=req.notes,
        )
        logger.info(
            "whitelist source added domain=%s platform=%s",
            entry.domain, entry.platform_name,
        )
        return JSONResponse(
            status_code=201,
            content={"code": 201, "data": entry.to_dict(), "msg": "created"},
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"code": 400, "data": None, "msg": str(exc)},
        )


@admin_router.post("/sources/whitelist/{domain}/decommission")
async def decommission_whitelist_source(
    domain: str,
    req: DecommissionRequest,
    _: None = Depends(verify_admin),
) -> JSONResponse:
    """Decommission a source (stop new scraping, do not delete historical data)."""
    from app.core.source_whitelist import source_whitelist

    try:
        entry = await source_whitelist.decommission(domain, reason=req.reason)
        logger.warning(
            "whitelist source decommissioned domain=%s reason=%s",
            entry.domain, req.reason,
        )
        return JSONResponse(
            status_code=200,
            content={"code": 200, "data": entry.to_dict(), "msg": "decommissioned"},
        )
    except KeyError as exc:
        return JSONResponse(
            status_code=404,
            content={"code": 404, "data": None, "msg": str(exc)},
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"code": 400, "data": None, "msg": str(exc)},
        )


@admin_router.post("/sources/whitelist/{domain}/recommission")
async def recommission_whitelist_source(
    domain: str,
    _: None = Depends(verify_admin),
) -> JSONResponse:
    """Recommission a previously decommissioned source."""
    from app.core.source_whitelist import source_whitelist

    try:
        entry = await source_whitelist.recommission(domain)
        logger.info("whitelist source recommissioned domain=%s", entry.domain)
        return JSONResponse(
            status_code=200,
            content={"code": 200, "data": entry.to_dict(), "msg": "recommissioned"},
        )
    except KeyError as exc:
        return JSONResponse(
            status_code=404,
            content={"code": 404, "data": None, "msg": str(exc)},
        )



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
