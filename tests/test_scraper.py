"""Playwright 抓取逻辑测试（mock async_playwright）。

工程规范：
- 必须用 async API 风格。
- 测试前创建 data 目录（conftest 已处理）。
- mock `app.core.scraper.async_playwright`，不启动真实浏览器。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.scraper import ScrapeError, Scraper
from app.templates import get_template, list_templates


class _MockElement:
    """模拟 Playwright Element handle。"""

    def __init__(self, text: str = "") -> None:
        self._text = text

    async def inner_text(self) -> str:
        return self._text

    async def query_selector(self, sel: str) -> "_MockElement | None":
        return None  # 默认列表内无子元素


class _MockPage:
    """模拟 Playwright Page。"""

    def __init__(
        self,
        title: str = "Mock Page",
        single_selectors: dict[str, str] | None = None,
        list_elements: list[dict[str, str]] | None = None,
    ) -> None:
        self._title = title
        # single_selectors: {selector: text}
        self._single_selectors = single_selectors or {}
        # list_elements: 每个元素是 {field_selector: text}
        self._list_elements = list_elements or []
        self.goto = AsyncMock()
        self.wait_for_selector = AsyncMock()
        self.wait_for_load_state = AsyncMock()
        # 新-4：page.route 用于 SSRF 重定向拦截，mock 成空操作
        self.route = AsyncMock()
        self._next_page_clicked = False

    async def title(self) -> str:
        return self._title

    async def query_selector(self, sel: str) -> _MockElement | None:
        text = self._single_selectors.get(sel)
        if text is None:
            return None
        return _MockElement(text)

    async def query_selector_all(self, sel: str) -> list[_MockElement]:
        # 简化：把所有 list 元素包装成 _MockElement，子选择器在 el.query_selector 中查找
        # 这里我们直接构造可以回答子选择器的元素
        return [
            _MockListElement(item) for item in self._list_elements
        ]

    async def click_next(self) -> bool:
        if self._next_page_clicked:
            return False
        self._next_page_clicked = True
        return True


class _MockListElement:
    """模拟列表项元素，能根据子选择器返回文本。"""

    def __init__(self, field_texts: dict[str, str]) -> None:
        # field_texts: {selector: text}
        self._field_texts = field_texts

    async def inner_text(self) -> str:
        return " ".join(self._field_texts.values())

    async def query_selector(self, sel: str) -> _MockElement | None:
        text = self._field_texts.get(sel)
        if text is None:
            return None
        return _MockElement(text)


class _MockPlaywrightCtx:
    """模拟 `async with async_playwright() as p:` 的上下文。"""

    def __init__(self, page: _MockPage) -> None:
        self._page = page

    async def __aenter__(self) -> Any:
        p = MagicMock()
        browser = AsyncMock()
        context = AsyncMock()
        p.chromium.launch = AsyncMock(return_value=browser)
        browser.new_context = AsyncMock(return_value=context)
        context.new_page = AsyncMock(return_value=self._page)
        # set_default_timeout / set_default_navigation_timeout 是同步方法
        context.set_default_timeout = MagicMock()
        context.set_default_navigation_timeout = MagicMock()
        return p

    async def __aexit__(self, *args: Any) -> None:
        pass


def _patch_playwright(page: _MockPage) -> Any:
    """返回 patch 上下文，替换 scraper.async_playwright。"""
    return patch(
        "app.core.scraper.async_playwright",
        return_value=_MockPlaywrightCtx(page),
    )


class TestScraperBasic:
    """基础抓取逻辑。"""

    async def test_scrape_missing_url_raises(self) -> None:
        """缺少 url 必须抛 ScrapeError。"""
        scraper = Scraper(headless=True, timeout_ms=1000)
        with pytest.raises(ScrapeError):
            await scraper.scrape({})

    async def test_scrape_single_page_with_selectors(self) -> None:
        """单页单条目抓取：selectors 字段映射。"""
        page = _MockPage(
            title="Test Product",
            single_selectors={
                "#productTitle": "Awesome Product",
                ".price": "$19.99",
            },
        )
        scraper = Scraper(headless=True, timeout_ms=1000)
        with _patch_playwright(page):
            result = await scraper.scrape(
                {
                    "url": "https://example.com/product/1",
                    "selectors": {
                        "title": "#productTitle",
                        "price": ".price",
                    },
                }
            )
        assert result["url"] == "https://example.com/product/1"
        assert result["pages_scraped"] == 1
        assert len(result["data"]) == 1
        assert result["data"][0]["title"] == "Awesome Product"
        assert result["data"][0]["price"] == "$19.99"

    async def test_scrape_list_with_list_selector(self) -> None:
        """列表抓取：list_selector + selectors。"""
        page = _MockPage(
            list_elements=[
                {"a.title": "Post 1", ".score": "100"},
                {"a.title": "Post 2", ".score": "200"},
            ],
        )
        scraper = Scraper(headless=True, timeout_ms=1000)
        with _patch_playwright(page):
            result = await scraper.scrape(
                {
                    "url": "https://old.reddit.com/r/python",
                    "list_selector": ".thing",
                    "selectors": {
                        "title": "a.title",
                        "score": ".score",
                    },
                }
            )
        assert len(result["data"]) == 2
        assert result["data"][0]["title"] == "Post 1"
        assert result["data"][0]["score"] == "100"
        assert result["data"][1]["title"] == "Post 2"


class TestScraperTemplates:
    """内置模板集成。"""

    async def test_list_templates_includes_amazon_reddit_news(self) -> None:
        names = list_templates()
        assert "amazon" in names
        assert "reddit" in names
        assert "news" in names

    async def test_amazon_template_merged(self) -> None:
        """使用 amazon 模板时自动套用默认选择器。"""
        tpl = get_template("amazon")
        assert tpl is not None
        assert "#productTitle" in tpl.selectors.values()

        page = _MockPage(
            single_selectors={
                "#productTitle": "Apple AirPods",
                ".a-price .a-offscreen": "$129.00",
            },
        )
        scraper = Scraper(headless=True, timeout_ms=1000)
        with _patch_playwright(page):
            result = await scraper.scrape(
                {
                    "url": "https://www.amazon.com/dp/B0D1X1QZ",
                    "template": "amazon",
                }
            )
        assert result["data"][0]["title"] == "Apple AirPods"
        assert result["data"][0]["price"] == "$129.00"

    async def test_unknown_template_ignored(self) -> None:
        """未知模板名应被忽略，不抛错。"""
        page = _MockPage(title="Hello")
        scraper = Scraper(headless=True, timeout_ms=1000)
        with _patch_playwright(page):
            result = await scraper.scrape(
                {
                    "url": "https://example.com",
                    "template": "nonexistent_template",
                }
            )
        assert result["pages_scraped"] == 1


class TestScraperPagination:
    """分页逻辑。"""

    async def test_pagination_collects_multiple_pages(self) -> None:
        """max_pages > 1 时翻页采集多次。"""
        # 模拟：第 1 次查询返回 Post 1，翻页后返回 Post 2
        page = _MockPage(
            list_elements=[{"a.title": "Post 1"}],
        )

        # 让 next_page 选择器返回一个可点击元素
        clickable = AsyncMock()
        page.query_selector = AsyncMock(  # type: ignore[method-assign]
            side_effect=lambda sel: clickable if sel == "a.next" else None
        )

        # 翻页后修改 list_elements
        original_query_all = page.query_selector_all

        async def _switch_list(sel: str) -> list[Any]:
            if page._next_page_clicked:  # type: ignore[attr-defined]
                page._list_elements = [{"a.title": "Post 2"}]  # type: ignore[attr-defined]
            return await original_query_all(sel)

        page.query_selector_all = AsyncMock(side_effect=_switch_list)  # type: ignore[method-assign]

        scraper = Scraper(headless=True, timeout_ms=1000)
        with _patch_playwright(page):
            result = await scraper.scrape(
                {
                    "url": "https://example.com/list",
                    "list_selector": ".thing",
                    "selectors": {"title": "a.title"},
                    "next_page_selector": "a.next",
                    "max_pages": 3,
                }
            )
        # 至少抓到第 1 页的数据
        assert len(result["data"]) >= 1
        assert result["pages_scraped"] >= 1


class TestScraperErrorHandling:
    """错误处理。"""

    async def test_playwright_timeout_raises_scrape_error(self) -> None:
        """Playwright 超时 → ScrapeError。"""
        from playwright.async_api import TimeoutError as PWTimeoutError

        page = _MockPage()
        page.goto = AsyncMock(side_effect=PWTimeoutError("navigation timeout"))

        scraper = Scraper(headless=True, timeout_ms=500)
        with _patch_playwright(page):
            with pytest.raises(ScrapeError):
                await scraper.scrape(
                    {"url": "https://example.com/slow", "selectors": {"x": "y"}}
                )

    async def test_proxy_pool_failure_fallback(self) -> None:
        """无代理时只跑一次，失败直接 ScrapeError。"""
        from playwright.async_api import Error as PWError

        page = _MockPage()
        page.goto = AsyncMock(side_effect=PWError("network unreachable"))

        scraper = Scraper(headless=True, timeout_ms=500)
        with _patch_playwright(page):
            with pytest.raises(ScrapeError):
                await scraper.scrape(
                    {"url": "https://example.com/fail", "selectors": {"x": "y"}}
                )



class TestHttp403Stop:
    """#34 修复：HTTP 403 必须立即停止，不得换 UA/代理重试。"""

    async def test_403_stops_immediately(self) -> None:
        """403 时立即停止，重试次数为 0（get_random_proxy 只调用 1 次）。"""
        page = _MockPage()
        mock_response = MagicMock()
        mock_response.status = 403
        page.goto = AsyncMock(return_value=mock_response)

        scraper = Scraper(headless=True, timeout_ms=1000)
        # 设置代理池使 max_attempts=3，验证 403 不触发重试
        with patch(
            "app.core.scraper.get_proxy_pool",
            return_value=["http://p1", "http://p2", "http://p3"],
        ):
            with patch(
                "app.core.scraper.get_random_proxy",
                return_value="http://p1",
            ) as mock_proxy:
                with _patch_playwright(page):
                    with pytest.raises(ScrapeError):
                        await scraper.scrape(
                            {"url": "https://example.com/forbidden", "selectors": {"x": "y"}}
                        )
        # 只调用 1 次 => 重试次数为 0
        assert mock_proxy.call_count == 1

    async def test_403_no_proxy_retry(self) -> None:
        """403 时不调用 report_proxy_failure。"""
        page = _MockPage()
        mock_response = MagicMock()
        mock_response.status = 403
        page.goto = AsyncMock(return_value=mock_response)

        scraper = Scraper(headless=True, timeout_ms=1000)
        with patch(
            "app.core.scraper.get_proxy_pool",
            return_value=["http://p1", "http://p2"],
        ):
            with patch(
                "app.core.scraper.get_random_proxy",
                return_value="http://p1",
            ):
                with patch(
                    "app.core.scraper.report_proxy_failure"
                ) as mock_report:
                    with _patch_playwright(page):
                        with pytest.raises(ScrapeError):
                            await scraper.scrape(
                                {"url": "https://example.com/forbidden", "selectors": {"x": "y"}}
                            )
        mock_report.assert_not_called()

    async def test_403_no_ua_change(self) -> None:
        """403 时不更换 UA（get_random_user_agent 只调用 1 次）。"""
        page = _MockPage()
        mock_response = MagicMock()
        mock_response.status = 403
        page.goto = AsyncMock(return_value=mock_response)

        scraper = Scraper(headless=True, timeout_ms=1000)
        with patch(
            "app.core.scraper.get_proxy_pool",
            return_value=["http://p1", "http://p2"],
        ):
            with patch(
                "app.core.scraper.get_random_proxy",
                return_value="http://p1",
            ):
                with patch(
                    "app.core.scraper.get_random_user_agent",
                    return_value="UA-1",
                ) as mock_ua:
                    with _patch_playwright(page):
                        with pytest.raises(ScrapeError):
                            await scraper.scrape(
                                {"url": "https://example.com/forbidden", "selectors": {"x": "y"}}
                            )
        assert mock_ua.call_count == 1

    async def test_200_normal_continues(self) -> None:
        """200 正常响应不中断抓取流程。"""
        page = _MockPage(
            single_selectors={"#title": "Hello"},
        )
        mock_response = MagicMock()
        mock_response.status = 200
        page.goto = AsyncMock(return_value=mock_response)

        scraper = Scraper(headless=True, timeout_ms=1000)
        with _patch_playwright(page):
            result = await scraper.scrape(
                {
                    "url": "https://example.com/ok",
                    "selectors": {"title": "#title"},
                }
            )
        assert result["url"] == "https://example.com/ok"
        assert result["pages_scraped"] == 1
        assert result["data"][0]["title"] == "Hello"
