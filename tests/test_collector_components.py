"""v4.1 §5.3 采集层三组件测试。

覆盖：
- HttpFetcher：fetch / _decompress / FetchResult 属性
- BrowserRenderer：render / render_list / RenderResult 属性（mock BrowserPool）
- Collector：collect 流程编排（mock 各组件）
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.collector import CollectRequest, CollectResult, Collector
from app.core.browser_renderer import BrowserRenderer, RenderResult
from app.core.http_fetcher import DEFAULT_USER_AGENT, FetchResult, HttpFetcher


# ========== HttpFetcher 测试 ==========


class TestHttpFetcher:
    """HttpFetcher 组件测试。"""

    def test_init_defaults(self):
        """默认参数初始化。"""
        fetcher = HttpFetcher()
        assert fetcher.user_agent == DEFAULT_USER_AGENT
        assert fetcher.timeout == 15.0
        assert fetcher.max_retries == 2

    def test_init_custom(self):
        """自定义参数初始化。"""
        fetcher = HttpFetcher(user_agent="Test/1.0", timeout=30.0, max_retries=5)
        assert fetcher.user_agent == "Test/1.0"
        assert fetcher.timeout == 30.0
        assert fetcher.max_retries == 5

    def test_decompress_plain(self):
        """无压缩内容直接解码。"""
        fetcher = HttpFetcher()
        result = fetcher._decompress(b"hello world", {})
        assert result == "hello world"

    def test_decompress_utf8(self):
        """UTF-8 中文解码。"""
        fetcher = HttpFetcher()
        result = fetcher._decompress("招标公告".encode("utf-8"), {})
        assert result == "招标公告"

    def test_decompress_gzip(self):
        """gzip 解压。"""
        import gzip

        fetcher = HttpFetcher()
        original = "测试内容".encode("utf-8")
        compressed = gzip.compress(original)
        result = fetcher._decompress(compressed, {"content-encoding": "gzip"})
        assert result == "测试内容"

    def test_decompress_invalid_encoding(self):
        """无效编码不崩溃，返回 UTF-8 replacement。"""
        fetcher = HttpFetcher()
        result = fetcher._decompress(b"\xff\xfe", {})
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_fetch_returns_fetch_result(self):
        """fetch 返回 FetchResult 对象。"""
        fetcher = HttpFetcher(max_retries=0)

        # mock httpx.AsyncClient
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"hello"
        mock_resp.headers = {"content-type": "text/html"}

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get.return_value = mock_resp

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await fetcher.fetch("https://example.com")

        assert isinstance(result, FetchResult)
        assert result.url == "https://example.com"
        assert result.status_code == 200
        assert result.content == "hello"
        assert result.success is True
        assert result.from_cache is False

    @pytest.mark.asyncio
    async def test_fetch_304_from_cache(self):
        """304 响应标记 from_cache=True。"""
        fetcher = HttpFetcher(max_retries=0)

        mock_resp = MagicMock()
        mock_resp.status_code = 304
        mock_resp.content = b""
        mock_resp.headers = {"etag": '"abc"'}

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get.return_value = mock_resp

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await fetcher.fetch("https://example.com", etag='"old-etag"')

        assert result.status_code == 304
        assert result.from_cache is True
        assert result.success is False  # 304 不是 2xx

    @pytest.mark.asyncio
    async def test_fetch_timeout_returns_error(self):
        """超时返回 error。"""
        import httpx

        fetcher = HttpFetcher(timeout=0.01, max_retries=0)

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get.side_effect = httpx.TimeoutException("timeout")

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await fetcher.fetch("https://example.com")

        assert result.success is False
        assert result.error is not None
        assert "timeout" in result.error


# ========== FetchResult 测试 ==========


class TestFetchResult:
    """FetchResult 属性测试。"""

    def test_success_2xx(self):
        r = FetchResult(url="u", status_code=200, content="c", raw_bytes=b"c")
        assert r.success is True

    def test_success_3xx_not_success(self):
        r = FetchResult(url="u", status_code=304, content="", raw_bytes=b"")
        assert r.success is False

    def test_success_4xx_not_success(self):
        r = FetchResult(url="u", status_code=403, content="", raw_bytes=b"")
        assert r.success is False

    def test_etag_property(self):
        r = FetchResult(url="u", status_code=200, content="c", raw_bytes=b"c", headers={"etag": '"abc"'})
        assert r.etag == '"abc"'

    def test_last_modified_property(self):
        r = FetchResult(url="u", status_code=200, content="c", raw_bytes=b"c", headers={"last-modified": "Wed, 01 Jan 2026 00:00:00 GMT"})
        assert r.last_modified == "Wed, 01 Jan 2026 00:00:00 GMT"


# ========== BrowserRenderer 测试 ==========


class TestBrowserRenderer:
    """BrowserRenderer 组件测试。"""

    def test_init_defaults(self):
        r = BrowserRenderer()
        assert r.page_timeout == 30000
        assert r.wait_timeout == 10000

    def test_init_custom(self):
        r = BrowserRenderer(page_timeout=60000, wait_timeout=20000)
        assert r.page_timeout == 60000
        assert r.wait_timeout == 20000

    def test_render_result_success(self):
        r = RenderResult(url="u", html="<html></html>")
        assert r.success is True

    def test_render_result_failure(self):
        r = RenderResult(url="u", html="", error="timeout")
        assert r.success is False

    @pytest.mark.asyncio
    async def test_render_returns_render_result(self):
        """render 返回 RenderResult（mock _acquire_page）。"""
        from contextlib import asynccontextmanager

        renderer = BrowserRenderer()

        # mock page
        mock_page = AsyncMock()
        mock_page.content.return_value = "<html>rendered</html>"
        mock_page.title.return_value = "Test Page"

        # mock _acquire_page 方法
        @asynccontextmanager
        async def fake_acquire_page():
            yield mock_page

        with patch.object(renderer, "_acquire_page", fake_acquire_page):
            result = await renderer.render("https://example.com")

        assert isinstance(result, RenderResult)
        assert result.url == "https://example.com"
        assert result.html == "<html>rendered</html>"
        assert result.title == "Test Page"
        assert result.success is True

    @pytest.mark.asyncio
    async def test_render_error(self):
        """render 异常返回 error。"""
        from contextlib import asynccontextmanager

        renderer = BrowserRenderer()

        # mock _acquire_page 抛异常
        @asynccontextmanager
        async def fake_acquire_page():
            raise Exception("pool init failed")
            yield  # 不会执行到，但需要 yield 使其成为 async generator

        with patch.object(renderer, "_acquire_page", fake_acquire_page):
            result = await renderer.render("https://example.com")

        assert result.success is False
        assert result.error == "pool init failed"


# ========== Collector 测试 ==========


class TestCollector:
    """Collector 协调器测试。"""

    def test_init_lazy_components(self):
        """组件延迟初始化。"""
        c = Collector()
        assert c._http_fetcher is None
        assert c._browser_renderer is None
        # 访问 property 后才初始化
        _ = c.http_fetcher
        assert c._http_fetcher is not None

    @pytest.mark.asyncio
    async def test_collect_ssrf_blocked(self):
        """SSRF 拦截后立即返回。"""
        collector = Collector()

        with patch("app.utils.url_safety.is_safe_url_async", new_callable=AsyncMock) as mock_safe:
            mock_safe.return_value = (False, "private IP")
            result = await collector.collect(CollectRequest(url="http://192.168.1.1"))

        assert result.success is False
        assert "SSRF" in result.error
        assert result.ssrf_checked is True
        assert result.robots_checked is False  # 流程中断

    @pytest.mark.asyncio
    async def test_collect_robots_disallowed(self):
        """robots.txt 禁止后返回。"""
        collector = Collector()

        with patch("app.utils.url_safety.is_safe_url_async", new_callable=AsyncMock) as mock_safe:
            mock_safe.return_value = (True, "")
            with patch.object(collector.robots_checker, "is_allowed", new_callable=AsyncMock) as mock_robots:
                mock_robots.return_value = False
                result = await collector.collect(CollectRequest(url="https://example.com"))

        assert result.success is False
        assert "robots" in result.error
        assert result.robots_checked is True

    @pytest.mark.asyncio
    async def test_collect_whitelist_blocked(self):
        """白名单外 URL 被拒。"""
        collector = Collector()

        with patch("app.utils.url_safety.is_safe_url_async", new_callable=AsyncMock) as mock_safe:
            mock_safe.return_value = (True, "")
            with patch.object(collector.robots_checker, "is_allowed", new_callable=AsyncMock) as mock_robots:
                mock_robots.return_value = True
                with patch.object(collector.source_whitelist, "is_allowed", return_value=False):
                    result = await collector.collect(CollectRequest(url="https://unknown.com"))

        assert result.success is False
        assert "whitelist" in result.error

    @pytest.mark.asyncio
    async def test_collect_http_success(self):
        """HTTP 抓取成功流程。"""
        collector = Collector()

        mock_fetch_result = FetchResult(
            url="https://example.com",
            status_code=200,
            content="<html>content</html>",
            raw_bytes=b"<html>content</html>",
            headers={},
        )

        with patch("app.utils.url_safety.is_safe_url_async", new_callable=AsyncMock) as mock_safe:
            mock_safe.return_value = (True, "")
            with patch.object(collector.robots_checker, "is_allowed", new_callable=AsyncMock) as mock_robots:
                mock_robots.return_value = True
                with patch.object(collector.source_whitelist, "is_allowed", return_value=True):
                    with patch.object(collector.rate_limiter, "wait", new_callable=AsyncMock):
                        with patch.object(collector.http_fetcher, "fetch", new_callable=AsyncMock) as mock_fetch:
                            mock_fetch.return_value = mock_fetch_result
                            result = await collector.collect(
                                CollectRequest(url="https://ccgp.gov.cn/notice/1", use_browser=False)
                            )

        assert result.success is True
        assert result.html == "<html>content</html>"
        assert result.fetch_method == "http"
        assert result.ssrf_checked is True
        assert result.robots_checked is True
        assert result.whitelist_checked is True
        assert result.rate_limited is True


# ========== CollectRequest / CollectResult 测试 ==========


class TestCollectDataClasses:
    """CollectRequest 和 CollectResult 数据类测试。"""

    def test_collect_request_defaults(self):
        r = CollectRequest(url="https://example.com")
        assert r.url == "https://example.com"
        assert r.use_browser is False
        assert r.source_platform == "unknown"

    def test_collect_result_defaults(self):
        r = CollectResult(url="u", success=False)
        assert r.success is False
        assert r.html == ""
        assert r.ssrf_checked is False
