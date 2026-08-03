"""来源白名单与下架处理机制（v4.1 §5.2 合规原则）。

职责：
- 维护允许采集的来源平台/域名清单（默认覆盖 ccgp/chinabidding/ggzy/qianlima）
- 提供 is_allowed(url) 校验：未在白名单或已下架的来源立即拒绝采集
- 支持运行时下架（decommission）某来源，记录下架原因与时间
- 支持重新启用（recommission）
- 支持列出全部来源及状态

设计原则：
- 进程内单例（与 rate_limiter / robots_checker 风格一致）
- 默认白名单覆盖 v4.1 §5.3 已实现的官方/合规平台
- 与 §13.3 数据删除联动：下架仅阻止新采集，不删除历史数据
- 线程安全：asyncio.Lock 保护写操作
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse


def _utc_now_iso() -> str:
    """UTC ISO8601 时间戳。"""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class WhitelistEntry:
    """白名单条目。"""

    domain: str
    platform_name: str
    platform_type: str = "commercial"  # government / authorized / commercial / unknown
    status: str = "active"  # active / decommissioned
    decommissioned_reason: str | None = None
    decommissioned_at: str | None = None
    added_at: str = field(default_factory=_utc_now_iso)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "platform_name": self.platform_name,
            "platform_type": self.platform_type,
            "status": self.status,
            "decommissioned_reason": self.decommissioned_reason,
            "decommissioned_at": self.decommissioned_at,
            "added_at": self.added_at,
            "notes": self.notes,
        }


# v4.1 §5.3 默认白名单：覆盖已实现的官方与商业平台
# - government: ccgp / ggzy（政府/公共资源交易平台）
# - commercial: chinabidding / qianlima（商业平台，需用户账号登录态采集）
_DEFAULT_WHITELIST: list[WhitelistEntry] = [
    WhitelistEntry(
        domain="ccgp.gov.cn",
        platform_name="中国政府采购网",
        platform_type="government",
        notes="v4.1 §5.3 默认白名单：官方原始来源",
    ),
    WhitelistEntry(
        domain="ggzy.gov.cn",
        platform_name="公共资源交易网",
        platform_type="government",
        notes="v4.1 §5.3 默认白名单：公共资源交易平台",
    ),
    WhitelistEntry(
        domain="chinabidding.cn",
        platform_name="中国招标投标网",
        platform_type="commercial",
        notes="v4.1 §5.3 默认白名单：商业转载/索引平台",
    ),
    WhitelistEntry(
        domain="qianlima.com",
        platform_name="千里马招标网",
        platform_type="commercial",
        notes="v4.1 §5.3 默认白名单：登录态采集商业平台",
    ),
]


class SourceWhitelist:
    """来源白名单管理器。

    线程安全：所有写操作使用 asyncio.Lock 保护。
    """

    def __init__(self) -> None:
        self._entries: dict[str, WhitelistEntry] = {}
        self._lock = asyncio.Lock()
        self._load_defaults()

    def _load_defaults(self) -> None:
        """加载默认白名单（不持锁，仅构造时调用）。"""
        for entry in _DEFAULT_WHITELIST:
            # 复制一份避免外部修改默认列表
            self._entries[entry.domain] = WhitelistEntry(
                domain=entry.domain,
                platform_name=entry.platform_name,
                platform_type=entry.platform_type,
                status=entry.status,
                decommissioned_reason=entry.decommissioned_reason,
                decommissioned_at=entry.decommissioned_at,
                added_at=entry.added_at,
                notes=entry.notes,
            )

    @staticmethod
    def _normalize_domain(url_or_domain: str) -> str:
        """从 URL 或裸域名提取小写的主域名。

        - "https://www.ccgp.gov.cn/a/b" -> "ccgp.gov.cn"
        - "www.ccgp.gov.cn" -> "ccgp.gov.cn"
        - "CCGP.GOV.CN" -> "ccgp.gov.cn"

        匹配策略：检查 url 的 hostname 是否 endswith 白名单中的某个 domain。
        本函数只做归一化，不做白名单匹配。
        """
        s = (url_or_domain or "").strip().lower()
        if not s:
            return ""
        # 如果带 scheme，用 urlparse
        if "://" in s:
            hostname = urlparse(s).hostname or ""
        else:
            # 裸域名：去掉 path 部分
            hostname = s.split("/")[0]
        # 去掉端口
        hostname = hostname.split(":")[0]
        # 去掉前导 www. / m. 等常见子域前缀，便于跨子域匹配
        # 但保留 ccgp.gov.cn 这种结构（www.ccgp.gov.cn → ccgp.gov.cn）
        for prefix in ("www.", "m.", "wap.", "mobile."):
            if hostname.startswith(prefix):
                hostname = hostname[len(prefix):]
                break
        return hostname

    def _match_domain(self, hostname: str) -> WhitelistEntry | None:
        """查找 hostname 对应的白名单条目（支持子域匹配）。

        例如 hostname="www.ccgp.gov.cn" 匹配 domain="ccgp.gov.cn"。
        """
        if not hostname:
            return None
        # 精确匹配
        if hostname in self._entries:
            return self._entries[hostname]
        # 子域匹配：hostname 以 ".domain" 结尾，或 hostname == domain
        for domain, entry in self._entries.items():
            if hostname == domain or hostname.endswith("." + domain):
                return entry
        return None

    def is_allowed(self, url_or_domain: str) -> tuple[bool, str]:
        """校验 URL/域名是否允许采集。

        Returns:
            (allowed, reason)
            - (True, "") 表示允许
            - (False, reason) 表示拒绝，reason 给出拒绝原因
        """
        hostname = self._normalize_domain(url_or_domain)
        if not hostname:
            return False, "URL 解析失败，无法识别域名"
        entry = self._match_domain(hostname)
        if entry is None:
            return False, f"域名 {hostname} 不在白名单中"
        if entry.status == "decommissioned":
            return False, (
                f"域名 {hostname} 已下架：{entry.decommissioned_reason or '未指定原因'}"
            )
        return True, ""

    async def add_source(
        self,
        domain: str,
        platform_name: str,
        platform_type: str = "commercial",
        notes: str = "",
    ) -> WhitelistEntry:
        """新增来源到白名单。若已存在则更新 platform_name/notes。"""
        normalized = self._normalize_domain(domain)
        if not normalized:
            raise ValueError(f"非法域名: {domain!r}")
        if platform_type not in {"government", "authorized", "commercial", "unknown"}:
            raise ValueError(f"非法 platform_type: {platform_type!r}")
        async with self._lock:
            existing = self._entries.get(normalized)
            if existing is not None:
                existing.platform_name = platform_name
                existing.platform_type = platform_type
                if notes:
                    existing.notes = notes
                return existing
            entry = WhitelistEntry(
                domain=normalized,
                platform_name=platform_name,
                platform_type=platform_type,
                notes=notes,
            )
            self._entries[normalized] = entry
            return entry

    async def decommission(
        self,
        domain: str,
        reason: str,
    ) -> WhitelistEntry:
        """下架某来源。下架后该域名所有新采集立即停止。

        与 §13.3 数据删除的关系：
        - 下架仅阻止新采集，不删除历史数据
        - 如需删除历史数据，调用 DataDeletionService.delete_by_source_platform
        """
        if not reason or not reason.strip():
            raise ValueError("下架原因必填，不能为空")
        normalized = self._normalize_domain(domain)
        async with self._lock:
            entry = self._match_domain(normalized)
            if entry is None:
                raise KeyError(f"域名 {normalized} 不在白名单中，无法下架")
            entry.status = "decommissioned"
            entry.decommissioned_reason = reason.strip()
            entry.decommissioned_at = _utc_now_iso()
            return entry

    async def recommission(self, domain: str) -> WhitelistEntry:
        """重新启用已下架的来源。"""
        normalized = self._normalize_domain(domain)
        async with self._lock:
            entry = self._match_domain(normalized)
            if entry is None:
                raise KeyError(f"域名 {normalized} 不在白名单中")
            if entry.status != "decommissioned":
                # 已是 active，幂等返回
                return entry
            entry.status = "active"
            entry.decommissioned_reason = None
            entry.decommissioned_at = None
            return entry

    def list_sources(self, status: str | None = None) -> list[dict[str, Any]]:
        """列出全部白名单来源。

        Args:
            status: 可选过滤状态（active / decommissioned），None 表示全部
        """
        result = []
        for entry in self._entries.values():
            if status is not None and entry.status != status:
                continue
            result.append(entry.to_dict())
        # 按 domain 字母序返回，便于人工查阅
        result.sort(key=lambda x: x["domain"])
        return result

    def get_entry(self, domain: str) -> WhitelistEntry | None:
        """获取单条白名单条目（不做状态过滤）。"""
        normalized = self._normalize_domain(domain)
        return self._match_domain(normalized)

    def reset(self) -> None:
        """重置为默认白名单。仅测试使用。"""
        self._entries.clear()
        self._load_defaults()


# 模块级单例（与 rate_limiter / robots_checker 风格一致）
source_whitelist = SourceWhitelist()