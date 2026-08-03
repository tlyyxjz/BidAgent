"""HTTP content cache manager (v4.1 sec 5.3 compliance layer).

Responsibilities:
- Cache HTTP response body + ETag + Last-Modified per URL.
- Provide conditional request headers (If-None-Match / If-Modified-Since).
- Handle 304 Not Modified by returning cached body.
- Reduce repeat access to remote sites (v4.1 sec 5.2 compliance).

Design:
- In-memory cache (single-process scraper; no Redis needed).
- Coroutine-safe (asyncio.Lock protects cache dict).
- TTL-based expiry + LRU eviction at max_entries.
- Logs only URL prefix, never body content.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional

from app.utils.logger import get_logger

logger = get_logger("cache_manager")


_DEFAULT_CACHE_TTL_SECONDS: Optional[float] = 3600.0
_DEFAULT_MAX_ENTRIES = 500


@dataclass
class CacheEntry:
    """HTTP response cache entry."""

    url: str
    body: str
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    content_type: Optional[str] = None
    fetched_at: float = field(default_factory=time.time)
    hit_count: int = 0

    def is_expired(self, ttl: Optional[float]) -> bool:
        """Check if cache entry is expired."""
        if ttl is None:
            return False
        return (time.time() - self.fetched_at) > ttl

    def conditional_headers(self) -> dict[str, str]:
        """Build conditional request headers."""
        headers: dict[str, str] = {}
        if self.etag:
            headers["If-None-Match"] = self.etag
        if self.last_modified:
            headers["If-Modified-Since"] = self.last_modified
        return headers


class CacheManager:
    """HTTP content cache manager (ETag / Last-Modified / body cache)."""

    def __init__(
        self,
        ttl: Optional[float] = _DEFAULT_CACHE_TTL_SECONDS,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
    ) -> None:
        self._ttl: Optional[float] = ttl
        self._max_entries: int = max_entries
        self._cache: dict[str, CacheEntry] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    async def get(self, url: str) -> Optional[CacheEntry]:
        """Get cache entry; returns None if missing or expired."""
        async with self._lock:
            entry = self._cache.get(url)
            if entry is None:
                return None
            if entry.is_expired(self._ttl):
                del self._cache[url]
                logger.debug("cache expired url=%s", url[:80])
                return None
            entry.hit_count += 1
            return entry

    async def set(
        self,
        url: str,
        body: str,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> CacheEntry:
        """Write cache entry; LRU evict at max_entries."""
        async with self._lock:
            if url not in self._cache and len(self._cache) >= self._max_entries:
                self._evict_lru()
            entry = CacheEntry(
                url=url, body=body, etag=etag,
                last_modified=last_modified, content_type=content_type,
            )
            self._cache[url] = entry
            return entry

    async def invalidate(self, url: str) -> None:
        """Invalidate cache for a URL."""
        async with self._lock:
            self._cache.pop(url, None)

    async def get_conditional_headers(self, url: str) -> dict[str, str]:
        """Get conditional request headers for a URL."""
        entry = await self.get(url)
        if entry is None:
            return {}
        return entry.conditional_headers()

    async def handle_304(self, url: str) -> Optional[str]:
        """Handle 304 Not Modified; return cached body."""
        entry = await self.get(url)
        if entry is None:
            logger.warning("304 received but no cache url=%s", url[:80])
            return None
        return entry.body

    def _evict_lru(self) -> None:
        """LRU eviction (caller must hold lock)."""
        if not self._cache:
            return
        oldest_url = min(self._cache, key=lambda u: self._cache[u].fetched_at)
        del self._cache[oldest_url]

    def reset(self) -> None:
        """Clear all cache (test helper)."""
        self._cache.clear()

    def stats(self) -> dict[str, int]:
        """Return cache stats."""
        total_hits = sum(e.hit_count for e in self._cache.values())
        return {
            "entries": len(self._cache),
            "total_hits": total_hits,
            "max_entries": self._max_entries,
        }


cache_manager = CacheManager(
    ttl=_DEFAULT_CACHE_TTL_SECONDS,
    max_entries=_DEFAULT_MAX_ENTRIES,
)
