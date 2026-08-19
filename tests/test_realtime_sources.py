"""realtime_sources（SourceAdapter 接口层）离线测试 —— D1 接口冻结验收。

全部离线：FakeAdapter 注入，不发任何网络请求。
覆盖：结果数据类契约 / 协议满足 / 并发编排与降级 / 基类合规包装
（robots 先行、403 熔断、超时、空结果、异常降级）/ 注册表解析。
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.realtime_sources import (
    STATUS_BLOCKED_403,
    STATUS_EMPTY,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_ROBOTS_DENIED,
    STATUS_TIMEOUT,
    BaseSourceAdapter,
    SourceAdapter,
    SourceBlockedError,
    SourceFetchResult,
    collect_realtime,
    resolve_adapters,
)


class FakeAdapter:
    """满足 SourceAdapter 协议的假适配器。"""

    def __init__(self, source: str, payloads: list[dict] | None = None,
                 raise_exc: Exception | None = None, delay: float = 0.0):
        self.source = source
        self.display_name = f"假源-{source}"
        self.domain = f"{source}.example.gov.cn"
        self._payloads = payloads or []
        self._raise = raise_exc
        self._delay = delay
        self.calls = 0

    async def fetch_payloads(self, limit: int = 20, robots_checker=None) -> SourceFetchResult:
        self.calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._raise is not None:
            raise self._raise
        return SourceFetchResult(
            source=self.source, ok=True, status=STATUS_OK,
            fetched=len(self._payloads), payloads=list(self._payloads),
        )


class _DenyRobots:
    async def is_allowed(self, url: str, user_agent: str = "*") -> bool:
        return False


class _AllowRobots:
    async def is_allowed(self, url: str, user_agent: str = "*") -> bool:
        return True


class StubAdapter(BaseSourceAdapter):
    """可控行为的基类子类。"""

    def __init__(self, payloads=None, raise_exc=None, delay=0.0):
        self.source = "fakeprov"
        self.display_name = "假省站"
        self.domain = "fake.example.gov.cn"
        self.robots_entry_url = "https://fake.example.gov.cn/list"
        self._payloads = payloads or []
        self._raise = raise_exc
        self._delay = delay
        self.build_calls = 0

    async def _fetch_and_build(self, limit: int) -> list[dict]:
        self.build_calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._raise is not None:
            raise self._raise
        return list(self._payloads)


# ---- 数据类与协议契约 ----

def test_fetch_result_defaults():
    r = SourceFetchResult(source="x")
    assert r.ok is False
    assert r.status == STATUS_ERROR
    assert r.fetched == 0
    assert r.payloads == []
    assert r.error is None
    assert r.elapsed_ms == 0


def test_fake_adapter_satisfies_protocol():
    assert isinstance(FakeAdapter("a"), SourceAdapter)


# ---- collect_realtime 编排 ----

@pytest.mark.asyncio
async def test_collect_realtime_happy_path():
    p1 = [{"project_name": "甲项目", "source_url": "https://a/1"}]
    p2 = [{"project_name": "乙项目", "source_url": "https://b/1"},
          {"project_name": "丙项目", "source_url": "https://b/2"}]
    out = await collect_realtime([FakeAdapter("s1", p1), FakeAdapter("s2", p2)])
    assert out["total_payloads"] == 3
    assert out["ok_count"] == 2
    assert [r.source for r in out["results"]] == ["s1", "s2"]
    assert all(r.ok for r in out["results"])


@pytest.mark.asyncio
async def test_collect_realtime_single_source_failure_degrades():
    good = FakeAdapter("good", [{"project_name": "x"}])
    bad = FakeAdapter("bad", raise_exc=RuntimeError("boom"))
    out = await collect_realtime([good, bad])
    assert out["ok_count"] == 1
    assert out["total_payloads"] == 1
    failed = next(r for r in out["results"] if r.source == "bad")
    assert failed.ok is False and failed.status == STATUS_ERROR
    assert "boom" in (failed.error or "")


@pytest.mark.asyncio
async def test_collect_realtime_concurrency_capped():
    """Semaphore(2) 下 3 个慢源：同时在飞的不超过 2。"""
    inflight = 0
    peak = 0

    class Slow(FakeAdapter):
        async def fetch_payloads(self, limit: int = 20, robots_checker=None) -> SourceFetchResult:
            nonlocal inflight, peak
            inflight += 1
            peak = max(peak, inflight)
            await asyncio.sleep(0.05)
            inflight -= 1
            return await super().fetch_payloads(limit, robots_checker)

    adapters = [Slow(f"s{i}") for i in range(3)]
    out = await collect_realtime(adapters, max_concurrent=2)
    assert out["ok_count"] == 3
    assert peak <= 2


@pytest.mark.asyncio
async def test_collect_realtime_passes_robots_checker():
    """D4：编排器的 robots_checker 透传到每个适配器（robots 拒绝时零抓取）。"""
    adapters = [StubAdapter(payloads=[{"x": 1}])]
    out = await collect_realtime(adapters, robots_checker=_DenyRobots())
    assert out["ok_count"] == 0
    r = out["results"][0]
    assert r.status == STATUS_ROBOTS_DENIED and r.ok is False
    assert adapters[0].build_calls == 0


# ---- BaseSourceAdapter 合规包装 ----

@pytest.mark.asyncio
async def test_robots_denied_blocks_before_fetch():
    a = StubAdapter(payloads=[{"x": 1}])
    r = await a.fetch_payloads(robots_checker=_DenyRobots())
    assert r.status == STATUS_ROBOTS_DENIED and r.ok is False
    assert a.build_calls == 0  # 未发任何抓取请求


@pytest.mark.asyncio
async def test_robots_allowed_proceeds():
    a = StubAdapter(payloads=[{"x": 1}])
    r = await a.fetch_payloads(robots_checker=_AllowRobots())
    assert r.status == STATUS_OK and r.fetched == 1
    assert a.build_calls == 1


@pytest.mark.asyncio
async def test_403_sets_circuit_breaker():
    a = StubAdapter(raise_exc=SourceBlockedError("403"))
    r1 = await a.fetch_payloads()
    assert r1.status == STATUS_BLOCKED_403 and a.build_calls == 1
    # 熔断后第二次调用不再发请求
    r2 = await a.fetch_payloads()
    assert r2.status == STATUS_BLOCKED_403 and a.build_calls == 1


@pytest.mark.asyncio
async def test_timeout_normalized():
    a = StubAdapter(delay=0.2)
    a.timeout_seconds = 0.05
    r = await a.fetch_payloads()
    assert r.status == STATUS_TIMEOUT and r.ok is False
    assert "超时" in (r.error or "")


@pytest.mark.asyncio
async def test_empty_payloads_status_empty_but_ok():
    a = StubAdapter(payloads=[])
    r = await a.fetch_payloads()
    assert r.status == STATUS_EMPTY and r.ok is True and r.fetched == 0


@pytest.mark.asyncio
async def test_generic_exception_normalized_to_error():
    a = StubAdapter(raise_exc=ValueError("解析失败"))
    r = await a.fetch_payloads()
    assert r.status == STATUS_ERROR and r.ok is False
    assert "解析失败" in (r.error or "")


@pytest.mark.asyncio
async def test_empty_message_exception_falls_back_to_class_name():
    """D4 真机守卫：异常 str() 为空（如 httpx.ReadTimeout）时
    error 兜底为类名，前端不会看到空白错误。"""

    class _SilentError(Exception):
        pass

    a = StubAdapter(raise_exc=_SilentError())
    r = await a.fetch_payloads()
    assert r.status == STATUS_ERROR and r.ok is False
    assert r.error == "_SilentError"


@pytest.mark.asyncio
async def test_elapsed_ms_recorded():
    a = StubAdapter(payloads=[{"x": 1}], delay=0.02)
    r = await a.fetch_payloads()
    assert r.elapsed_ms >= 10


# ---- 注册表解析 ----

def test_resolve_adapters_unknown_name_skipped():
    assert resolve_adapters(["not_registered_source"]) == []


def test_resolve_adapters_default_sources_registered():
    """D3 四源冻结：默认源均可实例化且 source 名一致。"""
    from app.services.realtime_sources import DEFAULT_SOURCES
    adapters = resolve_adapters()
    assert [a.source for a in adapters] == list(DEFAULT_SOURCES)
    assert len(adapters) == 4
