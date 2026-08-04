"""robots_checker.py 补充测试：提升覆盖率 94% -> 95%+.

覆盖未覆盖行: 79-80, 100-101, 178

策略:
- _extract_domain 中 urlparse 抛异常时返回空字符串 (行 79-80)
- _get_robots_url 中 urlparse 抛异常时返回空字符串 (行 100-101)
- is_allowed 中 robots_url 为空时返回 True (行 178, 协议相对 URL 触发)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from urllib.robotparser import RobotFileParser

from app.core.robots_checker import RobotsChecker, robots_checker


# ============================================================
# 测试套件 1: _extract_domain 异常处理 (行 79-80)
# ============================================================

class TestExtractDomainException:
    """覆盖 _extract_domain 中 urlparse 抛异常的分支."""

    def test_urlparse_value_error_caught(self, monkeypatch):
        """行 79-80: urlparse 抛 ValueError 时返回空字符串."""
        def raise_value_error(url):
            raise ValueError("invalid URL")

        monkeypatch.setattr(
            "app.core.robots_checker.urlparse", raise_value_error
        )
        result = RobotsChecker._extract_domain("http://example.com/path")
        assert result == ""

    def test_urlparse_type_error_caught(self, monkeypatch):
        """行 79-80: urlparse 抛 TypeError 时返回空字符串."""
        def raise_type_error(url):
            raise TypeError("type error")

        monkeypatch.setattr(
            "app.core.robots_checker.urlparse", raise_type_error
        )
        result = RobotsChecker._extract_domain("http://example.com/path")
        assert result == ""

    def test_urlparse_attribute_error_caught(self, monkeypatch):
        """行 79-80: urlparse 抛 AttributeError 时返回空字符串."""
        def raise_attr_error(url):
            raise AttributeError("attribute error")

        monkeypatch.setattr(
            "app.core.robots_checker.urlparse", raise_attr_error
        )
        result = RobotsChecker._extract_domain("http://example.com/path")
        assert result == ""


# ============================================================
# 测试套件 2: _get_robots_url 异常处理 (行 100-101)
# ============================================================

class TestGetRobotsUrlException:
    """覆盖 _get_robots_url 中 urlparse 抛异常的分支."""

    def test_urlparse_value_error_caught(self, monkeypatch):
        """行 100-101: urlparse 抛 ValueError 时返回空字符串."""
        def raise_value_error(url):
            raise ValueError("invalid URL")

        monkeypatch.setattr(
            "app.core.robots_checker.urlparse", raise_value_error
        )
        result = RobotsChecker._get_robots_url("http://example.com/path")
        assert result == ""

    def test_urlparse_type_error_caught(self, monkeypatch):
        """行 100-101: urlparse 抛 TypeError 时返回空字符串."""
        def raise_type_error(url):
            raise TypeError("type error")

        monkeypatch.setattr(
            "app.core.robots_checker.urlparse", raise_type_error
        )
        result = RobotsChecker._get_robots_url("http://example.com/path")
        assert result == ""

    def test_urlparse_attribute_error_caught(self, monkeypatch):
        """行 100-101: urlparse 抛 AttributeError 时返回空字符串."""
        def raise_attr_error(url):
            raise AttributeError("attribute error")

        monkeypatch.setattr(
            "app.core.robots_checker.urlparse", raise_attr_error
        )
        result = RobotsChecker._get_robots_url("http://example.com/path")
        assert result == ""


# ============================================================
# 测试套件 3: is_allowed robots_url 为空 (行 178)
# ============================================================

class TestIsAllowedEmptyRobotsUrl:
    """覆盖 is_allowed 中 robots_url 为空时返回 True 的分支."""

    async def test_protocol_relative_url_returns_true(self):
        """行 178: 协议相对 URL (//example.com/path) 的 scheme 为空,
        _get_robots_url 返回空字符串, is_allowed 返回 True.
        """
        checker = RobotsChecker()
        result = await checker.is_allowed("//example.com/path")
        assert result is True

    async def test_protocol_relative_url_with_port_returns_true(self):
        """行 178: 带端口的协议相对 URL 也返回 True."""
        checker = RobotsChecker()
        result = await checker.is_allowed("//example.com:8080/path")
        assert result is True

    async def test_protocol_relative_url_not_cached(self):
        """行 178: 协议相对 URL 返回 True 且不触发 robots.txt 获取."""
        checker = RobotsChecker(cache_ttl=100.0)
        mock_fetch = AsyncMock(return_value=RobotFileParser())
        with patch.object(checker, "_fetch_and_parse", mock_fetch):
            await checker.is_allowed("//example.com/path")
        assert mock_fetch.call_count == 0


# ============================================================
# 测试套件 4: 综合补充
# ============================================================

class TestRobotsCheckerExtra:
    """补充边界测试."""

    @staticmethod
    def _allow_all_parser():
        parser = RobotFileParser()
        parser.parse([])
        return parser

    async def test_url_with_path_query_fragment(self):
        """带路径、查询参数和片段的 URL 正常处理."""
        checker = RobotsChecker()
        with patch.object(
            checker,
            "_fetch_and_parse",
            return_value=self._allow_all_parser(),
        ):
            result = await checker.is_allowed(
                "https://example.com/path?query=1#fragment"
            )
        assert result is True

    async def test_invalidate_nonexistent_domain_no_error(self):
        """invalidate 不存在的域名不报错."""
        checker = RobotsChecker()
        checker.invalidate("nonexistent.example.com")

    async def test_invalidate_case_insensitive(self):
        """invalidate 大小写不敏感."""
        checker = RobotsChecker(cache_ttl=100.0)
        mock_fetch = AsyncMock(return_value=RobotFileParser())
        with patch.object(checker, "_fetch_and_parse", mock_fetch):
            await checker.is_allowed("https://Example.COM/path")
            checker.invalidate("example.com")
            await checker.is_allowed("https://Example.COM/path")
        assert mock_fetch.call_count == 2

    def test_stats_empty_cache(self):
        """空缓存的 stats 返回空 dict."""
        checker = RobotsChecker()
        assert checker.stats() == {}

    async def test_cache_after_successful_fetch(self):
        """成功获取后缓存被填充."""
        checker = RobotsChecker(cache_ttl=100.0)
        mock_fetch = AsyncMock(return_value=self._allow_all_parser())
        with patch.object(checker, "_fetch_and_parse", mock_fetch):
            await checker.is_allowed("https://example.com/a")
            stats = checker.stats()
        assert "example.com" in stats
        assert stats["example.com"] >= 0
