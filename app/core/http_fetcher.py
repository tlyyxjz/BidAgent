"""v4.1 §5.3 HTTP 抓取器组件。

职责：纯 HTTP 抓取（不依赖浏览器），支持：
- ETag / Last-Modified 条件请求
- 内容压缩（gzip/deflate）
- 超时与重试
- User-Agent 管理

与 browser_renderer.py 的区别：
- http_fetcher：用于静态 HTML / JSON API，轻量快速
- browser_renderer：用于需要 JS 渲染的动态页面

与 scraper.py 的关系：
- scraper.py 是完整采集流程（含 SSRF/robots/rate_limit/snapshot/template_check）
- http_fetcher 是其中的 HTTP 抓取环节，可被 scraper.py 委托调用
"""
from __future__ import annotations

import gzip
import zlib
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from app.utils.logger import get_logger

logger = get_logger("http_fetcher")

# 默认 User-Agent（v4.1 §5.3 合规要求：不伪装浏览器）
DEFAULT_USER_AGENT = "BidAgent/1.0 (Compatible; Public Notice Aggregator)"

# 默认超时（秒）
DEFAULT_TIMEOUT = 15.0

# 默认重试次数
DEFAULT_MAX_RETRIES = 2


@dataclass
class FetchResult:
    """HTTP 抓取结果。"""

    url: str
    status_code: int
    content: str  # 解码后的文本
    raw_bytes: bytes  # 原始字节
    headers: dict[str, str] = field(default_factory=dict)
    elapsed_ms: float = 0.0
    from_cache: bool = False  # 是否来自缓存（304 响应）
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        """是否成功（2xx 状态码）。"""
        return 200 <= self.status_code < 300

    @property
    def etag(self) -> Optional[str]:
        """响应中的 ETag。"""
        return self.headers.get("etag") or self.headers.get("ETag")

    @property
    def last_modified(self) -> Optional[str]:
        """响应中的 Last-Modified。"""
        return self.headers.get("last-modified") or self.headers.get("Last-Modified")


class HttpFetcher:
    """HTTP 抓取器。

    v4.1 §5.3 组件 1：负责纯 HTTP 抓取，支持条件请求和内容压缩。
    """

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.max_retries = max_retries

    async def fetch(
        self,
        url: str,
        headers: Optional[dict[str, str]] = None,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
    ) -> FetchResult:
        """抓取 URL 内容。

        Args:
            url: 目标 URL
            headers: 额外请求头
            etag: 上次的 ETag（用于条件请求，若服务器返回 304 则 from_cache=True）
            last_modified: 上次的 Last-Modified（用于条件请求）

        Returns:
            FetchResult
        """
        import time

        request_headers = {"User-Agent": self.user_agent}
        if headers:
            request_headers.update(headers)
        # 条件请求头
        if etag:
            request_headers["If-None-Match"] = etag
        if last_modified:
            request_headers["If-Modified-Since"] = last_modified
        request_headers["Accept-Encoding"] = "gzip, deflate"

        start = time.perf_counter()
        last_error: Optional[str] = None

        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=self.timeout,
                    follow_redirects=True,
                    verify=True,
                ) as client:
                    resp = await client.get(url, headers=request_headers)

                    elapsed_ms = (time.perf_counter() - start) * 1000
                    raw_bytes = resp.content

                    # 解压
                    content = self._decompress(raw_bytes, resp.headers)

                    # 304 表示内容未变
                    from_cache = resp.status_code == 304

                    return FetchResult(
                        url=url,
                        status_code=resp.status_code,
                        content=content,
                        raw_bytes=raw_bytes,
                        headers=dict(resp.headers),
                        elapsed_ms=elapsed_ms,
                        from_cache=from_cache,
                    )
            except httpx.TimeoutException as e:
                last_error = f"timeout: {e}"
                logger.warning(f"HTTP fetch timeout (attempt {attempt + 1}): {url}")
            except httpx.HTTPError as e:
                last_error = f"http_error: {e}"
                logger.warning(f"HTTP fetch error (attempt {attempt + 1}): {url} - {e}")

        elapsed_ms = (time.perf_counter() - start) * 1000
        return FetchResult(
            url=url,
            status_code=0,
            content="",
            raw_bytes=b"",
            headers={},
            elapsed_ms=elapsed_ms,
            error=last_error,
        )

    def _decompress(self, raw_bytes: bytes, headers: dict[str, str]) -> str:
        """根据 Content-Encoding 解压响应体。"""
        encoding = (headers.get("content-encoding") or "").lower()
        if encoding == "gzip":
            try:
                raw_bytes = gzip.decompress(raw_bytes)
            except OSError:
                pass
        elif encoding == "deflate":
            try:
                raw_bytes = zlib.decompress(raw_bytes)
            except zlib.error:
                try:
                    raw_bytes = zlib.decompress(raw_bytes, -zlib.MAX_WBITS)
                except zlib.error:
                    pass

        # 尝试 UTF-8 解码
        for charset in ("utf-8", "gbk", "gb2312", "latin-1"):
            try:
                return raw_bytes.decode(charset)
            except (UnicodeDecodeError, LookupError):
                continue
        return raw_bytes.decode("utf-8", errors="replace")
