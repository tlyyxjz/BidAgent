"""robots.txt 合规检查器（v4.1 §5.2 合规采集层）。

对应《标小智 项目总体规划 v4.1》第五章第 2 节"合规原则"：

> - 检查 robots.txt、服务条款及数据使用限制
> - 触发验证码、403 或封禁时停止访问，不进行规避

设计要点：
- 采集前检查目标 URL 是否被站点 robots.txt 允许。
- 用标准库 `urllib.robotparser` 解析 robots.txt（成熟、无额外依赖）。
- 异步获取 robots.txt（httpx），不阻塞事件循环。
- 按域名缓存解析结果（TTL 30 分钟），避免每次采集都重新获取。
- robots.txt 不可达（404/超时/5xx）时默认允许（RFC 9309 惯例：
  无 robots.txt 等同于完全允许）。
- 不记录 URL 路径到日志（仅记录 hostname 和 allow/deny 结果）。

接入方式：
    from app.core.robots_checker import robots_checker

    class Scraper:
        async def scrape(self, request):
            url = request["url"]
            allowed = await robots_checker.is_allowed(url, user_agent=ua)
            if not allowed:
                raise ScrapeError("robots.txt 禁止采集该 URL")
            # 然后发起真实请求
"""

from __future__ import annotations

import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from app.utils.logger import get_logger

logger = get_logger("robots_checker")


# robots.txt 缓存 TTL（秒）。30 分钟平衡时效性与性能。
_ROBOTS_CACHE_TTL_SECONDS = 1800.0

# 获取 robots.txt 的超时（秒）。避免站点无响应时长时间阻塞。
_ROBOTS_FETCH_TIMEOUT_SECONDS = 10.0


class RobotsChecker:
    """robots.txt 合规检查器。

    每个域名独立缓存 robots.txt 解析结果，TTL 到期后重新获取。
    不可达的 robots.txt 视为"完全允许"（RFC 9309 惯例）。
    """

    def __init__(
        self,
        cache_ttl: float = _ROBOTS_CACHE_TTL_SECONDS,
        fetch_timeout: float = _ROBOTS_FETCH_TIMEOUT_SECONDS,
    ) -> None:
        """初始化 robots.txt 检查器。

        Args:
            cache_ttl: 域名级缓存 TTL 秒数，默认 1800（30 分钟）。
            fetch_timeout: 获取 robots.txt 的超时秒数，默认 10。
        """
        self._cache_ttl: float = float(cache_ttl)
        self._fetch_timeout: float = float(fetch_timeout)
        # domain -> (RobotFileParser, fetch_timestamp)
        self._cache: dict[str, tuple[RobotFileParser, float]] = {}

    @staticmethod
    def _extract_domain(url: str) -> str:
        """从 URL 提取域名（hostname，小写）。"""
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

    @staticmethod
    def _get_robots_url(url: str) -> str:
        """从目标 URL 构造 robots.txt URL。

        Args:
            url: 目标采集 URL。

        Returns:
            str: ``{scheme}://{host}/robots.txt``；无法解析时返回空字符串。
        """
        if not url or not isinstance(url, str):
            return ""
        try:
            parsed = urlparse(url)
        except (ValueError, TypeError, AttributeError):
            return ""
        if not parsed.scheme or not parsed.hostname:
            return ""
        return f"{parsed.scheme}://{parsed.netloc}/robots.txt"

    async def _fetch_and_parse(
        self, robots_url: str, user_agent: str
    ) -> RobotFileParser:
        """异步获取并解析 robots.txt。

        不可达时返回一个"全允许"的 parser（RFC 9309 惯例）。

        Args:
            robots_url: robots.txt 的完整 URL。
            user_agent: 用于读取 robots.txt 的 User-Agent。

        Returns:
            RobotFileParser: 解析后的 parser。
        """
        parser = RobotFileParser()
        try:
            async with httpx.AsyncClient(
                timeout=self._fetch_timeout,
                follow_redirects=True,
                headers={"User-Agent": user_agent},
            ) as client:
                response = await client.get(robots_url)
                if response.status_code >= 400:
                    # 404 或 5xx：无 robots.txt，视为全允许
                    # 注意：RobotFileParser 未调用 parse() 时 can_fetch 返回 False
                    # （Python 3.10+ 行为），必须调用 parse([]) 初始化为"全允许"
                    logger.debug(
                        "robots.txt unreachable status=%d host=%s",
                        response.status_code,
                        robots_url.split("/")[2] if "/" in robots_url else "?",
                    )
                    parser.parse([])
                    return parser
                parser.parse(response.text.splitlines())
        except (httpx.HTTPError, OSError, Exception) as exc:  # noqa: BLE001
            logger.debug(
                "robots.txt fetch failed err=%s host=%s",
                type(exc).__name__,
                robots_url.split("/")[2] if "/" in robots_url else "?",
            )
            parser.parse([])
            return parser
        return parser

    async def is_allowed(
        self, url: str, user_agent: str = "*"
    ) -> bool:
        """检查 URL 是否被 robots.txt 允许采集。

        首次检查某域名时异步获取其 robots.txt 并缓存；
        后续检查直接用缓存（TTL 到期后重新获取）。

        Args:
            url: 待采集的目标 URL。
            user_agent: 采集器使用的 User-Agent，默认 "*"（匹配所有规则）。

        Returns:
            bool: True 表示允许采集；False 表示被 robots.txt 禁止。
        """
        domain = self._extract_domain(url)
        if not domain:
            return True

        cached = self._cache.get(domain)
        now = time.monotonic()
        if cached is not None:
            parser, fetched_at = cached
            if now - fetched_at < self._cache_ttl:
                return parser.can_fetch(user_agent, url)

        robots_url = self._get_robots_url(url)
        if not robots_url:
            return True

        parser = await self._fetch_and_parse(robots_url, user_agent)
        self._cache[domain] = (parser, now)

        allowed = parser.can_fetch(user_agent, url)
        if not allowed:
            logger.info(
                "robots.txt disallowed domain=%s ua=%s",
                domain,
                user_agent[:30],
            )
        return allowed

    def invalidate(self, domain: str) -> None:
        """使指定域名的缓存失效（下次 is_allowed 时重新获取）。"""
        self._cache.pop(domain.lower(), None)

    def reset(self) -> None:
        """清空所有缓存（测试辅助）。"""
        self._cache.clear()

    def stats(self) -> dict[str, float]:
        """返回各域名缓存距获取的秒数（调试用）。"""
        now = time.monotonic()
        return {d: now - t for d, (_, t) in self._cache.items()}


robots_checker = RobotsChecker(
    cache_ttl=_ROBOTS_CACHE_TTL_SECONDS,
    fetch_timeout=_ROBOTS_FETCH_TIMEOUT_SECONDS,
)
