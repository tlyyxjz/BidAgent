"""域名级采集频率限制器（v4.1 §13 合规采集层）。

对应《标小智 项目总体规划 v4.1》第十三章"合规采集层"中 `rate_limiter.py`
域名级频率限制的要求：

> 具体请求频率根据平台规则、接口限制、响应状态、缓存命中率和实际业务
> 必要性，进行来源级配置。

设计要点：
- 按域名（hostname）独立计数，不同域名互不阻塞（如 ccgp 和 chinabidding
  可并行抓取）。
- 默认间隔 8 秒（memory 约束：8 秒间隔、默认 UA、遇到 403 立即停手）。
- 支持按域名覆盖默认间隔（如某些平台允许更短间隔，某些平台要求更长）。
- 异步等待（`asyncio.sleep`），不阻塞事件循环。
- 协程安全：用 `asyncio.Lock` 保护 `_last_request` 字典，避免并发竞态。
- 内存实现：采集器是单进程，无需 Redis；进程重启后状态清空（保守重新计数）。
- 不记录 URL 或敏感参数到日志（仅记录 hostname 和等待秒数）。

接入方式：
    from app.core.rate_limiter import domain_rate_limiter

    class Scraper:
        async def scrape(self, request):
            url = request["url"]
            waited = await domain_rate_limiter.wait(url)
            if waited > 0:
                logger.debug("rate_limit waited %.2fs for %s", waited, hostname)
            # 然后发起真实请求
"""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlparse

from app.utils.logger import get_logger

logger = get_logger("rate_limiter")


# 默认采集间隔（秒）。memory 约束：8 秒间隔。
_DEFAULT_CRAWL_INTERVAL_SECONDS = 8.0


class DomainRateLimiter:
    """域名级采集频率限制器。

    每个域名独立维护"上次请求时间"，新请求到来时若距上次不足间隔则等待。
    不同域名互不阻塞，支持并发抓取多个平台。

    协程安全：`wait` / `release` 内部用 `asyncio.Lock` 保护 `_last_request`
    字典，避免多个协程并发修改导致间隔计算错误。

    语义说明：
    - `wait(url)` 返回时，调用方应**立即**发起真实请求。函数内部已将
      `_last_request[domain]` 更新为"预计请求发出时间"（now + wait_time），
      因此后续同域名请求会基于此时间计算间隔。
    - 若调用方在 `wait` 返回后因故放弃请求，应调用 `await release(url)`
      回滚 `_last_request`，避免下次请求被错误地延迟。
    """

    def __init__(
        self, default_interval: float = _DEFAULT_CRAWL_INTERVAL_SECONDS
    ) -> None:
        """初始化域名级频率限制器。

        Args:
            default_interval: 默认采集间隔秒数，默认 8.0（memory 约束）。
        """
        self._default_interval: float = float(default_interval)
        # domain -> monotonic 时间戳（上次请求发出时间）
        self._last_request: dict[str, float] = {}
        # domain -> 自定义间隔秒数（覆盖默认）
        self._domain_intervals: dict[str, float] = {}
        # 保护 _last_request / _domain_intervals 的并发访问
        self._lock: asyncio.Lock = asyncio.Lock()

    def set_interval(self, domain: str, interval: float) -> None:
        """为指定域名设置自定义采集间隔。

        用于按平台规则差异化配置（如官方平台允许 5 秒，商业平台要求 10 秒）。

        Args:
            domain: 域名（如 "www.ccgp.gov.cn"）。
            interval: 间隔秒数（必须 >= 0）。

        Raises:
            ValueError: interval 为负数。
        """
        if interval < 0:
            raise ValueError(f"interval 必须 >= 0，实际 {interval}")
        self._domain_intervals[domain] = float(interval)

    def get_interval(self, domain: str) -> float:
        """获取指定域名的采集间隔（自定义或默认）。"""
        return self._domain_intervals.get(domain, self._default_interval)

    @staticmethod
    def extract_domain(url: str) -> str:
        """从 URL 提取域名（hostname，小写）。

        Args:
            url: 完整 URL。

        Returns:
            str: 域名字符串；无法解析时返回空字符串。
        """
        if not url or not isinstance(url, str):
            return ""
        try:
            parsed = urlparse(url)
        except (ValueError, TypeError, AttributeError):
            return ""
        hostname = parsed.hostname
        if not hostname:
            return ""
        return hostname.lower()

    async def wait(self, url: str) -> float:
        """等待直到可以抓取该 URL 的域名。

        计算距上次同域名请求的间隔，若不足则 `asyncio.sleep` 补足。
        不同域名互不阻塞。

        Args:
            url: 待抓取的 URL。

        Returns:
            float: 实际等待的秒数（0.0 表示无需等待）。
        """
        domain = self.extract_domain(url)
        if not domain:
            # 无法解析域名（如本地文件、非法 URL），不限制
            return 0.0

        interval = self.get_interval(domain)
        if interval <= 0:
            # 间隔为 0 表示不限制该域名
            return 0.0

        async with self._lock:
            now = time.monotonic()
            last = self._last_request.get(domain, 0.0)
            elapsed = now - last
            wait_time = max(0.0, interval - elapsed)
            # 更新 last_request 为"预计请求发出时间"，
            # 这样并发到来的第二个请求会基于此时间计算间隔
            self._last_request[domain] = now + wait_time

        if wait_time > 0:
            # 在锁外 sleep，不阻塞其他域名的 wait 调用
            await asyncio.sleep(wait_time)
            logger.debug(
                "rate_limit waited %.2fs domain=%s", wait_time, domain
            )

        return wait_time

    async def release(self, url: str) -> None:
        """回滚 `wait` 对 `_last_request` 的更新。

        当调用方在 `wait` 返回后因故放弃请求（如 URL 校验失败、模板未找到），
        应调用本方法回滚，避免下次同域名请求被错误地延迟一个间隔。

        语义：本方法表示"我放弃了，没真实发请求"，因此删除该域名的
        last_request 记录是正确的。若并发有其他协程也在等同一域名，删除后
        它会立即放行——但这是可接受的，因为该协程的 wait 也只是"预订"了
        时间，并未真实发请求。

        Args:
            url: 待回滚的 URL。
        """
        domain = self.extract_domain(url)
        if not domain:
            return
        async with self._lock:
            self._last_request.pop(domain, None)

    def reset(self) -> None:
        """清空所有域名的频率限制状态（测试辅助）。"""
        self._last_request.clear()
        self._domain_intervals.clear()

    def stats(self) -> dict[str, float]:
        """返回各域名距上次请求的秒数（调试用）。

        Returns:
            dict[str, float]: domain -> 距上次请求的秒数（越大表示越久未请求）。
        """
        now = time.monotonic()
        return {d: now - t for d, t in self._last_request.items()}


# 模块级单例：全应用共享一个域名级频率限制器
domain_rate_limiter = DomainRateLimiter(
    default_interval=_DEFAULT_CRAWL_INTERVAL_SECONDS
)
