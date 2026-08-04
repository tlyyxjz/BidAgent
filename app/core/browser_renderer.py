"""v4.1 §5.3 浏览器渲染器组件。

职责：使用 Playwright 渲染动态页面，支持：
- 页面加载与等待
- 元素交互（点击、滚动、输入）
- 列表分页（点击下一页）
- 内容提取

与 http_fetcher.py 的区别：
- browser_renderer：用于需要 JS 渲染的动态页面（如 ccgp/ggzy）
- http_fetcher：用于静态 HTML / JSON API

与 scraper.py 的关系：
- scraper.py 内部 _scrape_with_playwright 方法承担渲染职责
- browser_renderer 是该职责的独立组件抽象，可被 scraper.py 委托
"""
# pragma: no cover — Playwright 浏览器渲染需要真实浏览器环境
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Optional

from app.utils.logger import get_logger

logger = get_logger("browser_renderer")

# 默认页面加载超时（毫秒）
DEFAULT_PAGE_TIMEOUT = 30000

# 默认等待元素超时（毫秒）
DEFAULT_WAIT_TIMEOUT = 10000


@dataclass
class RenderResult:
    """浏览器渲染结果。"""

    url: str
    html: str  # 渲染后的 HTML
    title: str = ""
    status_code: int = 200
    elapsed_ms: float = 0.0
    error: Optional[str] = None
    pages_crawled: int = 1  # 列表页场景下抓取的页数
    extracted_items: list[dict[str, Any]] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """是否成功。"""
        return self.error is None and bool(self.html)


class BrowserRenderer:
    """浏览器渲染器。

    v4.1 §5.3 组件 2：负责 Playwright 浏览器渲染抓取。
    """

    def __init__(
        self,
        page_timeout: int = DEFAULT_PAGE_TIMEOUT,
        wait_timeout: int = DEFAULT_WAIT_TIMEOUT,
    ) -> None:
        self.page_timeout = page_timeout
        self.wait_timeout = wait_timeout

    @asynccontextmanager
    async def _acquire_page(self):
        """获取浏览器页面（async context manager，可被测试 mock）。

        内部使用 BrowserPool.acquire() + new_context + new_page。
        """
        from app.core.browser_pool import BrowserPool

        slot = await BrowserPool.acquire()
        context = None
        page = None
        try:
            context = await slot.browser.new_context(
                locale="zh-CN",
                viewport={"width": 1366, "height": 768},
            )
            page = await context.new_page()
            yield page
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass
            if context:
                try:
                    await context.close()
                except Exception:
                    pass
            await BrowserPool.release(slot)

    async def render(
        self,
        url: str,
        wait_selector: Optional[str] = None,
        wait_timeout: Optional[int] = None,
    ) -> RenderResult:
        """渲染单个页面。

        Args:
            url: 目标 URL
            wait_selector: 等待元素出现的 CSS 选择器（None 表示只等 load 事件）
            wait_timeout: 等待超时（毫秒），None 用默认值

        Returns:
            RenderResult
        """
        import time

        start = time.perf_counter()
        try:
            async with self._acquire_page() as page:
                await page.goto(url, wait_until="domcontentloaded", timeout=self.page_timeout)

                if wait_selector:
                    timeout = wait_timeout or self.wait_timeout
                    try:
                        await page.wait_for_selector(wait_selector, timeout=timeout)
                    except Exception as e:
                        logger.warning(f"wait_selector timeout: {wait_selector} - {e}")

                html = await page.content()
                title = await page.title()
                elapsed_ms = (time.perf_counter() - start) * 1000

                return RenderResult(
                    url=url,
                    html=html,
                    title=title,
                    elapsed_ms=elapsed_ms,
                )
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.error(f"render failed: {url} - {e}")
            return RenderResult(
                url=url,
                html="",
                elapsed_ms=elapsed_ms,
                error=str(e),
            )

    async def render_list(
        self,
        url: str,
        item_selector: str,
        next_page_selector: str,
        max_pages: int = 10,
        wait_selector: Optional[str] = None,
    ) -> RenderResult:
        """渲染列表页（含分页）。

        Args:
            url: 起始 URL
            item_selector: 列表项 CSS 选择器
            next_page_selector: 下一页按钮 CSS 选择器
            max_pages: 最大翻页数
            wait_selector: 等待元素出现的 CSS 选择器

        Returns:
            RenderResult，extracted_items 含所有页的列表项
        """
        import time

        start = time.perf_counter()
        all_items: list[dict[str, Any]] = []
        pages_crawled = 0
        last_error: Optional[str] = None

        try:
            async with self._acquire_page() as page:
                current_url = url
                for page_num in range(max_pages):
                    await page.goto(current_url, wait_until="domcontentloaded", timeout=self.page_timeout)

                    if wait_selector:
                        try:
                            await page.wait_for_selector(wait_selector, timeout=self.wait_timeout)
                        except Exception:
                            pass

                    # 提取列表项
                    items = await self._extract_items(page, item_selector)
                    all_items.extend(items)
                    pages_crawled += 1

                    # 点击下一页
                    clicked = await self._click_next(page, next_page_selector)
                    if not clicked:
                        break

                html = await page.content()
                title = await page.title()
                elapsed_ms = (time.perf_counter() - start) * 1000

                return RenderResult(
                    url=url,
                    html=html,
                    title=title,
                    elapsed_ms=elapsed_ms,
                    pages_crawled=pages_crawled,
                    extracted_items=all_items,
                )
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000
            last_error = str(e)
            logger.error(f"render_list failed: {url} - {e}")

        return RenderResult(
            url=url,
            html="",
            elapsed_ms=elapsed_ms,
            error=last_error,
            pages_crawled=pages_crawled,
            extracted_items=all_items,
        )

    async def _extract_items(self, page, item_selector: str) -> list[dict[str, Any]]:
        """提取列表项。"""
        try:
            elements = await page.query_selector_all(item_selector)
            items: list[dict[str, Any]] = []
            for elem in elements:
                text = await elem.inner_text()
                items.append({"text": text.strip()})
            return items
        except Exception as e:
            logger.warning(f"extract_items failed: {e}")
            return []

    async def _click_next(self, page, next_page_selector: str) -> bool:
        """点击下一页按钮，返回是否成功点击。"""
        try:
            next_btn = await page.query_selector(next_page_selector)
            if next_btn:
                is_visible = await next_btn.is_visible()
                if is_visible:
                    await next_btn.click()
                    await page.wait_for_load_state("domcontentloaded")
                    return True
            return False
        except Exception as e:
            logger.warning(f"click_next failed: {e}")
            return False
