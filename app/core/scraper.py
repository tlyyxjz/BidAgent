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

from typing import Any
from urllib.parse import urlparse

from playwright.async_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Page,
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
from app.templates import get_template
from app.templates.base import ScrapeTemplate
from app.utils.hostname_cache import HostnameLRUCache
from app.utils.logger import get_logger
from app.utils.url_safety import is_safe_url, is_safe_url_async

logger = get_logger("scraper")


class ScrapeError(Exception):
    """抓取过程中的统一错误。"""


class HttpForbiddenError(Exception):
    """HTTP 403 Forbidden：被反爬封禁，必须停止抓取，不得换 UA/代理重试。"""


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
                )
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

        raise ScrapeError(f"抓取失败: {last_error}") from last_error

    @staticmethod
    def _merge_template(request: dict[str, Any]) -> dict[str, Any]:
        """合并模板默认配置；用户显式字段优先。"""
        template_name = request.get("template")
        if not template_name:
            return dict(request)

        tpl: ScrapeTemplate | None = get_template(template_name)
        if tpl is None:
            logger.warning("未知模板 %s，忽略", template_name)
            return dict(request)

        merged: dict[str, Any] = {
            "selectors": dict(tpl.selectors),
            "list_selector": tpl.list_selector,
            "wait_for_selector": tpl.wait_for_selector,
            "next_page_selector": tpl.next_page_selector,
            # Sol S-11：保留 template 名，scrape() 时根据它动态加载 storage_state
            "template": template_name,
        }
        # S-3 修复：传递模板上的 cookies（登录态采集，如 qianlima 模板）
        # 用户显式传 cookies 时优先用户传的（在下方覆盖逻辑里处理）
        template_cookies = getattr(tpl, "cookies", None)
        if template_cookies:
            merged["cookies"] = list(template_cookies)
        # 用户字段覆盖模板
        # Sol S-11：新增 storage_state 可覆盖字段（用户显式传入优先）
        for key in ("selectors", "list_selector", "wait_for_selector",
                    "next_page_selector", "max_pages", "cookies",
                    "extra_headers", "storage_state"):
            if key in request and request[key]:
                if key == "selectors" and isinstance(request[key], dict):
                    merged["selectors"].update(request[key])
                else:
                    merged[key] = request[key]
        merged["url"] = request.get("url")
        return merged

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
    ) -> dict[str, Any]:
        """启动 Playwright 执行实际抓取。"""
        ua = get_random_user_agent()
        proxy_arg = {"server": proxy} if proxy else None

        async with async_playwright() as p:
            browser: Browser = await p.chromium.launch(
                headless=self.headless,
                proxy=proxy_arg,
            )
            try:
                # Sol S-11：context 创建支持 storage_state（含 cookies + origins）
                context_options: dict[str, Any] = {
                    "user_agent": ua,
                    "viewport": {"width": 1280, "height": 800},
                    "locale": "zh-CN",
                }
                if storage_state is not None:
                    context_options["storage_state"] = storage_state

                context: BrowserContext = await browser.new_context(
                    **context_options
                )
                context.set_default_timeout(self.timeout_ms)
                context.set_default_navigation_timeout(self.timeout_ms)

                # 登录态：注入 cookies + 自定义 headers
                # （storage_state 已含 cookies，这里处理用户显式传入的额外 cookies）
                if extra_headers:
                    await context.set_extra_http_headers(extra_headers)
                if cookies:
                    await context.add_cookies(cookies)

                page: Page = await context.new_page()

                # 新-4 修复：SSRF 重定向防护
                # Playwright 默认跟随重定向，攻击者可让公网域名 302 到 169.254.169.254。
                # 用 page.route 拦截所有请求，对每个跳转后的 URL 做二次 is_safe_url 校验。
                # M-2 修复（第四轮）：用 is_safe_url_async 避免 DNS 解析阻塞事件循环。
                # M-2 修复（第五轮）：
                #   1. 只对 document 请求严格校验（HTML 主文档/导航），子资源继承主域名安全判定
                #   2. hostname 级 LRU 缓存避免同域名重复 DNS 解析（30-50 子资源 → 1 次 DNS）
                # m-2 修复（第七轮）：抽 HostnameLRUCache 成独立类，提升可测试性
                # m-1 修复（第八轮）：HostnameLRUCache import 提到文件顶部
                hostname_cache = HostnameLRUCache(capacity=64)

                async def _ssrf_guard(route):
                    req_url = route.request.url
                    try:
                        hostname = urlparse(req_url).hostname or ""
                    except Exception:  # noqa: BLE001
                        hostname = ""
                    hostname_lower = hostname.lower()

                    # document 请求严格校验（防 302 重定向到内网）
                    if route.request.resource_type == "document":
                        safe, reason = await is_safe_url_async(req_url)
                        if not safe:
                            logger.warning(
                                "SSRF guard blocked document url=%s reason=%s",
                                req_url[:80], reason,
                            )
                            await route.abort("blockedbyclient")
                            return
                        # 缓存 hostname 校验结果，供子资源复用
                        if hostname_lower:
                            hostname_cache.set(hostname_lower, (True, ""))
                        await route.continue_()
                        return

                    # 子资源请求：用 hostname 缓存校验，未命中则做一次完整校验
                    if hostname_lower:
                        cached = hostname_cache.get(hostname_lower)
                        if cached is not None:
                            if not cached[0]:
                                await route.abort("blockedbyclient")
                                return
                            await route.continue_()
                            return
                        # 未命中缓存：做一次校验并缓存
                        safe, reason = await is_safe_url_async(req_url)
                        hostname_cache.set(hostname_lower, (safe, reason))
                        # m-3 修复（第六轮）：缓存未命中时打 debug 日志，对称排查
                        if safe:
                            logger.debug(
                                "SSRF guard asset first-check ok hostname=%s url=%s",
                                hostname_lower, req_url[:80],
                            )
                        else:
                            logger.warning(
                                "SSRF guard blocked asset url=%s reason=%s",
                                req_url[:80], reason,
                            )
                            await route.abort("blockedbyclient")
                            return
                    await route.continue_()

                await page.route("**/*", _ssrf_guard)

                # #34 修复：检查 HTTP 响应状态码，403 时抛专用异常停止抓取
                # （Playwright page.goto 遇到 403 通常不抛异常，会渲染为错误页，
                # 导致原逻辑误判为成功或被反爬封禁后仍换 UA/代理重试加重封禁）。
                response = await page.goto(url, wait_until="domcontentloaded")
                if response and response.status == 403:
                    raise HttpForbiddenError(url)

                if wait_for:
                    try:
                        await page.wait_for_selector(wait_for, timeout=self.timeout_ms)
                    except PlaywrightTimeoutError:
                        logger.warning(
                            "wait_for_selector 超时 url=%s selector=%s",
                            url, wait_for,
                        )

                all_items: list[dict[str, Any]] = []
                pages_scraped = 0

                for page_num in range(1, max_pages + 1):
                    pages_scraped = page_num
                    items = await self._extract_page(page, selectors, list_selector)
                    all_items.extend(items)

                    # 翻页
                    if next_page and page_num < max_pages:
                        clicked = await self._click_next(page, next_page)
                        if not clicked:
                            logger.info("没有下一页，停止翻页 url=%s at_page=%d", url, page_num)
                            break
                        # 翻页后等待新内容
                        if wait_for:
                            try:
                                await page.wait_for_selector(wait_for, timeout=self.timeout_ms)
                            except PlaywrightTimeoutError:
                                pass
                    else:
                        break

                return {
                    "url": url,
                    "data": all_items,
                    "pages_scraped": pages_scraped,
                }
            finally:
                await browser.close()

    async def _extract_page(
        self,
        page: Page,
        selectors: dict[str, str],
        list_selector: str | None,
    ) -> list[dict[str, Any]]:
        """从当前 page 提取数据。"""
        if not selectors:
            # 没有选择器，返回页面纯文本摘要
            title = await page.title()
            return [{"_title": title}]

        if list_selector:
            return await self._extract_list(page, selectors, list_selector)
        return await self._extract_single(page, selectors)

    @staticmethod
    async def _extract_single(
        page: Page, selectors: dict[str, str]
    ) -> list[dict[str, Any]]:
        """单条目提取。"""
        item: dict[str, Any] = {}
        for field, sel in selectors.items():
            try:
                element = await page.query_selector(sel)
                item[field] = (await element.inner_text()) if element else None
            except PlaywrightError as exc:
                logger.warning("字段提取失败 field=%s selector=%s err=%s", field, sel, exc)
                item[field] = None
        return [item]

    @staticmethod
    async def _extract_list(
        page: Page, selectors: dict[str, str], list_selector: str
    ) -> list[dict[str, Any]]:
        """列表提取：每个 list_selector 元素都按 selectors 抽取字段。"""
        elements = await page.query_selector_all(list_selector)
        items: list[dict[str, Any]] = []
        for el in elements:
            item: dict[str, Any] = {}
            for field, sel in selectors.items():
                try:
                    child = await el.query_selector(sel)
                    item[field] = (await child.inner_text()) if child else None
                except PlaywrightError as exc:
                    logger.warning(
                        "列表字段提取失败 field=%s selector=%s err=%s", field, sel, exc
                    )
                    item[field] = None
            items.append(item)
        return items

    @staticmethod
    async def _click_next(page: Page, next_page_selector: str) -> bool:
        """点击下一页；返回是否成功。"""
        try:
            btn = await page.query_selector(next_page_selector)
            if not btn:
                return False
            await btn.click()
            # 等待网络空闲
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except PlaywrightTimeoutError:
                pass
            return True
        except PlaywrightError as exc:
            logger.warning("翻页失败 selector=%s err=%s", next_page_selector, exc)
            return False


# 模块级单例
scraper = Scraper()
