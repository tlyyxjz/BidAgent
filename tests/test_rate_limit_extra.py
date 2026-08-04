"""Extra tests for app/core/rate_limit.py.

Covers uncovered branches:
- _rate_limit_key: Bearer / X-API-Key / IP fallback
- get_user_daily_limit: free / starter / pro / unknown
- _get_redis_client: success (mock redis), failure fallback
- _increment_daily_count: redis path, redis failure fallback, memory day cleanup
- check_and_increment_rate_limit: paid plan, free under limit, free over limit (429)
- reset_memory_counter
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.core import rate_limit as rate_limit_mod
from app.core.rate_limit import (
    _get_redis_client,
    _increment_daily_count,
    _rate_limit_key,
    check_and_increment_rate_limit,
    get_user_daily_limit,
    reset_memory_counter,
)
from app.models.user import PLAN_FREE, PLAN_PRO, PLAN_STARTER


# ============================================================
# Suite 1: _rate_limit_key (lines 27-40)
# ============================================================

class TestRateLimitKey:
    """Cover _rate_limit_key with Bearer / X-API-Key / IP fallback."""

    def test_bearer_token_key_is_sha256(self) -> None:
        from hashlib import sha256
        request = MagicMock()
        request.headers = {"Authorization": "Bearer abc123"}
        key = _rate_limit_key(request)
        expected = "key:" + sha256(b"abc123").hexdigest()
        assert key == expected

    def test_bearer_token_stripped(self) -> None:
        from hashlib import sha256
        request = MagicMock()
        request.headers = {"Authorization": "Bearer   spaced-key  "}
        key = _rate_limit_key(request)
        expected = "key:" + sha256(b"spaced-key").hexdigest()
        assert key == expected

    def test_x_api_key_fallback(self) -> None:
        from hashlib import sha256
        request = MagicMock()
        request.headers = {"X-API-Key": "xkey123"}
        key = _rate_limit_key(request)
        expected = "key:" + sha256(b"xkey123").hexdigest()
        assert key == expected

    def test_x_api_key_stripped(self) -> None:
        from hashlib import sha256
        request = MagicMock()
        request.headers = {"X-API-Key": "  xkey  "}
        key = _rate_limit_key(request)
        expected = "key:" + sha256(b"xkey").hexdigest()
        assert key == expected

    def test_bearer_takes_precedence_over_x_api_key(self) -> None:
        from hashlib import sha256
        request = MagicMock()
        request.headers = {
            "Authorization": "Bearer bearer-key",
            "X-API-Key": "xkey",
        }
        key = _rate_limit_key(request)
        expected = "key:" + sha256(b"bearer-key").hexdigest()
        assert key == expected

    def test_no_key_falls_back_to_ip(self) -> None:
        request = MagicMock()
        request.headers = {}
        with patch("app.core.rate_limit.get_remote_address", return_value="1.2.3.4"):
            key = _rate_limit_key(request)
        assert key == "ip:1.2.3.4"

    def test_authorization_not_bearer_falls_back(self) -> None:
        request = MagicMock()
        request.headers = {"Authorization": "Basic abc"}
        with patch("app.core.rate_limit.get_remote_address", return_value="5.6.7.8"):
            key = _rate_limit_key(request)
        assert key == "ip:5.6.7.8"

    def test_case_insensitive_bearer_prefix(self) -> None:
        from hashlib import sha256
        request = MagicMock()
        request.headers = {"Authorization": "bearer lowercase-key"}
        key = _rate_limit_key(request)
        expected = "key:" + sha256(b"lowercase-key").hexdigest()
        assert key == expected


# ============================================================
# Suite 2: get_user_daily_limit (lines 59-65)
# ============================================================

class TestGetUserDailyLimit:
    """Cover get_user_daily_limit for each plan."""

    def test_free_plan_returns_configured_limit(self) -> None:
        limit = get_user_daily_limit(PLAN_FREE)
        assert limit == rate_limit_mod.settings.FREE_TIER_DAILY_LIMIT

    def test_starter_plan_unlimited(self) -> None:
        assert get_user_daily_limit(PLAN_STARTER) == -1

    def test_pro_plan_unlimited(self) -> None:
        assert get_user_daily_limit(PLAN_PRO) == -1

    def test_unknown_plan_falls_back_to_free_limit(self) -> None:
        limit = get_user_daily_limit("unknown_plan")
        assert limit == rate_limit_mod.settings.FREE_TIER_DAILY_LIMIT

    def test_empty_plan_falls_back_to_free_limit(self) -> None:
        assert get_user_daily_limit("") == rate_limit_mod.settings.FREE_TIER_DAILY_LIMIT


# ============================================================
# Suite 3: _get_redis_client (lines 68-85)
# ============================================================

class TestGetRedisClient:
    """Cover _get_redis_client success and failure."""

    def setup_method(self) -> None:
        rate_limit_mod._redis_client_cache = None

    def teardown_method(self) -> None:
        rate_limit_mod._redis_client_cache = None

    def test_redis_unavailable_returns_none(self) -> None:
        with patch.dict("sys.modules", {"redis": None}):
            client = _get_redis_client()
        assert client is None

    def test_redis_cached_after_success(self) -> None:
        mock_redis_client = MagicMock()
        mock_redis_client.ping.return_value = True
        mock_redis_class = MagicMock()
        mock_redis_class.from_url.return_value = mock_redis_client

        with patch.dict("sys.modules", {"redis": MagicMock(Redis=mock_redis_class)}):
            client1 = _get_redis_client()
            assert client1 is mock_redis_client
            mock_redis_client.ping.reset_mock()
            client2 = _get_redis_client()
            assert client2 is mock_redis_client
            mock_redis_client.ping.assert_not_called()

    def test_redis_ping_failure_returns_none(self) -> None:
        mock_redis_class = MagicMock()
        mock_redis_class.from_url.return_value.ping.side_effect = ConnectionError("no redis")

        with patch.dict("sys.modules", {"redis": MagicMock(Redis=mock_redis_class)}):
            client = _get_redis_client()
        assert client is None
        assert rate_limit_mod._redis_client_cache is None

    def test_redis_import_error_returns_none(self) -> None:
        with patch("builtins.__import__", side_effect=ImportError("no redis module")):
            client = _get_redis_client()
        assert client is None


# ============================================================
# Suite 4: _increment_daily_count memory path (lines 88-121)
# ============================================================

class TestIncrementDailyCountMemory:
    """Cover _increment_daily_count memory fallback with day cleanup."""

    def setup_method(self) -> None:
        reset_memory_counter()
        rate_limit_mod._redis_client_cache = None

    def teardown_method(self) -> None:
        reset_memory_counter()
        rate_limit_mod._redis_client_cache = None

    def test_memory_increment_returns_count(self) -> None:
        count = _increment_daily_count("key1", "2026-01-01")
        assert count == 1
        count = _increment_daily_count("key1", "2026-01-01")
        assert count == 2

    def test_memory_cleans_old_day(self) -> None:
        _increment_daily_count("key1", "2026-01-01")
        _increment_daily_count("key1", "2026-01-01")
        count = _increment_daily_count("key1", "2026-01-02")
        assert count == 1

    def test_memory_different_keys_independent(self) -> None:
        _increment_daily_count("keyA", "2026-01-01")
        count_b = _increment_daily_count("keyB", "2026-01-01")
        assert count_b == 1

    def test_memory_empty_bucket_after_day_cleanup_removed(self) -> None:
        _increment_daily_count("key1", "2026-01-01")
        assert "key1" in rate_limit_mod._memory_counts
        _increment_daily_count("key1", "2026-01-02")
        assert "key1" in rate_limit_mod._memory_counts
        assert rate_limit_mod._memory_counts["key1"] == {"2026-01-02": 1}


# ============================================================
# Suite 5: _increment_daily_count redis path (lines 93-102)
# ============================================================

class TestIncrementDailyCountRedis:
    """Cover _increment_daily_count redis path."""

    def setup_method(self) -> None:
        reset_memory_counter()
        rate_limit_mod._redis_client_cache = None

    def teardown_method(self) -> None:
        reset_memory_counter()
        rate_limit_mod._redis_client_cache = None

    def test_redis_incr_success(self) -> None:
        mock_client = MagicMock()
        mock_client.incr.return_value = 1
        rate_limit_mod._redis_client_cache = mock_client

        count = _increment_daily_count("key1", "2026-01-01")
        assert count == 1
        mock_client.incr.assert_called_once_with("scrapeflow:daily:2026-01-01:key1")

    def test_redis_incr_sets_expire_on_first(self) -> None:
        mock_client = MagicMock()
        mock_client.incr.return_value = 1
        rate_limit_mod._redis_client_cache = mock_client

        _increment_daily_count("key1", "2026-01-01")
        mock_client.expire.assert_called_once_with(
            "scrapeflow:daily:2026-01-01:key1", 86400 * 2
        )

    def test_redis_incr_no_expire_on_subsequent(self) -> None:
        mock_client = MagicMock()
        mock_client.incr.return_value = 2
        rate_limit_mod._redis_client_cache = mock_client

        _increment_daily_count("key1", "2026-01-01")
        mock_client.expire.assert_not_called()

    def test_redis_failure_falls_back_to_memory(self) -> None:
        mock_client = MagicMock()
        mock_client.incr.side_effect = ConnectionError("redis down")
        rate_limit_mod._redis_client_cache = mock_client

        count = _increment_daily_count("key1", "2026-01-01")
        assert count == 1
        assert rate_limit_mod._memory_counts["key1"]["2026-01-01"] == 1


# ============================================================
# Suite 6: check_and_increment_rate_limit (lines 124-157)
# ============================================================

class TestCheckAndIncrementRateLimit:
    """Cover check_and_increment_rate_limit paid/free/over-limit."""

    def setup_method(self) -> None:
        reset_memory_counter()
        rate_limit_mod._redis_client_cache = None

    def teardown_method(self) -> None:
        reset_memory_counter()
        rate_limit_mod._redis_client_cache = None

    @pytest.mark.asyncio
    async def test_paid_plan_unlimited_no_raise(self) -> None:
        await check_and_increment_rate_limit("key1", PLAN_PRO)
        await check_and_increment_rate_limit("key1", PLAN_STARTER)
        await check_and_increment_rate_limit("key1", PLAN_PRO)

    @pytest.mark.asyncio
    async def test_free_plan_under_limit_no_raise(self) -> None:
        await check_and_increment_rate_limit("free-key-1", PLAN_FREE)
        await check_and_increment_rate_limit("free-key-1", PLAN_FREE)
        await check_and_increment_rate_limit("free-key-1", PLAN_FREE)

    @pytest.mark.asyncio
    async def test_free_plan_over_limit_raises_429(self) -> None:
        limit = rate_limit_mod.settings.FREE_TIER_DAILY_LIMIT
        for _ in range(limit):
            await check_and_increment_rate_limit("free-key-2", PLAN_FREE)
        with pytest.raises(HTTPException) as exc_info:
            await check_and_increment_rate_limit("free-key-2", PLAN_FREE)
        assert exc_info.value.status_code == 429
        assert "Daily limit" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_free_plan_different_keys_independent(self) -> None:
        limit = rate_limit_mod.settings.FREE_TIER_DAILY_LIMIT
        for _ in range(limit):
            await check_and_increment_rate_limit("key-A", PLAN_FREE)
        with pytest.raises(HTTPException):
            await check_and_increment_rate_limit("key-A", PLAN_FREE)
        await check_and_increment_rate_limit("key-B", PLAN_FREE)

    @pytest.mark.asyncio
    async def test_rate_limit_key_hashed(self) -> None:
        from hashlib import sha256
        await check_and_increment_rate_limit("plaintext-key", PLAN_FREE)
        expected_key = "key:" + sha256(b"plaintext-key").hexdigest()
        assert expected_key in rate_limit_mod._memory_counts


# ============================================================
# Suite 7: reset_memory_counter (line 160-162)
# ============================================================

class TestResetMemoryCounter:
    """Cover reset_memory_counter."""

    def test_reset_clears_all_counts(self) -> None:
        _increment_daily_count("key1", "2026-01-01")
        _increment_daily_count("key2", "2026-01-01")
        assert len(rate_limit_mod._memory_counts) > 0
        reset_memory_counter()
        assert rate_limit_mod._memory_counts == {}

    def test_reset_idempotent(self) -> None:
        reset_memory_counter()
        reset_memory_counter()
        assert rate_limit_mod._memory_counts == {}


# ============================================================
# Suite 8: limiter instance
# ============================================================

class TestLimiterInstance:
    """Cover the global limiter instance configuration."""

    def test_limiter_uses_rate_limit_key(self) -> None:
        from app.core.rate_limit import limiter
        assert limiter._key_func is _rate_limit_key

    def test_limiter_default_limits_empty(self) -> None:
        from app.core.rate_limit import limiter
        assert limiter._default_limits == []
