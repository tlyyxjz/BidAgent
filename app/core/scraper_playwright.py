"""Playwright 页面加载与渲染（从 scraper.py 拆分）。

包含浏览器启动、SSRF 重定向防护、页面导航、快照保存、模板监控。
依赖注入：``async_playwright`` 和 ``user_agent`` 由调用方传入，
确保 ``app.core.scraper`` 命名空间上的 monkeypatch 在测试中生效。
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
)

from app.core.scraper_errors import HttpForbiddenError
from app.core.scraper_extract import click_next, extract_page
from app.core.snapshot_manager import snapshot_manager
from app.core.template_monitor import template_monitor
from app.utils.hostname_cache import HostnameLRUCache
from app.utils.logger import get_logger
from app.utils.url_safety import is_safe_url_async

logger = get_logger("scraper")


async def scrape_with_playwright(
    *,
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
    headless: bool,
    timeout_ms: int,
    playwright_factory: Any,
    user_agent: str,
) -> dict[str, Any]:
    """启动 Playwright 执行实际抓取。

    ``playwright_factory`` 和 ``user_agent`` 由调用方从
    ``app.core.scraper`` 命名空间解析后传入，使测试 monkeypatch 生效。
    """
    ua = user_agent
    proxy_arg = {"server": proxy} if proxy else None

    async with playwright_factory() as p:
        browser: Browser = await p.chromium.launch(
            headless=headless,
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
            context.set_default_timeout(timeout_ms)
            context.set_default_navigation_timeout(timeout_ms)

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
                    await page.wait_for_selector(wait_for, timeout=timeout_ms)
                except PlaywrightTimeoutError:
                    logger.warning(
                        "wait_for_selector 超时 url=%s selector=%s",
                        url, wait_for,
                    )

            all_items: list[dict[str, Any]] = []
            pages_scraped = 0

            for page_num in range(1, max_pages + 1):
                pages_scraped = page_num
                items = await extract_page(page, selectors, list_selector)
                all_items.extend(items)

                # 翻页
                if next_page and page_num < max_pages:
                    clicked = await click_next(page, next_page)
                    if not clicked:
                        logger.info("没有下一页，停止翻页 url=%s at_page=%d", url, page_num)
                        break
                    # 翻页后等待新内容
                    if wait_for:
                        try:
                            await page.wait_for_selector(wait_for, timeout=timeout_ms)
                        except PlaywrightTimeoutError:
                            pass
                else:
                    break

            # v4.1 §5.3 snapshot_manager：保存页面快照（SHA256 哈希 + 版本管理）
            try:
                html_content = await page.content()
                snap_record = await snapshot_manager.save_snapshot(
                    url, html_content,
                    material=False,
                )
                if snap_record.is_new_version:
                    logger.info(
                        "snapshot saved url=%s v%d hash=%s",
                        url[:80], snap_record.version_number,
                        snap_record.content_hash[:16],
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("snapshot save failed url=%s err=%s", url[:80], exc)

            # v4.1 §5.3 template_monitor：检测页面模板结构变更
            if selectors and template_name:
                try:
                    changed = await template_monitor.check(
                        template_name, page, selectors,
                    )
                    if changed:
                        logger.warning(
                            "template structure may have changed name=%s url=%s",
                            template_name, url[:80],
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "template_monitor check failed name=%s err=%s",
                        template_name, exc,
                    )

            return {
                "url": url,
                "data": all_items,
                "pages_scraped": pages_scraped,
            }
        finally:
            await browser.close()
