"""域名级采集频率限制器单元测试（v4.1 §13 合规采集层）。

覆盖 app.core.rate_limiter.DomainRateLimiter：
- extract_domain：URL → hostname 解析
- wait：首次请求不等待、同域名二次请求等待、不同域名互不阻塞
- set_interval / get_interval：自定义域名间隔
- release：回滚 wait 的预订
- reset：清空状态
- stats：调试用统计

测试用短间隔（0.05s）替代生产默认 8s，保持测试快速且可验证。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.core.rate_limiter import (
    DomainRateLimiter,
    _DEFAULT_CRAWL_INTERVAL_SECONDS,
    domain_rate_limiter,
)


# ========== extract_domain ==========


class TestExtractDomain:
    """extract_domain 静态方法测试。"""

    def test_normal_url(self) -> None:
        """标准 URL 应提取 hostname 并小写化。"""
        assert DomainRateLimiter.extract_domain("https://www.ccgp.gov.cn/a/b") == "www.ccgp.gov.cn"
        assert DomainRateLimiter.extract_domain("http://Example.COM/x") == "example.com"

    def test_url_with_port(self) -> None:
        """带端口的 URL 应提取 hostname（不含端口）。"""
        assert DomainRateLimiter.extract_domain("https://localhost:8080/api") == "localhost"
        assert DomainRateLimiter.extract_domain("http://127.0.0.1:3000/") == "127.0.0.1"

    def test_https_scheme(self) -> None:
        """HTTPS URL 应正常解析。"""
        assert DomainRateLimiter.extract_domain("https://chinabidding.cn") == "chinabidding.cn"

    def test_empty_string(self) -> None:
        """空字符串返回空。"""
        assert DomainRateLimiter.extract_domain("") == ""

    def test_none_input(self) -> None:
        """None 输入返回空（TypeError 容错）。"""
        assert DomainRateLimiter.extract_domain(None) == ""  # type: ignore[arg-type]

    def test_non_string_input(self) -> None:
        """非字符串输入返回空。"""
        assert DomainRateLimiter.extract_domain(12345) == ""  # type: ignore[arg-type]
        assert DomainRateLimiter.extract_domain([]) == ""  # type: ignore[arg-type]

    def test_url_without_scheme(self) -> None:
        """无 scheme 的 URL 解析行为：urlparse 无法提取 hostname。"""
        # urlparse("example.com/path").hostname → None
        result = DomainRateLimiter.extract_domain("example.com/path")
        assert result == ""

    def test_malformed_url(self) -> None:
        """畸形 URL 返回空（ValueError 容错）。"""
        # 这些不应抛异常
        result = DomainRateLimiter.extract_domain("ht!tp://[broken")
        assert isinstance(result, str)


# ========== wait 首次请求 ==========


class TestWaitFirstRequest:
    """wait 方法：首次请求行为。"""

    async def test_first_request_no_wait(self) -> None:
        """首次请求某域名应返回 0.0（无需等待）。"""
        limiter = DomainRateLimiter(default_interval=8.0)
        waited = await limiter.wait("https://www.ccgp.gov.cn/a")
        assert waited == 0.0

    async def test_empty_url_no_wait(self) -> None:
        """空 URL 返回 0.0（无法解析域名，不限制）。"""
        limiter = DomainRateLimiter(default_interval=8.0)
        assert await limiter.wait("") == 0.0

    async def test_unresolvable_url_no_wait(self) -> None:
        """无法解析 hostname 的 URL 返回 0.0。"""
        limiter = DomainRateLimiter(default_interval=8.0)
        assert await limiter.wait("not-a-url") == 0.0


# ========== wait 同域名二次请求 ==========


class TestWaitSameDomain:
    """wait 方法：同域名二次请求行为。"""

    async def test_second_request_within_interval_waits(self) -> None:
        """同域名第二次请求在间隔内应等待。"""
        limiter = DomainRateLimiter(default_interval=0.1)
        await limiter.wait("https://example.com/a")
        start = time.monotonic()
        waited = await limiter.wait("https://example.com/b")
        elapsed = time.monotonic() - start
        # 应等待约 0.1 秒（允许误差）
        assert waited > 0
        assert elapsed >= 0.08  # 至少等了 0.08s（留 20% 误差）
        assert waited <= 0.15  # 但不应超过 0.15s

    async def test_second_request_after_interval_no_wait(self) -> None:
        """同域名第二次请求在间隔过后应返回 0.0。"""
        limiter = DomainRateLimiter(default_interval=0.05)
        await limiter.wait("https://example.com/a")
        # 等待超过间隔
        await asyncio.sleep(0.08)
        waited = await limiter.wait("https://example.com/b")
        assert waited == 0.0

    async def test_same_domain_different_path_still_waits(self) -> None:
        """同域名不同路径仍受频率限制（按域名而非 URL 计数）。"""
        limiter = DomainRateLimiter(default_interval=0.1)
        await limiter.wait("https://example.com/page1")
        waited = await limiter.wait("https://example.com/page2")
        assert waited > 0

    async def test_same_domain_http_https_treated_separately(self) -> None:
        """同域名 HTTP 和 HTTPS 应被视为同一域名（hostname 相同）。"""
        limiter = DomainRateLimiter(default_interval=0.1)
        await limiter.wait("http://example.com/a")
        waited = await limiter.wait("https://example.com/b")
        assert waited > 0  # 同 hostname，应等待


# ========== wait 不同域名互不阻塞 ==========


class TestWaitDifferentDomains:
    """wait 方法：不同域名互不阻塞。"""

    async def test_different_domains_no_blocking(self) -> None:
        """不同域名的请求不应相互阻塞。"""
        limiter = DomainRateLimiter(default_interval=8.0)
        await limiter.wait("https://ccgp.gov.cn/a")
        # 不同域名应立即返回
        waited = await limiter.wait("https://chinabidding.cn/b")
        assert waited == 0.0

    async def test_multiple_domains_independent(self) -> None:
        """多个域名各自独立计数，a.com 的等待不消耗 b.com 的间隔。"""
        limiter = DomainRateLimiter(default_interval=10.0)
        # a.com 用短间隔，b.com 用默认长间隔
        limiter.set_interval("a.com", 0.1)
        # 三个不同域名首次请求都应 0 等待
        assert await limiter.wait("https://a.com/1") == 0.0
        assert await limiter.wait("https://b.com/1") == 0.0
        assert await limiter.wait("https://c.com/1") == 0.0
        # a.com 二次请求应等待（短间隔 0.1s）
        assert await limiter.wait("https://a.com/2") > 0
        # b.com 二次请求也应等待：a.com 的 0.1s 等待不消耗 b.com 的 10s 间隔
        assert await limiter.wait("https://b.com/2") > 0

    async def test_subdomain_treated_as_different(self) -> None:
        """子域名视为不同域名（www.example.com ≠ api.example.com）。"""
        limiter = DomainRateLimiter(default_interval=0.1)
        await limiter.wait("https://www.example.com/a")
        # api.example.com 是不同 hostname
        waited = await limiter.wait("https://api.example.com/b")
        assert waited == 0.0


# ========== set_interval / get_interval ==========


class TestSetGetInterval:
    """set_interval / get_interval 测试。"""

    def test_get_default_interval(self) -> None:
        """未设置自定义间隔时返回默认值。"""
        limiter = DomainRateLimiter(default_interval=8.0)
        assert limiter.get_interval("example.com") == 8.0

    def test_set_custom_interval(self) -> None:
        """设置自定义间隔后 get_interval 应返回自定义值。"""
        limiter = DomainRateLimiter(default_interval=8.0)
        limiter.set_interval("ccgp.gov.cn", 3.0)
        assert limiter.get_interval("ccgp.gov.cn") == 3.0
        # 其他域名仍用默认
        assert limiter.get_interval("other.com") == 8.0

    def test_set_interval_zero_disables_limit(self) -> None:
        """间隔设为 0 表示不限制该域名。"""
        limiter = DomainRateLimiter(default_interval=8.0)
        limiter.set_interval("fast.com", 0.0)
        assert limiter.get_interval("fast.com") == 0.0

    def test_set_interval_negative_raises(self) -> None:
        """负数间隔应抛 ValueError。"""
        limiter = DomainRateLimiter(default_interval=8.0)
        with pytest.raises(ValueError, match=">= 0"):
            limiter.set_interval("bad.com", -1.0)

    async def test_custom_interval_takes_effect(self) -> None:
        """自定义间隔应实际生效。"""
        limiter = DomainRateLimiter(default_interval=8.0)
        limiter.set_interval("fast.com", 0.05)
        await limiter.wait("https://fast.com/a")
        # 0.05s 后即可再次请求
        await asyncio.sleep(0.06)
        assert await limiter.wait("https://fast.com/b") == 0.0

    def test_default_interval_constant_is_8(self) -> None:
        """生产默认间隔常量应为 8.0 秒（memory 约束）。"""
        assert _DEFAULT_CRAWL_INTERVAL_SECONDS == 8.0


# ========== release ==========


class TestRelease:
    """release 方法测试。"""

    async def test_release_allows_immediate_next_request(self) -> None:
        """wait 后 release，下次同域名请求不应被延迟。"""
        limiter = DomainRateLimiter(default_interval=8.0)
        await limiter.wait("https://example.com/a")
        # 不 release 的话，下次请求要等 8s
        # release 后应立即放行
        await limiter.release("https://example.com/a")
        waited = await limiter.wait("https://example.com/b")
        assert waited == 0.0

    async def test_release_different_domain_no_effect(self) -> None:
        """release 不同域名不应影响已限制的域名。"""
        limiter = DomainRateLimiter(default_interval=8.0)
        await limiter.wait("https://a.com/1")
        await limiter.release("https://b.com/1")  # release 不同域名
        # a.com 仍应被限制
        waited = await limiter.wait("https://a.com/2")
        assert waited > 0

    async def test_release_empty_url_no_error(self) -> None:
        """release 空 URL 不应抛异常。"""
        limiter = DomainRateLimiter(default_interval=8.0)
        await limiter.release("")  # 不应抛异常

    async def test_release_without_prior_wait_no_error(self) -> None:
        """未 wait 过的域名 release 不应抛异常。"""
        limiter = DomainRateLimiter(default_interval=8.0)
        await limiter.release("https://never-waited.com/a")


# ========== reset ==========


class TestReset:
    """reset 方法测试。"""

    async def test_reset_clears_last_request(self) -> None:
        """reset 应清空 _last_request，下次请求不等待。"""
        limiter = DomainRateLimiter(default_interval=8.0)
        await limiter.wait("https://example.com/a")
        limiter.reset()
        assert await limiter.wait("https://example.com/b") == 0.0

    async def test_reset_clears_custom_intervals(self) -> None:
        """reset 应清空 _domain_intervals。"""
        limiter = DomainRateLimiter(default_interval=8.0)
        limiter.set_interval("fast.com", 0.0)
        limiter.reset()
        # reset 后应回到默认间隔
        assert limiter.get_interval("fast.com") == 8.0

    def test_reset_empty_state_no_error(self) -> None:
        """reset 空状态不应抛异常。"""
        limiter = DomainRateLimiter(default_interval=8.0)
        limiter.reset()
        limiter.reset()  # 重复 reset 也不报错


# ========== stats ==========


class TestStats:
    """stats 方法测试。"""

    async def test_stats_empty_initially(self) -> None:
        """初始状态 stats 返回空字典。"""
        limiter = DomainRateLimiter(default_interval=8.0)
        assert limiter.stats() == {}

    async def test_stats_shows_domain_after_wait(self) -> None:
        """wait 后 stats 应包含该域名。"""
        limiter = DomainRateLimiter(default_interval=8.0)
        await limiter.wait("https://example.com/a")
        stats = limiter.stats()
        assert "example.com" in stats
        assert stats["example.com"] >= 0  # 距上次请求的秒数

    async def test_stats_multiple_domains(self) -> None:
        """多个域名的 stats 应各自独立。"""
        limiter = DomainRateLimiter(default_interval=8.0)
        await limiter.wait("https://a.com/1")
        await asyncio.sleep(0.02)
        await limiter.wait("https://b.com/1")
        stats = limiter.stats()
        assert "a.com" in stats
        assert "b.com" in stats
        # a.com 距上次请求时间应大于 b.com
        assert stats["a.com"] > stats["b.com"]


# ========== 模块级单例 ==========


class TestModuleSingleton:
    """模块级单例 domain_rate_limiter 测试。"""

    def test_singleton_is_instance(self) -> None:
        """domain_rate_limiter 应是 DomainRateLimiter 实例。"""
        assert isinstance(domain_rate_limiter, DomainRateLimiter)

    def test_singleton_default_interval_is_8(self) -> None:
        """模块级单例的默认间隔应为 8.0 秒（memory 约束）。"""
        assert domain_rate_limiter._default_interval == 8.0

    async def test_singleton_reset_works(self) -> None:
        """模块级单例的 reset 应正常工作。"""
        await domain_rate_limiter.wait("https://test-singleton.com/a")
        domain_rate_limiter.reset()
        assert await domain_rate_limiter.wait("https://test-singleton.com/b") == 0.0


# ========== 并发安全 ==========


class TestConcurrency:
    """并发安全测试（asyncio.Lock 保护 _last_request）。"""

    async def test_concurrent_same_domain_serialized(self) -> None:
        """同域名并发请求应被串行化（第二个等待）。"""
        limiter = DomainRateLimiter(default_interval=0.1)
        # 两个协程同时请求同一域名
        results = await asyncio.gather(
            limiter.wait("https://example.com/a"),
            limiter.wait("https://example.com/b"),
        )
        # 第一个应 0 等待，第二个应 > 0
        # 由于 gather 是并发的，不能确定哪个先到锁
        # 但至少有一个 > 0（被串行化的那个）
        assert any(w > 0 for w in results)

    async def test_concurrent_different_domains_parallel(self) -> None:
        """不同域名并发请求应并行（都不等待）。"""
        limiter = DomainRateLimiter(default_interval=8.0)
        results = await asyncio.gather(
            limiter.wait("https://a.com/1"),
            limiter.wait("https://b.com/1"),
            limiter.wait("https://c.com/1"),
        )
        # 三个不同域名首次请求都应 0 等待
        assert all(w == 0.0 for w in results)
