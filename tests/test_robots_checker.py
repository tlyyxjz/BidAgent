"""robots.txt 合规检查器单元测试（v4.1 §5.2 合规采集层）。

覆盖 app.core.robots_checker.RobotsChecker：
- _extract_domain / _get_robots_url：URL 解析
- is_allowed：允许/禁止/不可达场景
- 缓存：TTL 命中、invalidate、reset
- _fetch_and_parse：httpx 请求与解析

测试用 mock httpx 避免真实网络请求。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from urllib.robotparser import RobotFileParser

from app.core.robots_checker import (
    RobotsChecker,
    _ROBOTS_CACHE_TTL_SECONDS,
    _ROBOTS_FETCH_TIMEOUT_SECONDS,
    robots_checker,
)


# ========== _extract_domain ==========


class TestExtractDomain:
    """_extract_domain 静态方法测试。"""

    def test_normal_url(self) -> None:
        assert RobotsChecker._extract_domain("https://www.ccgp.gov.cn/a") == "www.ccgp.gov.cn"
        assert RobotsChecker._extract_domain("http://Example.COM/x") == "example.com"

    def test_empty_string(self) -> None:
        assert RobotsChecker._extract_domain("") == ""

    def test_none_input(self) -> None:
        assert RobotsChecker._extract_domain(None) == ""  # type: ignore[arg-type]

    def test_non_string_input(self) -> None:
        assert RobotsChecker._extract_domain(12345) == ""  # type: ignore[arg-type]

    def test_no_hostname(self) -> None:
        assert RobotsChecker._extract_domain("not-a-url") == ""


# ========== _get_robots_url ==========


class TestGetRobotsUrl:
    """_get_robots_url 静态方法测试。"""

    def test_normal_url(self) -> None:
        assert (
            RobotsChecker._get_robots_url("https://www.ccgp.gov.cn/a/b")
            == "https://www.ccgp.gov.cn/robots.txt"
        )

    def test_http_url(self) -> None:
        assert (
            RobotsChecker._get_robots_url("http://example.com/page")
            == "http://example.com/robots.txt"
        )

    def test_url_with_port(self) -> None:
        assert (
            RobotsChecker._get_robots_url("https://localhost:8080/api")
            == "https://localhost:8080/robots.txt"
        )

    def test_empty_string(self) -> None:
        assert RobotsChecker._get_robots_url("") == ""

    def test_no_scheme(self) -> None:
        assert RobotsChecker._get_robots_url("example.com/path") == ""

    def test_non_string(self) -> None:
        assert RobotsChecker._get_robots_url(None) == ""  # type: ignore[arg-type]


# ========== is_allowed ==========


class TestIsAllowed:
    """is_allowed 方法测试（mock _fetch_and_parse）。"""

    @staticmethod
    def _make_parser(allow_all: bool = True) -> RobotFileParser:
        parser = RobotFileParser()
        if allow_all:
            parser.parse([])
        else:
            parser.parse(["User-agent: *", "Disallow: /"])
        return parser

    async def test_allowed_url_returns_true(self) -> None:
        checker = RobotsChecker()
        with patch.object(
            checker, "_fetch_and_parse", return_value=self._make_parser(True)
        ):
            assert await checker.is_allowed("https://example.com/page") is True

    async def test_disallowed_url_returns_false(self) -> None:
        checker = RobotsChecker()
        with patch.object(
            checker, "_fetch_and_parse", return_value=self._make_parser(False)
        ):
            assert await checker.is_allowed("https://example.com/page") is False

    async def test_unresolvable_domain_returns_true(self) -> None:
        checker = RobotsChecker()
        assert await checker.is_allowed("not-a-url") is True

    async def test_empty_url_returns_true(self) -> None:
        checker = RobotsChecker()
        assert await checker.is_allowed("") is True

    async def test_robots_txt_404_returns_true(self) -> None:
        """robots.txt 404 时返回 True（RFC 9309）。"""
        checker = RobotsChecker()
        empty_parser = RobotFileParser()
        empty_parser.parse([])  # parse([]) sets allow_all behavior
        with patch.object(
            checker, "_fetch_and_parse", return_value=empty_parser
        ):
            assert await checker.is_allowed("https://no-robots.example.com/x") is True


# ========== 缓存行为 ==========


class TestCaching:
    """缓存行为测试。"""

    async def test_cache_hit_avoids_refetch(self) -> None:
        checker = RobotsChecker(cache_ttl=100.0)
        mock_fetch = AsyncMock(return_value=RobotFileParser())
        with patch.object(checker, "_fetch_and_parse", mock_fetch):
            await checker.is_allowed("https://example.com/a")
            await checker.is_allowed("https://example.com/b")
        assert mock_fetch.call_count == 1

    async def test_cache_expiry_triggers_refetch(self) -> None:
        checker = RobotsChecker(cache_ttl=0.01)
        mock_fetch = AsyncMock(return_value=RobotFileParser())
        with patch.object(checker, "_fetch_and_parse", mock_fetch):
            await checker.is_allowed("https://example.com/a")
            import asyncio
            await asyncio.sleep(0.02)
            await checker.is_allowed("https://example.com/b")
        assert mock_fetch.call_count == 2

    async def test_different_domains_cached_separately(self) -> None:
        checker = RobotsChecker(cache_ttl=100.0)
        mock_fetch = AsyncMock(return_value=RobotFileParser())
        with patch.object(checker, "_fetch_and_parse", mock_fetch):
            await checker.is_allowed("https://a.com/1")
            await checker.is_allowed("https://b.com/1")
        assert mock_fetch.call_count == 2

    async def test_invalidate_clears_single_domain(self) -> None:
        checker = RobotsChecker(cache_ttl=100.0)
        mock_fetch = AsyncMock(return_value=RobotFileParser())
        with patch.object(checker, "_fetch_and_parse", mock_fetch):
            await checker.is_allowed("https://example.com/a")
            checker.invalidate("example.com")
            await checker.is_allowed("https://example.com/b")
        assert mock_fetch.call_count == 2

    async def test_reset_clears_all_cache(self) -> None:
        checker = RobotsChecker(cache_ttl=100.0)
        mock_fetch = AsyncMock(return_value=RobotFileParser())
        with patch.object(checker, "_fetch_and_parse", mock_fetch):
            await checker.is_allowed("https://a.com/1")
            await checker.is_allowed("https://b.com/1")
            checker.reset()
            await checker.is_allowed("https://a.com/2")
            await checker.is_allowed("https://b.com/2")
        assert mock_fetch.call_count == 4

    async def test_stats_shows_cached_domains(self) -> None:
        checker = RobotsChecker(cache_ttl=100.0)
        with patch.object(checker, "_fetch_and_parse", return_value=RobotFileParser()):
            await checker.is_allowed("https://a.com/1")
            stats = checker.stats()
        assert "a.com" in stats


# ========== _fetch_and_parse ==========


class TestFetchAndParse:
    """_fetch_and_parse 方法测试（mock httpx.AsyncClient）。"""

    async def test_successful_fetch_parses_content(self) -> None:
        checker = RobotsChecker()
        robots_content = "User-agent: *\nDisallow: /private\n"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = robots_content

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.core.robots_checker.httpx.AsyncClient", return_value=mock_client):
            parser = await checker._fetch_and_parse(
                "https://example.com/robots.txt", "*"
            )
        assert parser.can_fetch("*", "https://example.com/public") is True
        assert parser.can_fetch("*", "https://example.com/private") is False

    async def test_404_returns_empty_parser(self) -> None:
        checker = RobotsChecker()
        mock_response = MagicMock()
        mock_response.status_code = 404

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.core.robots_checker.httpx.AsyncClient", return_value=mock_client):
            parser = await checker._fetch_and_parse(
                "https://example.com/robots.txt", "*"
            )
        assert parser.can_fetch("*", "https://example.com/anything") is True

    async def test_500_returns_empty_parser(self) -> None:
        checker = RobotsChecker()
        mock_response = MagicMock()
        mock_response.status_code = 500

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.core.robots_checker.httpx.AsyncClient", return_value=mock_client):
            parser = await checker._fetch_and_parse(
                "https://example.com/robots.txt", "*"
            )
        assert parser.can_fetch("*", "https://example.com/anything") is True

    async def test_network_error_returns_empty_parser(self) -> None:
        checker = RobotsChecker()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.core.robots_checker.httpx.AsyncClient", return_value=mock_client):
            parser = await checker._fetch_and_parse(
                "https://example.com/robots.txt", "*"
            )
        assert parser.can_fetch("*", "https://example.com/anything") is True

    async def test_timeout_returns_empty_parser(self) -> None:
        checker = RobotsChecker()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ReadTimeout("read timeout"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.core.robots_checker.httpx.AsyncClient", return_value=mock_client):
            parser = await checker._fetch_and_parse(
                "https://example.com/robots.txt", "*"
            )
        assert parser.can_fetch("*", "https://example.com/anything") is True


# ========== 模块级单例 ==========


class TestModuleSingleton:
    """模块级单例 robots_checker 测试。"""

    def test_singleton_is_instance(self) -> None:
        assert isinstance(robots_checker, RobotsChecker)

    def test_singleton_cache_ttl_is_1800(self) -> None:
        assert robots_checker._cache_ttl == 1800.0

    def test_singleton_fetch_timeout_is_10(self) -> None:
        assert robots_checker._fetch_timeout == 10.0

    def test_constants(self) -> None:
        assert _ROBOTS_CACHE_TTL_SECONDS == 1800.0
        assert _ROBOTS_FETCH_TIMEOUT_SECONDS == 10.0

    def test_singleton_reset_works(self) -> None:
        robots_checker._cache["test.com"] = (RobotFileParser(), 0.0)
        robots_checker.reset()
        assert robots_checker._cache == {}


# ========== 特定 robots.txt 规则测试 ==========


class TestRobotsRules:
    """特定 robots.txt 规则解析测试。"""

    @staticmethod
    def _parse(robots_txt: str) -> RobotFileParser:
        parser = RobotFileParser()
        parser.parse(robots_txt.splitlines())
        return parser

    async def test_disallow_specific_path(self) -> None:
        checker = RobotsChecker()
        robots_txt = "User-agent: *\nDisallow: /private\n"
        mock_fetch = AsyncMock(return_value=self._parse(robots_txt))
        with patch.object(checker, "_fetch_and_parse", mock_fetch):
            assert await checker.is_allowed("https://example.com/public") is True
            assert await checker.is_allowed("https://example.com/private") is False
            assert await checker.is_allowed("https://example.com/private/secret") is False

    async def test_allow_all(self) -> None:
        checker = RobotsChecker()
        mock_fetch = AsyncMock(return_value=self._parse(""))
        with patch.object(checker, "_fetch_and_parse", mock_fetch):
            assert await checker.is_allowed("https://example.com/anything") is True

    async def test_disallow_all(self) -> None:
        checker = RobotsChecker()
        robots_txt = "User-agent: *\nDisallow: /\n"
        mock_fetch = AsyncMock(return_value=self._parse(robots_txt))
        with patch.object(checker, "_fetch_and_parse", mock_fetch):
            assert await checker.is_allowed("https://example.com/anything") is False

    async def test_specific_user_agent_rule(self) -> None:
        checker = RobotsChecker()
        robots_txt = (
            "User-agent: MyBot\n"
            "Disallow: /\n"
            "User-agent: *\n"
            "Disallow:\n"
        )
        mock_fetch = AsyncMock(return_value=self._parse(robots_txt))
        with patch.object(checker, "_fetch_and_parse", mock_fetch):
            assert await checker.is_allowed(
                "https://example.com/x", user_agent="MyBot"
            ) is False
            assert await checker.is_allowed(
                "https://example.com/x", user_agent="OtherBot"
            ) is True
