"""API Key 认证中间件（仅保留认证工具与依赖）。

S-7 拆分：管理后台路由（admin_router）已迁移到 app/api/admin.py。

工程规范：
- 所有中间件用 async/await。
- API Key 以 HMAC-SHA256 摘要存储（v4.1 §13.1 升级），用 `hmac.compare_digest`
  constant-time 比较。HMAC 引入服务端 SECRET_KEY，即使数据库泄露也无法离线爆破。
- Bearer token 认证。
- Admin 路由不能被认证中间件拦截（admin_router 在 main.py 中单独挂载）。
"""

from __future__ import annotations

import hashlib
import secrets
import warnings

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.database import get_db
from app.models.user import ApiKey, User, utc_now
from app.utils.credentials import (
    generate_api_key as _generate_raw_api_key,
    hash_api_key,
    verify_api_key as _credentials_verify_api_key,
)
from app.utils.logger import get_logger

logger = get_logger("auth")


__all__ = [
    "hash_api_key",
    "hash_api_key_sha256_deprecated",
    "generate_api_key",
    "verify_api_key",
    "verify_admin",
]


# ==== 工具函数 ====

def hash_api_key_sha256_deprecated(key: str) -> str:
    """[已废弃] 返回 API key 的纯 SHA256 hex digest。

    .. deprecated:: v4.1
        该函数仅保留用于历史数据迁移参考，不再用于新存储的 API Key。
        新代码应使用 :func:`app.utils.credentials.hash_api_key`（HMAC-SHA256）。
        纯 SHA256 是无盐哈希，数据库泄露后易遭离线字典爆破；HMAC 引入服务端
        SECRET_KEY 显著提升抗爆破能力。
    """
    warnings.warn(
        "hash_api_key_sha256_deprecated 已废弃，请使用 "
        "app.utils.credentials.hash_api_key（HMAC-SHA256）替代。",
        DeprecationWarning,
        stacklevel=2,
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def generate_api_key() -> str:
    """生成 cryptographically secure 的 API key（带前缀，便于识别）。

    v4.1 §13.1：底层调用 :func:`app.utils.credentials.generate_api_key`
    （`secrets.token_urlsafe(32)`，256 位熵），保留 `sk_` 前缀以维持既有
    应用层约定与测试兼容性。
    """
    return "sk_" + _generate_raw_api_key()


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

    v4.1 §13.1：使用 `app.utils.credentials.hash_api_key`（HMAC-SHA256）计算
    摘要并在数据库中查找；找到后再用 `credentials.verify_api_key` 进行常量时间
    二次校验，作为纵深防御。

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
        # 且 key_hash 已 HMAC-SHA256，无法通过时序反推明文 key。MVP 可接受。
        raise _auth_error("Invalid API key")

    # 常量时间二次校验（v4.1 §13.1 纵深防御）：即便 DB 索引命中，仍用
    # hmac.compare_digest 比对 raw key 的 HMAC 与存储摘要，杜绝时序侧信道。
    if not _credentials_verify_api_key(raw_api_key, api_key_obj.key_hash):
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
