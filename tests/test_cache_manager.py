"""cache_manager.py unit tests (v4.1 sec 5.3)."""

from __future__ import annotations

import asyncio
import time

import pytest

from app.core.cache_manager import CacheEntry, CacheManager


class TestCacheEntry:
    def test_is_expired_false_when_ttl_none(self) -> None:
        entry = CacheEntry(url="http://x", body="data")
        assert entry.is_expired(None) is False

    def test_is_expired_false_within_ttl(self) -> None:
        entry = CacheEntry(url="http://x", body="data", fetched_at=time.time())
        assert entry.is_expired(3600) is False

    def test_is_expired_true_after_ttl(self) -> None:
        entry = CacheEntry(url="http://x", body="data", fetched_at=time.time() - 7200)
        assert entry.is_expired(3600) is True

    def test_conditional_headers_empty(self) -> None:
        entry = CacheEntry(url="http://x", body="data")
        assert entry.conditional_headers() == {}

    def test_conditional_headers_with_etag(self) -> None:
        entry = CacheEntry(url="http://x", body="data", etag='"abc"')
        headers = entry.conditional_headers()
        assert headers == {"If-None-Match": '"abc"'}

    def test_conditional_headers_with_lm(self) -> None:
        entry = CacheEntry(url="http://x", body="data", last_modified="Mon, 03 Aug 2026 GMT")
        assert "If-Modified-Since" in entry.conditional_headers()

    def test_conditional_headers_both(self) -> None:
        entry = CacheEntry(url="http://x", body="data", etag='"v1"', last_modified="Mon")
        assert len(entry.conditional_headers()) == 2


class TestCacheManagerGetSet:
    @pytest.mark.asyncio
    async def test_get_returns_none_for_missing(self) -> None:
        cm = CacheManager()
        assert await cm.get("http://missing.com") is None

    @pytest.mark.asyncio
    async def test_set_then_get(self) -> None:
        cm = CacheManager()
        await cm.set("http://x.com", "body", etag='"e1"')
        entry = await cm.get("http://x.com")
        assert entry is not None
        assert entry.body == "body"
        assert entry.etag == '"e1"'

    @pytest.mark.asyncio
    async def test_get_increments_hit_count(self) -> None:
        cm = CacheManager()
        await cm.set("http://x.com", "body")
        await cm.get("http://x.com")
        await cm.get("http://x.com")
        entry = await cm.get("http://x.com")
        assert entry is not None
        assert entry.hit_count == 3

    @pytest.mark.asyncio
    async def test_set_overwrites(self) -> None:
        cm = CacheManager()
        await cm.set("http://x.com", "v1")
        await cm.set("http://x.com", "v2")
        entry = await cm.get("http://x.com")
        assert entry is not None
        assert entry.body == "v2"


class TestConditionalHeaders:
    @pytest.mark.asyncio
    async def test_headers_empty_for_miss(self) -> None:
        cm = CacheManager()
        assert await cm.get_conditional_headers("http://x.com") == {}

    @pytest.mark.asyncio
    async def test_headers_returns_etag(self) -> None:
        cm = CacheManager()
        await cm.set("http://x.com", "body", etag='"e1"')
        headers = await cm.get_conditional_headers("http://x.com")
        assert "If-None-Match" in headers

    @pytest.mark.asyncio
    async def test_handle_304_returns_body(self) -> None:
        cm = CacheManager()
        await cm.set("http://x.com", "cached-body")
        assert await cm.handle_304("http://x.com") == "cached-body"

    @pytest.mark.asyncio
    async def test_handle_304_none_for_miss(self) -> None:
        cm = CacheManager()
        assert await cm.handle_304("http://missing.com") is None


class TestTTL:
    @pytest.mark.asyncio
    async def test_expired_removed_on_get(self) -> None:
        cm = CacheManager(ttl=0.01)
        await cm.set("http://x.com", "body")
        await asyncio.sleep(0.02)
        assert await cm.get("http://x.com") is None

    @pytest.mark.asyncio
    async def test_ttl_none_never_expires(self) -> None:
        cm = CacheManager(ttl=None)
        await cm.set("http://x.com", "body")
        await asyncio.sleep(0.01)
        assert await cm.get("http://x.com") is not None


class TestLRU:
    @pytest.mark.asyncio
    async def test_evicts_oldest(self) -> None:
        cm = CacheManager(max_entries=2)
        await cm.set("http://a.com", "a")
        await cm.set("http://b.com", "b")
        await cm.get("http://a.com")
        await cm.set("http://c.com", "c")
        assert await cm.get("http://b.com") is None
        assert await cm.get("http://a.com") is not None
        assert await cm.get("http://c.com") is not None


class TestInvalidate:
    @pytest.mark.asyncio
    async def test_invalidate_removes(self) -> None:
        cm = CacheManager()
        await cm.set("http://x.com", "body")
        await cm.invalidate("http://x.com")
        assert await cm.get("http://x.com") is None

    @pytest.mark.asyncio
    async def test_invalidate_nonexistent_no_error(self) -> None:
        cm = CacheManager()
        await cm.invalidate("http://missing.com")


class TestResetStats:
    @pytest.mark.asyncio
    async def test_reset_clears_all(self) -> None:
        cm = CacheManager()
        await cm.set("http://a.com", "a")
        cm.reset()
        assert await cm.get("http://a.com") is None

    @pytest.mark.asyncio
    async def test_stats(self) -> None:
        cm = CacheManager(max_entries=10)
        await cm.set("http://a.com", "a")
        await cm.set("http://b.com", "b")
        stats = cm.stats()
        assert stats["entries"] == 2
        assert stats["max_entries"] == 10


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_no_race(self) -> None:
        cm = CacheManager(max_entries=100)
        urls = [f"http://x{i}.com" for i in range(20)]
        await asyncio.gather(*[cm.set(u, f"body-{i}") for i, u in enumerate(urls)])
        entries = await asyncio.gather(*[cm.get(u) for u in urls])
        for i, entry in enumerate(entries):
            assert entry is not None
            assert entry.body == f"body-{i}"
