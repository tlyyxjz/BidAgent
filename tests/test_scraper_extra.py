"""Extra tests for app/core/scraper.py.

Covers branches not exercised by the existing test_scraper.py:
- SSRF blocked path (is_safe_url returns False)
- robots.txt disallowed path
- cache hit / cache parse failure
- cache set failure (TypeError/ValueError)
- max_pages clamping (< 1 and > 50)
- template merge with cookies + storage_state
- domain_rate_limiter waited > 0 log
- _extract_single field error (PlaywrightError to None)
- _extract_list field error (PlaywrightError to None)
- _click_next no button / PlaywrightError
- non-proxy exception break path
- proxy timeout with report_proxy_failure
- non-string url
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from playwright.async_api import Error as PlaywrightError

from app.core.cache_manager import cache_manager
from app.core.scraper import HttpForbiddenError, ScrapeError, Scraper


class _MockElement:
    def __init__(self, text: str = "") -> None:
        self._text = text

    async def inner_text(self) -> str:
        return self._text

    async def query_selector(self, sel: str) -> "_MockElement | None":
        return None


class _MockPage:
    def __init__(
        self,
        title: str = "Mock Page",
        single_selectors: dict[str, str] | None = None,
        list_elements: list[dict[str, str]] | None = None,
    ) -> None:
        self._title = title
        self._single_selectors = single_selectors or {}
        self._list_elements = list_elements or []
        self.goto = AsyncMock()
        self.wait_for_selector = AsyncMock()
        self.wait_for_load_state = AsyncMock()
        self.route = AsyncMock()
        self.content = AsyncMock(return_value="<html>mock</html>")
        self.query_selector_all = AsyncMock(return_value=[])

    async def title(self) -> str:
        return self._title

    async def query_selector(self, sel: str) -> _MockElement | None:
        text = self._single_selectors.get(sel)
        if text is None:
            return None
        return _MockElement(text)


class _MockPlaywrightCtx:
    def __init__(self, page: _MockPage) -> None:
        self._page = page

    async def __aenter__(self) -> Any:
        p = MagicMock()
        browser = AsyncMock()
        context = AsyncMock()
        p.chromium.launch = AsyncMock(return_value=browser)
        browser.new_context = AsyncMock(return_value=context)
        context.new_page = AsyncMock(return_value=self._page)
        context.set_default_timeout = MagicMock()
        context.set_default_navigation_timeout = MagicMock()
        context.set_extra_http_headers = AsyncMock()
        context.add_cookies = AsyncMock()
        return p

    async def __aexit__(self, *args: Any) -> None:
        pass


def _patch_pw(page: _MockPage) -> Any:
    return patch(
        "app.core.scraper.async_playwright",
        return_value=_MockPlaywrightCtx(page),
    )


def _ok_response() -> MagicMock:
    resp = MagicMock()
    resp.status = 200
    return resp


class TestScraperBlocked:
    async def test_ssrf_blocked_raises_scrape_error(self) -> None:
        scraper = Scraper(headless=True, timeout_ms=1000)
        with patch(
            "app.core.scraper.is_safe_url", return_value=(False, "loopback ip blocked")
        ):
            with pytest.raises(ScrapeError, match="URL"):
                await scraper.scrape({"url": "https://example.com/1"})

    async def test_robots_disallowed_raises_scrape_error(self) -> None:
        scraper = Scraper(headless=True, timeout_ms=1000)
        with patch(
            "app.core.scraper.robots_checker.is_allowed",
            new_callable=AsyncMock,
            return_value=False,
        ):
            with pytest.raises(ScrapeError, match="robots.txt"):
                await scraper.scrape({"url": "https://example.com/2"})

    async def test_non_string_url_raises(self) -> None:
        scraper = Scraper(headless=True, timeout_ms=1000)
        with pytest.raises(ScrapeError):
            await scraper.scrape({"url": 12345})


class TestScraperCache:
    async def test_cache_hit_returns_cached_result(self) -> None:
        url = "https://cache-hit-test.com/1"
        cached = {"url": url, "data": [{"_title": "cached"}], "pages_scraped": 1}
        await cache_manager.set(url, json.dumps(cached))

        scraper = Scraper(headless=True, timeout_ms=1000)
        result = await scraper.scrape({"url": url})
        assert result["from_cache"] is True
        assert result["data"][0]["_title"] == "cached"

    async def test_cache_parse_failure_falls_through(self) -> None:
        url = "https://cache-bad-json.com/1"
        await cache_manager.set(url, "{invalid json}")

        page = _MockPage(single_selectors={"#t": "Hello"})
        page.goto = AsyncMock(return_value=_ok_response())

        scraper = Scraper(headless=True, timeout_ms=1000)
        with _patch_pw(page):
            result = await scraper.scrape({
                "url": url,
                "selectors": {"title": "#t"},
            })
        assert result["data"][0]["title"] == "Hello"

    async def test_cache_set_failure_continues(self) -> None:
        page = _MockPage(single_selectors={"#t": "CacheSetFail"})
        page.goto = AsyncMock(return_value=_ok_response())

        scraper = Scraper(headless=True, timeout_ms=1000)
        with _patch_pw(page):
            with patch.object(
                cache_manager, "set", new_callable=AsyncMock,
                side_effect=TypeError("not serializable"),
            ):
                result = await scraper.scrape({
                    "url": "https://cache-set-fail.com/1",
                    "selectors": {"title": "#t"},
                })
        assert result["data"][0]["title"] == "CacheSetFail"


class TestMaxPagesClamping:
    async def test_max_pages_below_1_clamped_to_1(self) -> None:
        page = _MockPage(single_selectors={"#t": "Clamped"})
        page.goto = AsyncMock(return_value=_ok_response())

        scraper = Scraper(headless=True, timeout_ms=1000)
        with _patch_pw(page):
            result = await scraper.scrape({
                "url": "https://clamp-test.com/1",
                "selectors": {"title": "#t"},
                "max_pages": 0,
            })
        assert result["pages_scraped"] == 1

    async def test_max_pages_above_50_clamped_to_50(self) -> None:
        page = _MockPage(single_selectors={"#t": "Clamped50"})
        page.goto = AsyncMock(return_value=_ok_response())

        scraper = Scraper(headless=True, timeout_ms=1000)
        with _patch_pw(page):
            result = await scraper.scrape({
                "url": "https://clamp50-test.com/1",
                "selectors": {"title": "#t"},
                "max_pages": 100,
            })
        assert result["pages_scraped"] >= 1


class TestTemplateMerge:
    def test_no_template_returns_request_copy(self) -> None:
        request = {"url": "https://test.com", "selectors": {"a": "b"}}
        merged = Scraper._merge_template(request)
        assert merged["url"] == "https://test.com"
        assert merged["selectors"] == {"a": "b"}

    def test_unknown_template_warns_and_returns_copy(self) -> None:
        request = {"url": "https://test.com", "template": "does_not_exist"}
        merged = Scraper._merge_template(request)
        assert merged["url"] == "https://test.com"

    def test_template_selectors_merge_with_user_selectors(self) -> None:
        request = {
            "url": "https://test.com",
            "template": "amazon",
            "selectors": {"custom_field": ".custom"},
        }
        merged = Scraper._merge_template(request)
        assert merged["selectors"].get("custom_field") == ".custom"

    def test_template_user_storage_state_overrides(self) -> None:
        request = {
            "url": "https://test.com",
            "template": "amazon",
            "storage_state": {"cookies": [], "origins": []},
        }
        merged = Scraper._merge_template(request)
        assert merged["storage_state"] == {"cookies": [], "origins": []}

    async def test_scrape_with_user_storage_state(self) -> None:
        page = _MockPage(single_selectors={"#t": "SS"})
        page.goto = AsyncMock(return_value=_ok_response())

        scraper = Scraper(headless=True, timeout_ms=1000)
        with _patch_pw(page):
            result = await scraper.scrape({
                "url": "https://ss-test.com/1",
                "selectors": {"title": "#t"},
                "storage_state": {"cookies": [], "origins": []},
            })
        assert result["data"][0]["title"] == "SS"

    async def test_scrape_with_extra_headers_and_cookies(self) -> None:
        page = _MockPage(single_selectors={"#t": "HC"})
        page.goto = AsyncMock(return_value=_ok_response())

        scraper = Scraper(headless=True, timeout_ms=1000)
        with _patch_pw(page):
            result = await scraper.scrape({
                "url": "https://hc-test.com/1",
                "selectors": {"title": "#t"},
                "extra_headers": {"X-Custom": "val"},
                "cookies": [{"name": "c", "value": "v", "domain": "test.com"}],
            })
        assert result["data"][0]["title"] == "HC"


class TestExtractFieldErrors:
    async def test_extract_single_playwright_error_sets_none(self) -> None:
        page = MagicMock()

        async def _query_raises(sel):
            raise PlaywrightError("element not found")

        page.query_selector = _query_raises
        result = await Scraper._extract_single(page, {"title": "#broken"})
        assert result == [{"title": None}]

    async def test_extract_list_playwright_error_sets_none(self) -> None:
        mock_element = MagicMock()

        async def _child_raises(sel):
            raise PlaywrightError("child not found")

        mock_element.query_selector = _child_raises

        page = MagicMock()
        page.query_selector_all = AsyncMock(return_value=[mock_element])

        result = await Scraper._extract_list(page, {"title": ".child"}, ".item")
        assert len(result) == 1
        assert result[0]["title"] is None


class TestClickNextErrors:
    async def test_click_next_no_button_returns_false(self) -> None:
        page = MagicMock()
        page.query_selector = AsyncMock(return_value=None)
        result = await Scraper._click_next(page, "a.next")
        assert result is False

    async def test_click_next_playwright_error_returns_false(self) -> None:
        button = MagicMock()
        button.click = AsyncMock(side_effect=PlaywrightError("click failed"))

        page = MagicMock()
        page.query_selector = AsyncMock(return_value=button)

        result = await Scraper._click_next(page, "a.next")
        assert result is False

    async def test_click_next_success_with_networkidle_timeout(self) -> None:
        from playwright.async_api import TimeoutError as PWTimeoutError

        button = MagicMock()
        button.click = AsyncMock()

        page = MagicMock()
        page.query_selector = AsyncMock(return_value=button)
        page.wait_for_load_state = AsyncMock(side_effect=PWTimeoutError("timeout"))

        result = await Scraper._click_next(page, "a.next")
        assert result is True


class TestProxyRetryPaths:
    async def test_proxy_timeout_reports_failure(self) -> None:
        from playwright.async_api import TimeoutError as PWTimeoutError

        page = _MockPage()
        page.goto = AsyncMock(side_effect=PWTimeoutError("nav timeout"))

        scraper = Scraper(headless=True, timeout_ms=500)
        with patch("app.core.scraper.get_proxy_pool", return_value=["http://p1", "http://p2"]):
            with patch("app.core.scraper.get_random_proxy", return_value="http://p1"):
                with patch("app.core.scraper.report_proxy_failure") as mock_report:
                    with _patch_pw(page):
                        with pytest.raises(ScrapeError):
                            await scraper.scrape({
                                "url": "https://proxy-timeout.com/1",
                                "selectors": {"x": "y"},
                            })
        mock_report.assert_called()

    async def test_proxy_error_reports_failure(self) -> None:
        page = _MockPage()
        page.goto = AsyncMock(side_effect=PlaywrightError("network error"))

        scraper = Scraper(headless=True, timeout_ms=500)
        with patch("app.core.scraper.get_proxy_pool", return_value=["http://p1", "http://p2"]):
            with patch("app.core.scraper.get_random_proxy", return_value="http://p1"):
                with patch("app.core.scraper.report_proxy_failure") as mock_report:
                    with _patch_pw(page):
                        with pytest.raises(ScrapeError):
                            await scraper.scrape({
                                "url": "https://proxy-error.com/1",
                                "selectors": {"x": "y"},
                            })
        mock_report.assert_called()

    async def test_non_proxy_exception_breaks_immediately(self) -> None:
        page = _MockPage()
        page.goto = AsyncMock(side_effect=RuntimeError("unexpected"))

        scraper = Scraper(headless=True, timeout_ms=500)
        with patch("app.core.scraper.get_proxy_pool", return_value=["http://p1", "http://p2"]):
            with patch("app.core.scraper.get_random_proxy", return_value="http://p1") as mock_proxy:
                with _patch_pw(page):
                    with pytest.raises(ScrapeError):
                        await scraper.scrape({
                            "url": "https://runtime-err.com/1",
                            "selectors": {"x": "y"},
                        })
        assert mock_proxy.call_count == 1


class TestRateLimitWaitedLog:
    async def test_waited_log_emitted(self) -> None:
        page = _MockPage(single_selectors={"#t": "Waited"})
        page.goto = AsyncMock(return_value=_ok_response())

        scraper = Scraper(headless=True, timeout_ms=1000)
        with patch(
            "app.core.scraper.domain_rate_limiter.wait",
            new_callable=AsyncMock,
            return_value=0.5,
        ):
            with _patch_pw(page):
                result = await scraper.scrape({
                    "url": "https://waited-log.com/1",
                    "selectors": {"title": "#t"},
                })
        assert result["data"][0]["title"] == "Waited"


class TestNoSelectors:
    async def test_no_selectors_returns_title(self) -> None:
        page = _MockPage(title="Bare Page")
        page.goto = AsyncMock(return_value=_ok_response())

        scraper = Scraper(headless=True, timeout_ms=1000)
        with _patch_pw(page):
            result = await scraper.scrape({"url": "https://bare-title.com/1"})
        assert result["data"][0]["_title"] == "Bare Page"
