"""User-Agent + 代理池轮换。

工程规范：
- UA 池 10+ 个现代浏览器 UA。
- 代理池从环境变量加载，逗号分隔。
- 每次请求随机选择；代理失败自动切换。
"""

from __future__ import annotations

import random
from typing import Optional

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger("proxy")


# 现代浏览器 User-Agent 池（10+ 个）
USER_AGENTS: list[str] = [
    # Chrome on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    # Chrome on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    # Firefox on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    # Firefox on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.2; rv:121.0) Gecko/20100101 Firefox/121.0",
    # Safari on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    # Edge on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    # Chrome on Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]


def get_random_user_agent() -> str:
    """返回随机 User-Agent。"""
    return random.choice(USER_AGENTS)


def get_proxy_pool() -> list[str]:
    """返回代理池列表（来自 settings.PROXY_LIST）。"""
    return settings.proxies


def get_random_proxy(exclude: Optional[list[str]] = None) -> Optional[str]:
    """从代理池中随机选一个代理。

    Args:
        exclude: 已知失败的代理列表，避免重复选择。

    Returns:
        代理 URL 字符串；若代理池为空或全部被排除，返回 None。
    """
    pool = get_proxy_pool()
    if not pool:
        return None

    exclude_set = set(exclude or [])
    candidates = [p for p in pool if p not in exclude_set]
    if not candidates:
        # 全部失败，重置并重试一次
        logger.warning("所有代理均已失败，重新从完整池中选取")
        candidates = pool

    return random.choice(candidates)


def report_proxy_failure(proxy: str, failed: list[str]) -> None:
    """记录失败代理，加入排除列表。"""
    if proxy and proxy not in failed:
        failed.append(proxy)
        logger.warning("代理失败已排除: %s（已失败 %d 个）", proxy, len(failed))
