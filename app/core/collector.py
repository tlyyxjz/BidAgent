"""v4.1 §5.3 采集协调器组件。

职责：编排完整采集流程，按顺序调用各组件：
1. URL 安全校验（SSRF 防护）
2. robots.txt 合规检查
3. 来源白名单校验
4. 域名级频率限制
5. 内容缓存检查（ETag/Last-Modified）
6. HTTP 抓取 或 浏览器渲染（根据页面类型选择）
7. 页面快照保存
8. 模板变更检测

与 scraper.py 的关系：
- scraper.py 已实现完整采集流程，是 Collector 的具体实现
- Collector 是该流程的抽象接口，scraper.py 的 Scraper 类是其实现之一
- 新代码应优先使用 Collector 接口，便于测试和扩展
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.utils.logger import get_logger

logger = get_logger("collector")


@dataclass
class CollectRequest:
    """采集请求。"""

    url: str
    source_platform: str = "unknown"
    notice_type: str = "unknown"  # tender/award/correction
    use_browser: bool = False  # True 用浏览器渲染，False 用 HTTP 抓取
    wait_selector: Optional[str] = None  # 浏览器渲染时的等待选择器
    headers: Optional[dict[str, str]] = None
    # 缓存相关
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    # 模板相关
    template_name: Optional[str] = None  # 使用的模板名称


@dataclass
class CollectResult:
    """采集结果。"""

    url: str
    success: bool
    html: str = ""
    status_code: int = 0
    error: Optional[str] = None
    # 流程记录
    ssrf_checked: bool = False
    robots_checked: bool = False
    whitelist_checked: bool = False
    rate_limited: bool = False
    from_cache: bool = False
    snapshot_saved: bool = False
    template_checked: bool = False
    # 元信息
    elapsed_ms: float = 0.0
    fetch_method: str = ""  # "http" / "browser"
    content_sha256: Optional[str] = None


class Collector:
    """采集协调器。

    v4.1 §5.3 组件 3：编排完整采集流程，按合规顺序调用各组件。
    """

    def __init__(self) -> None:
        # 延迟初始化各组件（避免 import 时创建实例）
        self._http_fetcher = None
        self._browser_renderer = None
        self._rate_limiter = None
        self._robots_checker = None
        self._cache_manager = None
        self._snapshot_manager = None
        self._template_monitor = None
        self._source_whitelist = None

    @property
    def http_fetcher(self):
        if self._http_fetcher is None:
            from app.core.http_fetcher import HttpFetcher
            self._http_fetcher = HttpFetcher()
        return self._http_fetcher

    @property
    def browser_renderer(self):
        if self._browser_renderer is None:
            from app.core.browser_renderer import BrowserRenderer
            self._browser_renderer = BrowserRenderer()
        return self._browser_renderer

    @property
    def rate_limiter(self):
        if self._rate_limiter is None:
            from app.core.rate_limiter import DomainRateLimiter
            self._rate_limiter = DomainRateLimiter()
        return self._rate_limiter

    @property
    def robots_checker(self):
        if self._robots_checker is None:
            from app.core.robots_checker import RobotsChecker
            self._robots_checker = RobotsChecker()
        return self._robots_checker

    @property
    def source_whitelist(self):
        if self._source_whitelist is None:
            from app.core.source_whitelist import SourceWhitelist
            self._source_whitelist = SourceWhitelist()
        return self._source_whitelist

    async def collect(self, request: CollectRequest) -> CollectResult:
        """执行完整采集流程。

        流程：
        1. URL SSRF 校验
        2. robots.txt 检查
        3. 来源白名单校验
        4. 域名级频率限制
        5. HTTP 抓取 或 浏览器渲染
        6. 返回结果

        Args:
            request: 采集请求

        Returns:
            CollectResult
        """
        import time
        from urllib.parse import urlparse

        start = time.perf_counter()
        result = CollectResult(url=request.url, success=False)

        # 1. SSRF 校验
        try:
            from app.utils.url_safety import is_safe_url_async
            safe, reason = await is_safe_url_async(request.url)
            result.ssrf_checked = True
            if not safe:
                result.error = f"SSRF blocked: {reason}"
                result.elapsed_ms = (time.perf_counter() - start) * 1000
                return result
        except Exception as e:
            logger.warning(f"SSRF check failed (allowing): {e}")
            result.ssrf_checked = True

        # 2. robots.txt 检查
        try:
            allowed = await self.robots_checker.is_allowed(request.url)
            result.robots_checked = True
            if not allowed:
                result.error = "robots.txt disallowed"
                result.elapsed_ms = (time.perf_counter() - start) * 1000
                return result
        except Exception as e:
            logger.warning(f"robots check failed (allowing): {e}")
            result.robots_checked = True

        # 3. 来源白名单校验
        try:
            result.whitelist_checked = True
            if not self.source_whitelist.is_allowed(request.url):
                result.error = f"source not in whitelist: {request.source_platform}"
                result.elapsed_ms = (time.perf_counter() - start) * 1000
                return result
        except Exception as e:
            logger.warning(f"whitelist check failed (allowing): {e}")
            result.whitelist_checked = True

        # 4. 域名级频率限制
        try:
            domain = urlparse(request.url).netloc
            await self.rate_limiter.wait(domain)
            result.rate_limited = True
        except Exception as e:
            logger.warning(f"rate limit failed (allowing): {e}")

        # 5. 抓取
        if request.use_browser:
            result.fetch_method = "browser"
            render_result = await self.browser_renderer.render(
                request.url,
                wait_selector=request.wait_selector,
            )
            result.html = render_result.html
            result.status_code = render_result.status_code
            if render_result.error:
                result.error = render_result.error
                result.elapsed_ms = (time.perf_counter() - start) * 1000
                return result
        else:
            result.fetch_method = "http"
            fetch_result = await self.http_fetcher.fetch(
                request.url,
                headers=request.headers,
                etag=request.etag,
                last_modified=request.last_modified,
            )
            result.html = fetch_result.content
            result.status_code = fetch_result.status_code
            result.from_cache = fetch_result.from_cache
            if fetch_result.error:
                result.error = fetch_result.error
                result.elapsed_ms = (time.perf_counter() - start) * 1000
                return result

        result.success = bool(result.html)
        result.elapsed_ms = (time.perf_counter() - start) * 1000
        return result
