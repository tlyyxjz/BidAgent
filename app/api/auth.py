"""API Key 认证中间件（仅保留认证工具与依赖）。

S-7 拆分：管理后台路由（admin_router）已迁移到 app/api/admin.py。

工程规范：
- 所有中间件用 async/await。
- API Key 以 SHA256 hash 存储，用 `secrets.compare_digest` constant-time 比较。
- Bearer token 认证。
- Admin 路由不能被认证中间件拦截（admin_router 在 main.py 中单独挂载）。
"""

from __future__ import annotations

import hashlib
import secrets

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.database import get_db
from app.models.user import ApiKey, User, utc_now
from app.utils.logger import get_logger

logger = get_logger("auth")


# ==== 工具函数 ====

def hash_api_key(key: str) -> str:
    """返回 API key 的 SHA256 hex digest。"""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def generate_api_key() -> str:
    """生成 cryptographically secure 的 API key（带前缀，便于识别）。"""
    return "sk_" + secrets.token_urlsafe(32)


def _auth_error(detail: str) -> HTTPException:
    """构造 401 Bearer 认证错误。"""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


# ==== 认证依赖 ====

async def verify_api_key(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> tuple[User, ApiKey, str]:
    """从 Bearer token 验证 API key，返回 (user, api_key_obj, raw_api_key)。

    Raises:
        HTTPException 401: 缺少/无效 Bearer token。
        HTTPException 403: 用户或 key 已禁用。
    """
    authorization = request.headers.get("Authorization", "")
    parts = authorization.split()

    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise _auth_error("Missing or invalid Bearer token")

    raw_api_key = parts[1]
    key_hash = hash_api_key(raw_api_key)

    result = await db.execute(
        select(ApiKey).where(ApiKey.key_hash == key_hash)
    )
    api_key_obj = result.scalar_one_or_none()

    if api_key_obj is None:
        # 注意：数据库查询本身不是 constant-time，但对 401 错误响应时间差异极小，
        # 且 key_hash 已 SHA256，无法通过时序反推明文 key。MVP 可接受。
        raise _auth_error("Invalid API key")

    if not api_key_obj.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API key has been revoked",
        )

    user_result = await db.execute(
        select(User).where(User.id == api_key_obj.user_id)
    )
    user = user_result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is inactive or deleted",
        )

    # 更新 last_used_at（不阻塞请求）
    api_key_obj.last_used_at = utc_now()
    await db.commit()

    return user, api_key_obj, raw_api_key


async def verify_admin(request: Request) -> None:
    """验证管理员密钥（X-Admin-Secret 头）。"""
    provided = request.headers.get("X-Admin-Secret", "")
    if secrets.compare_digest(provided, settings.ADMIN_SECRET):
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid admin secret",
    )
