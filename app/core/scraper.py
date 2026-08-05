"""Playwright 异步抓取核心。

工程规范：
- 使用 `playwright.async_api` 的 async API。
- Chromium headless，超时控制（默认 30 秒）。
- 支持 JS 渲染等待（wait_for_selector）。
- 支持自定义 CSS selector -> 字段映射。
- 支持分页（next_page_selector + max_pages）。
- 自动轮换 User-Agent + 代理池（每次请求随机）。
- 代理失败自动切换重试。
- 完善错误处理：网络错误、选择器找不到、超时。
"""

from __future__ import annotations

import json

from typing import Any

from playwright.async_api import (
    Page,
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from app.config import settings
from app.core.proxy import (
    get_proxy_pool,
    get_random_proxy,
    get_random_user_agent,
    report_proxy_failure,
)
from app.core.cache_manager import cache_manager
from app.core.rate_limiter import domain_rate_limiter
from app.core.robots_checker import robots_checker
from app.utils.logger import get_logger
from app.utils.url_safety import is_safe_url

# 拆分后的子模块：re-export 错误类保持公开接口不变
from app.core.scraper_errors import HttpForbiddenError, ScrapeError  # noqa: F401
from app.core.scraper_extract import (  # noqa: F401
    click_next as _click_next_fn,
    extract_list as _extract_list_fn,
    extract_page as _extract_page_fn,
    extract_single as _extract_single_fn,
)
from app.core.scraper_playwright import scrape_with_playwright
from app.core.scraper_utils import merge_template

logger = get_logger("scraper")


class Scraper:
    """Playwright 异步抓取器。"""

    def __init__(
        self,
        headless: bool | None = None,
        timeout_ms: int | None = None,
    ) -> None:
        self.headless = settings.PLAYWRIGHT_HEADLESS if headless is None else headless
        self.timeout_ms = (timeout_ms or settings.PLAYWRIGHT_TIMEOUT_SECONDS) * 1000

    async def scrape(self, request: dict[str, Any]) -> dict[str, Any]:
        """执行一次抓取请求。

        Args:
            request: 抓取请求字典，包含：
                - url (str): 目标 URL（必填）
                - selectors (dict[str, str]): 字段名 -> CSS 选择器（可选）
                - list_selector (str): 列表项选择器，启用多条目抓取（可选）
                - wait_for_selector (str): 等待该元素出现表示 JS 渲染完成（可选）
                - next_page_selector (str): 下一页按钮选择器，启用分页（可选）
                - max_pages (int): 最大分页数，默认 1（可选）
                - template (str): 内置模板名 amazon/reddit/news（可选）
                - cookies (list[dict]): Playwright cookie 列表，登录态采集（可选）
                - extra_headers (dict[str, str]): 自定义请求头（可选）

        Returns:
            ``{"url": ..., "data": [...], "pages_scraped": int}`` 形式的字典。
        """
        url = request.get("url")
        if not url or not isinstance(url, str):
            raise ScrapeError("url 字段必填且必须是字符串")

        # M-7 修复：SSRF 防护，拒绝内网/回环/链路本地地址
        safe, reason = is_safe_url(url)
        if not safe:
            logger.warning("SSRF blocked url=%s reason=%s", url[:80], reason)
            raise ScrapeError(f"URL 不安全: {reason}")

        # v4.1 §5.2 合规原则：检查 robots.txt 是否允许采集该 URL
        # 在频率限制前检查：若 robots.txt 禁止，无需等待
        allowed = await robots_checker.is_allowed(url)
        if not allowed:
            logger.warning("robots.txt disallowed url=%s", url[:80])
            raise ScrapeError(f"robots.txt 禁止采集该 URL: {url[:80]}")

        # v4.1 §5.3 cache_manager：检查内容缓存（命中则直接返回，不走 rate_limit）
        cached_entry = await cache_manager.get(url)
        if cached_entry is not None:
            try:
                cached_result = json.loads(cached_entry.body)
                logger.info("cache hit url=%s hits=%d", url[:80], cached_entry.hit_count)
                cached_result["from_cache"] = True
                return cached_result
            except (json.JSONDecodeError, TypeError) as exc:
                logger.warning("cache parse failed url=%s err=%s", url[:80], exc)

        # v4.1 §13 合规采集层：域名级频率限制（默认 8 秒间隔，按域名独立计数）
        # 在 SSRF 校验通过后、模板合并前等待，确保真实请求前已满足间隔
        waited = await domain_rate_limiter.wait(url)
        if waited > 0:
            logger.info(
                "domain_rate_limit waited=%.2fs url=%s",
                waited, url[:80],
            )

        # 合并模板默认配置
        merged = self._merge_template(request)

        selectors: dict[str, str] = merged.get("selectors") or {}
        list_selector: str | None = merged.get("list_selector")
        wait_for: str | None = merged.get("wait_for_selector")
        next_page: str | None = merged.get("next_page_selector")
        max_pages: int = int(merged.get("max_pages") or 1)
        if max_pages < 1:
            max_pages = 1
        if max_pages > 50:
            max_pages = 50  # 安全上限

        # 登录态：cookie + 自定义 header + storage_state（Sol S-11）
        cookies: list[dict[str, Any]] = merged.get("cookies") or []
        extra_headers: dict[str, str] = merged.get("extra_headers") or {}
        storage_state: dict[str, Any] | None = merged.get("storage_state")

        # Sol S-11：千里马模板动态加载 storage_state（不固化在模块导入时）
        if (
            merged.get("template") == "qianlima"
            and storage_state is None
        ):
            from app.templates.qianlima import get_qianlima_storage_state

            storage_state = await get_qianlima_storage_state()

        failed_proxies: list[str] = []
        last_error: Exception | None = None

        # 重试逻辑：代理池为空时只跑一次；有代理时最多重试 3 次
        max_attempts = 3 if get_proxy_pool() else 1
        for attempt in range(1, max_attempts + 1):
            proxy = get_random_proxy(exclude=failed_proxies)
            try:
                result = await self._scrape_with_playwright(
                    url=url,
                    selectors=selectors,
                    list_selector=list_selector,
                    wait_for=wait_for,
                    next_page=next_page,
                    max_pages=max_pages,
                    proxy=proxy,
                    cookies=cookies,
                    extra_headers=extra_headers,
                    storage_state=storage_state,
                    template_name=merged.get("template"),
                )
                # v4.1 §5.3 cache_manager：抓取成功后缓存结果
                try:
                    await cache_manager.set(url, json.dumps(result, ensure_ascii=False))
                    logger.debug("cache set url=%s", url[:80])
                except (TypeError, ValueError) as exc:
                    logger.warning("cache set failed url=%s err=%s", url[:80], exc)
                return result
            except HttpForbiddenError as exc:
                # #34 修复：HTTP 403 表示被反爬封禁，必须立即停止，
                # 不得换 UA/代理重试（重试会加重封禁）。
                last_error = exc
                logger.warning("HTTP 403 Forbidden, 停止抓取: %s", url)
                break
            except PlaywrightTimeoutError as exc:
                last_error = exc
                logger.warning("抓取超时 url=%s attempt=%d proxy=%s", url, attempt, proxy)
                if proxy:
                    report_proxy_failure(proxy, failed_proxies)
            except PlaywrightError as exc:
                last_error = exc
                logger.warning(
                    "Playwright 错误 url=%s attempt=%d proxy=%s err=%s",
                    url, attempt, proxy, exc,
                )
                if proxy:
                    report_proxy_failure(proxy, failed_proxies)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.exception("抓取未知错误 url=%s attempt=%d", url, attempt)
                break  # 非代理类错误不重试

        # 抓取失败：回滚域名级频率限制的"预订"，避免下次同域名请求被多等一个间隔
        await domain_rate_limiter.release(url)

        raise ScrapeError(f"抓取失败: {last_error}") from last_error

    @staticmethod
    def _merge_template(request: dict[str, Any]) -> dict[str, Any]:
        """合并模板默认配置；用户显式字段优先。"""
        return merge_template(request)

    async def _scrape_with_playwright(
        self,
        url: str,
        selectors: dict[str, str],
        list_selector: str | None,
        wait_for: str | None,
        next_page: str | None,
        max_pages: int,
        proxy: str | None,
        cookies: list[dict[str, Any]] | None = None,
        extra_headers: dict[str, str] | None = None,
        storage_state: dict[str, Any] | None = None,
        template_name: str | None = None,
    ) -> dict[str, Any]:
        """启动 Playwright 执行实际抓取。

        依赖注入：``async_playwright`` 和 ``get_random_user_agent`` 从本模块
        命名空间解析后传入 ``scrape_with_playwright``，使测试 monkeypatch 生效。
        """
        return await scrape_with_playwright(
            url=url,
            selectors=selectors,
            list_selector=list_selector,
            wait_for=wait_for,
            next_page=next_page,
            max_pages=max_pages,
            proxy=proxy,
            cookies=cookies,
            extra_headers=extra_headers,
            storage_state=storage_state,
            template_name=template_name,
            headless=self.headless,
            timeout_ms=self.timeout_ms,
            playwright_factory=async_playwright,
            user_agent=get_random_user_agent(),
        )

    async def _extract_page(
        self,
        page: Page,
        selectors: dict[str, str],
        list_selector: str | None,
    ) -> list[dict[str, Any]]:
        """从当前 page 提取数据。"""
        return await _extract_page_fn(page, selectors, list_selector)

    @staticmethod
    async def _extract_single(
        page: Page, selectors: dict[str, str]
    ) -> list[dict[str, Any]]:
        """单条目提取。"""
        return await _extract_single_fn(page, selectors)

    @staticmethod
    async def _extract_list(
        page: Page, selectors: dict[str, str], list_selector: str
    ) -> list[dict[str, Any]]:
        """列表提取：每个 list_selector 元素都按 selectors 抽取字段。"""
        return await _extract_list_fn(page, selectors, list_selector)

    @staticmethod
    async def _click_next(page: Page, next_page_selector: str) -> bool:
        """点击下一页；返回是否成功。"""
        return await _click_next_fn(page, next_page_selector)


# 模块级单例
scraper = Scraper()
