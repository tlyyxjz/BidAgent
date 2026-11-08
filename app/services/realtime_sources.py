"""实时数据源适配层（SourceAdapter）—— D1 接口冻结版（2026-08-19）。

职责：把分站采集器（scripts/collect_*.py）接入现有实时链路。
链路：adapter 抓取 → 复用脚本 build_payload 纯函数 → 调用方把
payloads 交给 ingest_scrape_result 入库（本模块不碰数据库，便于离线测试）。

冻结契约（此后不得变更签名；确需变更须更新本节冻结记录）：
- SourceFetchResult 字段集与 status 取值集合；
- SourceAdapter 协议（source/display_name/domain/fetch_payloads）；
- BaseSourceAdapter 合规包装顺序：robots → 熔断 → 抓取（限流由
  domain_rate_limiter 在 _fetch_and_build 内的脚本 _fetch 中执行）；
- collect_realtime(adapters, limit, max_concurrent) 返回结构。

合规红线（硬编码在基类，不是注释承诺）：
1. robots 先行：is_allowed 为 False 直接返回 robots_denied，不发任何请求；
2. 域内限流：复用 app.core.rate_limiter.domain_rate_limiter（默认 8s/域）；
3. 403 即停：单源被拦置 blocked_403 熔断，本次会话不再重试该源；
4. 单源失败降级：collect_realtime 中任何一源异常不影响其他源。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.utils.logger import get_logger

logger = get_logger("services.realtime_sources")

# status 取值集合（冻结）
STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_ROBOTS_DENIED = "robots_denied"
STATUS_BLOCKED_403 = "blocked_403"
STATUS_TIMEOUT = "timeout"
STATUS_ERROR = "error"

UA = "BidAgent/1.0 (+educational-research; compliant crawler)"


@dataclass
class SourceFetchResult:
    """单个数据源一次实时抓取的结果（冻结字段集）。"""

    source: str
    ok: bool = False
    status: str = STATUS_ERROR
    fetched: int = 0            # 列表条目数
    payloads: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    elapsed_ms: int = 0


@runtime_checkable
class SourceAdapter(Protocol):
    """数据源适配器协议（D1 冻结）。"""

    source: str          # 平台标识，与 tenders.source_platform 对齐
    display_name: str    # 演示页展示名
    domain: str          # 主域名（限流/robots 粒度）

    async def fetch_payloads(
        self, limit: int = 20
    ) -> SourceFetchResult:
        """抓取列表 + 详情并产出入库 payload（build_payload 契约）。"""
        ...


class SourceBlockedError(Exception):
    """源站返回 403（反爬拦截），触发该源本次会话熔断。"""


class BaseSourceAdapter:
    """适配器基类：合规包装 + 结果归一化。

    子类只需实现 _fetch_and_build（抓取并返回 build_payload 输出列表），
    robots 检查、403 熔断、超时归一、计时全部由基类统一处理——
    保证任何新接入源不可能绕过合规纪律。
    """

    source: str = ""
    display_name: str = ""
    domain: str = ""
    robots_entry_url: str = ""   # robots 检查用入口 URL（默认 list_url）
    timeout_seconds: float = 45.0
    _blocked: bool = False       # 403 熔断标志（实例级）

    async def fetch_payloads(
        self, limit: int = 20, robots_checker: Any = None
    ) -> SourceFetchResult:
        start = time.monotonic()

        def _result(status: str, ok: bool, fetched: int = 0,
                    payloads: list[dict[str, Any]] | None = None,
                    error: str | None = None) -> SourceFetchResult:
            return SourceFetchResult(
                source=self.source, ok=ok, status=status, fetched=fetched,
                payloads=payloads or [], error=error,
                elapsed_ms=int((time.monotonic() - start) * 1000),
            )

        # 合规 1：robots 先行（禁止即停，不发任何请求）
        entry_url = self.robots_entry_url or getattr(self, "list_url", "")
        if robots_checker is not None and entry_url:
            allowed = await robots_checker.is_allowed(entry_url, UA)
            if not allowed:
                logger.warning("robots denied source=%s url=%s",
                               self.source, entry_url)
                return _result(STATUS_ROBOTS_DENIED, ok=False,
                               error="robots.txt 禁止采集")

        # 合规 2：403 熔断（本次会话内被拦过的源不再请求）
        if self._blocked:
            return _result(STATUS_BLOCKED_403, ok=False,
                           error="该源本次会话已被 403 熔断")

        try:
            payloads = await asyncio.wait_for(
                self._fetch_and_build(limit), timeout=self.timeout_seconds
            )
        except SourceBlockedError as exc:
            self._blocked = True
            logger.warning("403 blocked source=%s", self.source)
            return _result(STATUS_BLOCKED_403, ok=False, error=str(exc))
        except asyncio.TimeoutError:
            return _result(STATUS_TIMEOUT, ok=False,
                           error=f"超时（>{self.timeout_seconds:.0f}s）")
        except Exception as exc:  # noqa: BLE001 - 单源失败必须降级
            logger.exception("realtime source failed source=%s", self.source)
            # D4 真机：httpx.ReadTimeout 等异常 str() 为空，兜底用类名
            return _result(STATUS_ERROR, ok=False,
                           error=str(exc) or exc.__class__.__name__)

        if not payloads:
            return _result(STATUS_EMPTY, ok=True)
        return _result(STATUS_OK, ok=True,
                       fetched=len(payloads), payloads=payloads)

    async def _fetch_and_build(self, limit: int) -> list[dict[str, Any]]:
        """子类实现：抓取列表+详情，返回 build_payload 输出的 dict 列表。

        要求：网络请求走脚本内 _fetch（已含 domain_rate_limiter 限流）；
        遇 403 抛 SourceBlockedError。
        """
        raise NotImplementedError


async def collect_realtime(
    adapters: list[SourceAdapter],
    limit: int = 20,
    max_concurrent: int = 3,
    robots_checker: Any = None,
) -> dict[str, Any]:
    """并发运行多个适配器（单源失败降级，不影响其他源）。

    与 scheduler/collector.py 同一纪律：并发抓取、结果各自独立；
    入库由调用方串行执行（避免 SQLite 并发写锁）。

    D4：robots_checker 透传给每个适配器的 fetch_payloads
    （robots 先行检查在适配器基类内完成，None 时跳过）。

    Returns:
        {"results": [SourceFetchResult...], "total_payloads": int,
         "ok_count": int}
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _run_one(adapter: SourceAdapter) -> SourceFetchResult:
        async with semaphore:
            try:
                return await adapter.fetch_payloads(
                    limit=limit, robots_checker=robots_checker
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("adapter crashed source=%s",
                                 getattr(adapter, "source", "?"))
                return SourceFetchResult(
                    source=getattr(adapter, "source", "unknown"),
                    ok=False, status=STATUS_ERROR,
                    error=str(exc) or exc.__class__.__name__,
                )

    results = await asyncio.gather(*(_run_one(a) for a in adapters))
    results = list(results)
    return {
        "results": results,
        "total_payloads": sum(len(r.payloads) for r in results),
        "ok_count": sum(1 for r in results if r.ok),
    }


# ---------------------------------------------------------------------------
# 适配器注册表（D3 四源全部接入，冻结）
# source -> (模块路径, 类名)；惰性导入，避免离线测试引入网络依赖。
# ---------------------------------------------------------------------------
_ADAPTER_REGISTRY: dict[str, tuple[str, str]] = {
    "hubei": ("app.services.realtime_adapters", "HubeiAdapter"),
    "jiangsu": ("app.services.realtime_adapters", "JiangsuAdapter"),
    "yunnan": ("app.services.realtime_adapters", "YunnanAdapter"),
    "shandong": ("app.services.realtime_adapters", "ShandongAdapter"),
    "tianjin": ("app.services.realtime_adapters", "TianjinAdapter"),
    "qingdao": ("app.services.realtime_adapters", "QingdaoAdapter"),    "henan": ("app.services.realtime_adapters", "HenanAdapter"),
    "zhejiang": ("app.services.realtime_adapters", "ZhejiangAdapter"),
    "guangdong": ("app.services.realtime_adapters", "GuangdongAdapter"),
}

# 四源冻结（D6 实机核对通过：shandong API 搬 :8087/api，colCode=0302，失败自动降级不阻断）
DEFAULT_SOURCES: tuple[str, ...] = ("hubei", "jiangsu", "yunnan", "shandong", "tianjin", "qingdao", "henan", "zhejiang", "guangdong")


def resolve_adapters(names: list[str] | None = None) -> list[SourceAdapter]:
    """按 source 名实例化适配器；未知名字跳过并告警。"""
    import importlib

    wanted = list(names) if names else list(DEFAULT_SOURCES)
    adapters: list[SourceAdapter] = []
    for name in wanted:
        entry = _ADAPTER_REGISTRY.get(name)
        if entry is None:
            logger.warning("unknown realtime source: %s", name)
            continue
        module_path, class_name = entry
        cls = getattr(importlib.import_module(module_path), class_name)
        adapters.append(cls())
    return adapters
