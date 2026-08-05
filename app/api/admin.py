"""管理后台路由（admin CRUD）。

S-7 拆分：从 app/api/auth.py 拆出，auth.py 只保留认证工具与依赖。

工程规范：
- admin_router 不受 API key 中间件影响（在 main.py 中独立挂载）。
- 所有路由用 Depends(verify_admin) 校验 X-Admin-Secret 头。
- 统一错误响应 {code, data, msg}。

拆分说明（保证单文件 ≤300 行，公开接口不变）：
- 本文件保留：admin_router 定义、User/ApiKey 模型与 CRUD 路由。
- 白名单路由 → app/api/admin_whitelist.py
- 数据删除路由 → app/api/admin_deletion.py
- 子模块在底部 import 时将各自路由注册到 admin_router 上。
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


# ==== 子模块路由注册 ====
# 在 admin_router 定义之后 import，子模块将各自路由注册到 admin_router 上。
# 顺序：白名单 → 数据删除（与原 admin.py 中的路由顺序保持一致）。
from app.api import admin_whitelist  # noqa: E402,F401
from app.api import admin_deletion  # noqa: E402,F401
