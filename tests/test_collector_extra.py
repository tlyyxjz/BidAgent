"""Extra tests for app/core/collector.py.

Covers uncovered branches in Collector.collect():
- SSRF check exception path (line 152-154)
- robots check exception path (line 164-166)
- whitelist check exception path (line 175-177)
- rate limit wait success + exception path (lines 182-185)
- browser render path success + error (lines 188-199)
- http fetch error path (lines 211-214)
- http fetch from_cache flag (line 210)
- success=False when html empty (line 216)
- lazy property initialization for all components
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.core.browser_renderer import RenderResult
from app.core.collector import CollectRequest, CollectResult, Collector
from app.core.http_fetcher import FetchResult


# ============================================================
# 测试套件 1: SSRF 校验异常分支 (行 152-154)
# ============================================================

class TestSSRFCheckException:
    """覆盖 SSRF 校验抛异常时流程继续（allowing）。"""

    @pytest.mark.asyncio
    async def test_ssrf_exception_allows_continue(self) -> None:
        """SSRF 校验抛异常时不中断流程，继续后续步骤。"""
        collector = Collector()

        with patch(
            "app.utils.url_safety.is_safe_url_async",
            new_callable=AsyncMock,
            side_effect=RuntimeError("dns lookup failed"),
        ):
            with patch.object(
                collector.robots_checker,
                "is_allowed",
                new_callable=AsyncMock,
                return_value=True,
            ):
                with patch.object(
                    collector.source_whitelist, "is_allowed", return_value=True
                ):
                    with patch.object(
                        collector.rate_limiter, "wait", new_callable=AsyncMock
                    ):
                        mock_fetch_result = FetchResult(
                            url="https://example.com",
                            status_code=200,
                            content="<html>ok</html>",
                            raw_bytes=b"<html>ok</html>",
                        )
                        with patch.object(
                            collector.http_fetcher,
                            "fetch",
                            new_callable=AsyncMock,
                            return_value=mock_fetch_result,
                        ):
                            result = await collector.collect(
                                CollectRequest(url="https://example.com")
                            )

        assert result.ssrf_checked is True
        assert result.success is True
        assert result.html == "<html>ok</html>"


# ============================================================
# 测试套件 2: robots.txt 检查异常分支 (行 164-166)
# ============================================================

class TestRobotsCheckException:
    """覆盖 robots 检查抛异常时流程继续（allowing）。"""

    @pytest.mark.asyncio
    async def test_robots_exception_allows_continue(self) -> None:
        """robots 检查抛异常时不中断流程。"""
        collector = Collector()

        with patch(
            "app.utils.url_safety.is_safe_url_async",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            with patch.object(
                collector.robots_checker,
                "is_allowed",
                new_callable=AsyncMock,
                side_effect=RuntimeError("robots fetch failed"),
            ):
                with patch.object(
                    collector.source_whitelist, "is_allowed", return_value=True
                ):
                    with patch.object(
                        collector.rate_limiter, "wait", new_callable=AsyncMock
                    ):
                        mock_fetch_result = FetchResult(
                            url="https://example.com",
                            status_code=200,
                            content="<html>ok</html>",
                            raw_bytes=b"<html>ok</html>",
                        )
                        with patch.object(
                            collector.http_fetcher,
                            "fetch",
                            new_callable=AsyncMock,
                            return_value=mock_fetch_result,
                        ):
                            result = await collector.collect(
                                CollectRequest(url="https://example.com")
                            )

        assert result.robots_checked is True
        assert result.success is True


# ============================================================
# 测试套件 3: 白名单校验异常分支 (行 175-177)
# ============================================================

class TestWhitelistCheckException:
    """覆盖白名单校验抛异常时流程继续（allowing）。"""

    @pytest.mark.asyncio
    async def test_whitelist_exception_allows_continue(self) -> None:
        """白名单校验抛异常时不中断流程。"""
        collector = Collector()

        with patch(
            "app.utils.url_safety.is_safe_url_async",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            with patch.object(
                collector.robots_checker,
                "is_allowed",
                new_callable=AsyncMock,
                return_value=True,
            ):
                with patch.object(
                    collector.source_whitelist,
                    "is_allowed",
                    side_effect=RuntimeError("whitelist db error"),
                ):
                    with patch.object(
                        collector.rate_limiter, "wait", new_callable=AsyncMock
                    ):
                        mock_fetch_result = FetchResult(
                            url="https://example.com",
                            status_code=200,
                            content="<html>ok</html>",
                            raw_bytes=b"<html>ok</html>",
                        )
                        with patch.object(
                            collector.http_fetcher,
                            "fetch",
                            new_callable=AsyncMock,
                            return_value=mock_fetch_result,
                        ):
                            result = await collector.collect(
                                CollectRequest(url="https://example.com")
                            )

        assert result.whitelist_checked is True
        assert result.success is True


# ============================================================
# 测试套件 4: 频率限制分支 (行 182-185)
# ============================================================

class TestRateLimitBranches:
    """覆盖频率限制成功和异常分支。"""

    @pytest.mark.asyncio
    async def test_rate_limit_success_sets_flag(self) -> None:
        """频率限制成功后 rate_limited=True。"""
        collector = Collector()

        with patch(
            "app.utils.url_safety.is_safe_url_async",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            with patch.object(
                collector.robots_checker,
                "is_allowed",
                new_callable=AsyncMock,
                return_value=True,
            ):
                with patch.object(
                    collector.source_whitelist, "is_allowed", return_value=True
                ):
                    with patch.object(
                        collector.rate_limiter,
                        "wait",
                        new_callable=AsyncMock,
                        return_value=0.0,
                    ) as mock_wait:
                        mock_fetch_result = FetchResult(
                            url="https://ccgp.gov.cn/1",
                            status_code=200,
                            content="<html>ok</html>",
                            raw_bytes=b"<html>ok</html>",
                        )
                        with patch.object(
                            collector.http_fetcher,
                            "fetch",
                            new_callable=AsyncMock,
                            return_value=mock_fetch_result,
                        ):
                            result = await collector.collect(
                                CollectRequest(url="https://ccgp.gov.cn/1")
                            )

        assert result.rate_limited is True
        mock_wait.assert_awaited_once_with("ccgp.gov.cn")

    @pytest.mark.asyncio
    async def test_rate_limit_exception_allows_continue(self) -> None:
        """频率限制抛异常时不中断流程（allowing）。"""
        collector = Collector()

        with patch(
            "app.utils.url_safety.is_safe_url_async",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            with patch.object(
                collector.robots_checker,
                "is_allowed",
                new_callable=AsyncMock,
                return_value=True,
            ):
                with patch.object(
                    collector.source_whitelist, "is_allowed", return_value=True
                ):
                    with patch.object(
                        collector.rate_limiter,
                        "wait",
                        new_callable=AsyncMock,
                        side_effect=RuntimeError("lock error"),
                    ):
                        mock_fetch_result = FetchResult(
                            url="https://example.com",
                            status_code=200,
                            content="<html>ok</html>",
                            raw_bytes=b"<html>ok</html>",
                        )
                        with patch.object(
                            collector.http_fetcher,
                            "fetch",
                            new_callable=AsyncMock,
                            return_value=mock_fetch_result,
                        ):
                            result = await collector.collect(
                                CollectRequest(url="https://example.com")
                            )

        # rate_limited 保持 False（异常未到 result.rate_limited = True）
        assert result.rate_limited is False
        assert result.success is True


# ============================================================
# 测试套件 5: 浏览器渲染路径 (行 188-199)
# ============================================================

class TestBrowserRenderPath:
    """覆盖 use_browser=True 的渲染流程。"""

    @pytest.mark.asyncio
    async def test_browser_render_success(self) -> None:
        """浏览器渲染成功。"""
        collector = Collector()

        mock_render_result = RenderResult(
            url="https://example.com",
            html="<html>rendered</html>",
            title="Test",
            status_code=200,
        )

        with patch(
            "app.utils.url_safety.is_safe_url_async",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            with patch.object(
                collector.robots_checker,
                "is_allowed",
                new_callable=AsyncMock,
                return_value=True,
            ):
                with patch.object(
                    collector.source_whitelist, "is_allowed", return_value=True
                ):
                    with patch.object(
                        collector.rate_limiter, "wait", new_callable=AsyncMock
                    ):
                        with patch.object(
                            collector.browser_renderer,
                            "render",
                            new_callable=AsyncMock,
                            return_value=mock_render_result,
                        ) as mock_render:
                            result = await collector.collect(
                                CollectRequest(
                                    url="https://example.com",
                                    use_browser=True,
                                    wait_selector=".content",
                                )
                            )

        assert result.success is True
        assert result.fetch_method == "browser"
        assert result.html == "<html>rendered</html>"
        assert result.status_code == 200
        mock_render.assert_awaited_once_with(
            "https://example.com", wait_selector=".content"
        )

    @pytest.mark.asyncio
    async def test_browser_render_error_returns_failure(self) -> None:
        """浏览器渲染返回 error 时流程中断。"""
        collector = Collector()

        mock_render_result = RenderResult(
            url="https://example.com",
            html="",
            error="timeout",
        )

        with patch(
            "app.utils.url_safety.is_safe_url_async",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            with patch.object(
                collector.robots_checker,
                "is_allowed",
                new_callable=AsyncMock,
                return_value=True,
            ):
                with patch.object(
                    collector.source_whitelist, "is_allowed", return_value=True
                ):
                    with patch.object(
                        collector.rate_limiter, "wait", new_callable=AsyncMock
                    ):
                        with patch.object(
                            collector.browser_renderer,
                            "render",
                            new_callable=AsyncMock,
                            return_value=mock_render_result,
                        ):
                            result = await collector.collect(
                                CollectRequest(
                                    url="https://example.com",
                                    use_browser=True,
                                )
                            )

        assert result.success is False
        assert result.error == "timeout"
        assert result.fetch_method == "browser"

    @pytest.mark.asyncio
    async def test_browser_render_without_wait_selector(self) -> None:
        """浏览器渲染不传 wait_selector。"""
        collector = Collector()

        mock_render_result = RenderResult(
            url="https://example.com",
            html="<html>ok</html>",
        )

        with patch(
            "app.utils.url_safety.is_safe_url_async",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            with patch.object(
                collector.robots_checker,
                "is_allowed",
                new_callable=AsyncMock,
                return_value=True,
            ):
                with patch.object(
                    collector.source_whitelist, "is_allowed", return_value=True
                ):
                    with patch.object(
                        collector.rate_limiter, "wait", new_callable=AsyncMock
                    ):
                        with patch.object(
                            collector.browser_renderer,
                            "render",
                            new_callable=AsyncMock,
                            return_value=mock_render_result,
                        ) as mock_render:
                            result = await collector.collect(
                                CollectRequest(
                                    url="https://example.com",
                                    use_browser=True,
                                )
                            )

        assert result.success is True
        mock_render.assert_awaited_once_with(
            "https://example.com", wait_selector=None
        )


# ============================================================
# 测试套件 6: HTTP 抓取错误分支 (行 211-214)
# ============================================================

class TestHttpFetchError:
    """覆盖 HTTP 抓取返回 error 时的流程。"""

    @pytest.mark.asyncio
    async def test_http_fetch_error_returns_failure(self) -> None:
        """HTTP 抓取返回 error 时流程中断。"""
        collector = Collector()

        mock_fetch_result = FetchResult(
            url="https://example.com",
            status_code=0,
            content="",
            raw_bytes=b"",
            error="connection refused",
        )

        with patch(
            "app.utils.url_safety.is_safe_url_async",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            with patch.object(
                collector.robots_checker,
                "is_allowed",
                new_callable=AsyncMock,
                return_value=True,
            ):
                with patch.object(
                    collector.source_whitelist, "is_allowed", return_value=True
                ):
                    with patch.object(
                        collector.rate_limiter, "wait", new_callable=AsyncMock
                    ):
                        with patch.object(
                            collector.http_fetcher,
                            "fetch",
                            new_callable=AsyncMock,
                            return_value=mock_fetch_result,
                        ):
                            result = await collector.collect(
                                CollectRequest(url="https://example.com")
                            )

        assert result.success is False
        assert result.error == "connection refused"
        assert result.fetch_method == "http"

    @pytest.mark.asyncio
    async def test_http_fetch_from_cache_flag(self) -> None:
        """HTTP 抓取 304 响应 from_cache=True。"""
        collector = Collector()

        mock_fetch_result = FetchResult(
            url="https://example.com",
            status_code=304,
            content="",
            raw_bytes=b"",
            from_cache=True,
            error=None,
        )

        with patch(
            "app.utils.url_safety.is_safe_url_async",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            with patch.object(
                collector.robots_checker,
                "is_allowed",
                new_callable=AsyncMock,
                return_value=True,
            ):
                with patch.object(
                    collector.source_whitelist, "is_allowed", return_value=True
                ):
                    with patch.object(
                        collector.rate_limiter, "wait", new_callable=AsyncMock
                    ):
                        with patch.object(
                            collector.http_fetcher,
                            "fetch",
                            new_callable=AsyncMock,
                            return_value=mock_fetch_result,
                        ):
                            result = await collector.collect(
                                CollectRequest(
                                    url="https://example.com",
                                    etag='"old-etag"',
                                    last_modified="Wed, 01 Jan 2026 00:00:00 GMT",
                                )
                            )

        # error 为 None，不会中断；但 html 为空，success=False
        assert result.from_cache is True
        assert result.error is None
        assert result.success is False  # bool("") == False


# ============================================================
# 测试套件 7: 成功判定与空内容 (行 216)
# ============================================================

class TestSuccessDetermination:
    """覆盖 result.success = bool(result.html) 分支。"""

    @pytest.mark.asyncio
    async def test_empty_html_means_failure(self) -> None:
        """抓取成功但 html 为空时 success=False。"""
        collector = Collector()

        mock_fetch_result = FetchResult(
            url="https://example.com",
            status_code=200,
            content="",
            raw_bytes=b"",
            error=None,
        )

        with patch(
            "app.utils.url_safety.is_safe_url_async",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            with patch.object(
                collector.robots_checker,
                "is_allowed",
                new_callable=AsyncMock,
                return_value=True,
            ):
                with patch.object(
                    collector.source_whitelist, "is_allowed", return_value=True
                ):
                    with patch.object(
                        collector.rate_limiter, "wait", new_callable=AsyncMock
                    ):
                        with patch.object(
                            collector.http_fetcher,
                            "fetch",
                            new_callable=AsyncMock,
                            return_value=mock_fetch_result,
                        ):
                            result = await collector.collect(
                                CollectRequest(url="https://example.com")
                            )

        assert result.success is False
        assert result.html == ""
        assert result.elapsed_ms >= 0.0

    @pytest.mark.asyncio
    async def test_non_empty_html_means_success(self) -> None:
        """抓取成功且 html 非空时 success=True。"""
        collector = Collector()

        mock_fetch_result = FetchResult(
            url="https://example.com",
            status_code=200,
            content="<html>data</html>",
            raw_bytes=b"<html>data</html>",
        )

        with patch(
            "app.utils.url_safety.is_safe_url_async",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            with patch.object(
                collector.robots_checker,
                "is_allowed",
                new_callable=AsyncMock,
                return_value=True,
            ):
                with patch.object(
                    collector.source_whitelist, "is_allowed", return_value=True
                ):
                    with patch.object(
                        collector.rate_limiter, "wait", new_callable=AsyncMock
                    ):
                        with patch.object(
                            collector.http_fetcher,
                            "fetch",
                            new_callable=AsyncMock,
                            return_value=mock_fetch_result,
                        ):
                            result = await collector.collect(
                                CollectRequest(
                                    url="https://example.com",
                                    headers={"X-Test": "1"},
                                )
                            )

        assert result.success is True
        assert result.html == "<html>data</html>"


# ============================================================
# 测试套件 8: 延迟属性初始化 (行 92-118)
# ============================================================

class TestLazyPropertyInit:
    """覆盖各组件的延迟初始化 property。"""

    def test_browser_renderer_lazy_init(self) -> None:
        """browser_renderer 延迟初始化。"""
        c = Collector()
        assert c._browser_renderer is None
        renderer = c.browser_renderer
        assert c._browser_renderer is not None
        assert renderer is c._browser_renderer

    def test_rate_limiter_lazy_init(self) -> None:
        """rate_limiter 延迟初始化。"""
        c = Collector()
        assert c._rate_limiter is None
        rl = c.rate_limiter
        assert c._rate_limiter is not None
        assert rl is c._rate_limiter

    def test_robots_checker_lazy_init(self) -> None:
        """robots_checker 延迟初始化。"""
        c = Collector()
        assert c._robots_checker is None
        rc = c.robots_checker
        assert c._robots_checker is not None
        assert rc is c._robots_checker

    def test_source_whitelist_lazy_init(self) -> None:
        """source_whitelist 延迟初始化。"""
        c = Collector()
        assert c._source_whitelist is None
        sw = c.source_whitelist
        assert c._source_whitelist is not None
        assert sw is c._source_whitelist

    def test_property_caches_instance(self) -> None:
        """多次访问 property 返回同一实例。"""
        c = Collector()
        first = c.http_fetcher
        second = c.http_fetcher
        assert first is second


# ============================================================
# 测试套件 9: CollectRequest / CollectResult 数据类
# ============================================================

class TestCollectRequestFields:
    """覆盖 CollectRequest 字段默认值。"""

    def test_collect_request_all_fields(self) -> None:
        """CollectRequest 全字段构造。"""
        req = CollectRequest(
            url="https://example.com",
            source_platform="ccgp",
            notice_type="tender",
            use_browser=True,
            wait_selector=".list",
            headers={"User-Agent": "test"},
            etag='"abc"',
            last_modified="Wed, 01 Jan 2026 00:00:00 GMT",
            template_name="ccgp_list",
        )
        assert req.url == "https://example.com"
        assert req.source_platform == "ccgp"
        assert req.notice_type == "tender"
        assert req.use_browser is True
        assert req.wait_selector == ".list"
        assert req.headers == {"User-Agent": "test"}
        assert req.etag == '"abc"'
        assert req.last_modified == "Wed, 01 Jan 2026 00:00:00 GMT"
        assert req.template_name == "ccgp_list"

    def test_collect_result_all_fields(self) -> None:
        """CollectResult 全字段构造。"""
        r = CollectResult(
            url="u",
            success=True,
            html="<html/>",
            status_code=200,
            error=None,
            ssrf_checked=True,
            robots_checked=True,
            whitelist_checked=True,
            rate_limited=True,
            from_cache=True,
            snapshot_saved=True,
            template_checked=True,
            elapsed_ms=12.5,
            fetch_method="http",
            content_sha256="abc123",
        )
        assert r.success is True
        assert r.html == "<html/>"
        assert r.status_code == 200
        assert r.ssrf_checked is True
        assert r.robots_checked is True
        assert r.whitelist_checked is True
        assert r.rate_limited is True
        assert r.from_cache is True
        assert r.snapshot_saved is True
        assert r.template_checked is True
        assert r.elapsed_ms == 12.5
        assert r.fetch_method == "http"
        assert r.content_sha256 == "abc123"
