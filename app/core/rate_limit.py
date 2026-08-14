"""基于 slowapi + Redis 的速率限制。

工程规范：
- 免费用户 5 次/天（按 API key hash 限制）。
- 付费用户不限制。
- Redis 不可用时 fallback 到内存计数（开发环境）。
- 速率限制 key 用 SHA256(api_key) 避免明文落 Redis。
"""

from __future__ import annotations

import time
from hashlib import sha256
from typing import Any

from fastapi import HTTPException, Request, status
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings
from app.models.user import PLAN_FREE, PLAN_PRO, PLAN_STARTER
from app.utils.logger import get_logger

logger = get_logger("rate_limit")


def _rate_limit_key(request: Request) -> str:
    """生成速率限制 key：优先用 API key 的 SHA256，否则用客户端 IP。"""
    authorization = request.headers.get("Authorization", "")
    api_key: str | None = None
    if authorization.lower().startswith("bearer "):
        api_key = authorization[7:].strip()

    x_api_key = request.headers.get("X-API-Key")
    if not api_key and x_api_key:
        api_key = x_api_key.strip()

    if api_key:
        return "key:" + sha256(api_key.encode("utf-8")).hexdigest()
    return "ip:" + get_remote_address(request)


# 全局 limiter 实例（默认限额留空，端点上按用户套餐动态决定）
limiter = Limiter(
    key_func=_rate_limit_key,
    default_limits=[],
    storage_uri=settings.REDIS_URL,
    strategy="fixed-window",
)


# 内存 fallback 计数器：key -> {day: count}
_memory_counts: dict[str, dict[str, int]] = {}

# Redis 客户端缓存
_redis_client_cache: Any = None


def get_user_daily_limit(plan: str) -> int:
    """根据套餐返回每日限额（-1 表示无限）。"""
    if plan == PLAN_FREE:
        return settings.FREE_TIER_DAILY_LIMIT
    if plan in (PLAN_STARTER, PLAN_PRO):
        return -1  # 付费无限
    return settings.FREE_TIER_DAILY_LIMIT


def _get_redis_client() -> Any:
    """惰性创建 Redis 客户端；失败返回 None。"""
    global _redis_client_cache
    if _redis_client_cache is not None:
        return _redis_client_cache
    try:
        from redis import Redis  # type: ignore[import-not-found]

        client = Redis.from_url(
            settings.REDIS_URL, decode_responses=True, socket_connect_timeout=2
        )
        client.ping()  # 触发连接测试
        _redis_client_cache = client
        return client
    except Exception as exc:  # noqa: BLE001
        logger.debug("Redis 连接失败，使用内存计数器: %s", exc)
        _redis_client_cache = None
        return None


def _increment_daily_count(key: str, day: str, count: int = 1) -> int:
    """增加今日计数，返回当前计数。

    优先用 Redis；不可用则 fallback 到内存计数器。

    Args:
        key: 速率限制 key（SHA256 hash）。
        day: 日期字符串（YYYY-MM-DD）。
        count: 增加的次数（batch 接口按实际 URL 数量计数）。
    """
    if count < 1:
        count = 1

    redis_key = f"bidagent:daily:{day}:{key}"
    client = _get_redis_client()
    if client is not None:
        try:
            new_count = client.incrby(redis_key, count)
            if new_count == count:
                client.expire(redis_key, 86400 * 2)
            return int(new_count)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Redis incr 失败，使用内存计数器: %s", exc)

    # 内存 fallback（BE-H9：开发环境可用，生产环境应配置 Redis）
    logger.warning(
        "Redis unavailable, using in-memory rate limiting (not suitable for production)"
    )
    # 先清理旧 day，再增加 today；清理后若 bucket 为空则从 _memory_counts 移除
    bucket = _memory_counts.get(key)
    if bucket is not None:
        for old_day in list(bucket.keys()):
            if old_day != day:
                del bucket[old_day]
        if not bucket:
            # 跨天后旧 day 已清理光，移除空 bucket
            _memory_counts.pop(key, None)
            bucket = None

    if bucket is None:
        bucket = {}
        _memory_counts[key] = bucket

    bucket[day] = bucket.get(day, 0) + count
    return bucket[day]


async def check_and_increment_rate_limit(
    api_key: str, plan: str, count: int = 1
) -> None:
    """检查并增加今日请求计数。

    免费套餐超过 FREE_TIER_DAILY_LIMIT 抛出 HTTPException(429)。
    付费套餐直接放行。

    Args:
        api_key: 明文 API key（内部 hash 后用作 key）。
        plan: 用户套餐 free/starter/pro。
        count: 本次请求消耗的计数（batch 接口按 URL 数量计）。

    Raises:
        HTTPException: 429 当超出免费套餐每日限额。
        HTTPException: 503 当 RATE_LIMIT_REQUIRE_REDIS=true 且 Redis 不可用。
    """
    limit = get_user_daily_limit(plan)
    if limit < 0:
        return  # 付费无限

    # BE-H9: 生产环境可配置强制要求 Redis（仅对需要限流的免费用户生效）
    if settings.RATE_LIMIT_REQUIRE_REDIS:
        client = _get_redis_client()
        if client is None:
            logger.warning(
                "RATE_LIMIT_REQUIRE_REDIS=true but Redis unavailable, "
                "rejecting request (fail-closed)"
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Rate limiting service unavailable. Please retry later.",
            )

    key = "key:" + sha256(api_key.encode("utf-8")).hexdigest()
    day = time.strftime("%Y-%m-%d", time.gmtime())

    new_count = _increment_daily_count(key, day, count)

    if new_count > limit:
        logger.warning(
            "rate limit exceeded key=%s plan=%s count=%d limit=%d",
            key, plan, new_count, limit,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Daily limit ({limit}) exceeded for free plan. "
                f"Upgrade at https://bidagent.dev/pricing"
            ),
        )


def reset_memory_counter() -> None:
    """测试辅助：重置内存计数器。"""
    _memory_counts.clear()
