# -*- coding: utf-8 -*-
"""ccgp 加固层守卫测试（退避重试 + 熔断冷却 + 断点续采 + 降级编排）。

覆盖：错误归类、退避序列与抖动、熔断状态机（指数冷却/封顶/恢复）、
断点持久化（落盘/重载/坏文件）、collector 重试与 403 零重试、
降级兜底采集编排。全部离线，零网络。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from app.scheduler import ccgp_resilience as res  # noqa: E402


# ---------------------------------------------------------------- 错误归类

def test_classify_blocked():
    assert res.classify_failure("抓取失败: HTTP 403 Forbidden, 停止抓取") == "blocked"
    assert res.classify_failure("被 WAF 拦截") == "blocked"


def test_classify_fatal_beats_blocked():
    # robots 拒绝消息含 forbidden 字样，也必须判 fatal（重试无意义）
    assert res.classify_failure("robots.txt 禁止采集该 URL") == "fatal"
    assert res.classify_failure("URL 不安全: private ip") == "fatal"


def test_classify_retryable():
    assert res.classify_failure("抓取失败: Timeout 30000ms exceeded") == "retryable"
    assert res.classify_failure("net::ERR_CONNECTION_RESET") == "retryable"
    assert res.classify_failure("") == "retryable"


# ---------------------------------------------------------------- 退避序列

def test_backoff_delays_exponential():
    p = res.RetryPolicy()
    assert res.backoff_delays(p) == [2.0, 4.0]  # 3 次尝试 → 2 个间隔


def test_backoff_delays_cap():
    p = res.RetryPolicy(max_attempts=6, base_delay=10.0, factor=2.0, max_delay=30.0)
    assert res.backoff_delays(p) == [10.0, 20.0, 30.0, 30.0, 30.0]


def test_backoff_delays_single_attempt():
    assert res.backoff_delays(res.RetryPolicy(max_attempts=1)) == []


def test_add_jitter_bounds():
    for _ in range(50):
        d = res.add_jitter(10.0, 0.25)
        assert 7.5 <= d <= 12.5
    assert res.add_jitter(10.0, 0.0) == 10.0


# ---------------------------------------------------------------- 熔断状态机

def test_breaker_below_threshold_no_cooldown():
    b = res.SourceBreaker(failure_threshold=2)
    b.record_failure("ccgp")
    assert b.cooldown_seconds("ccgp") == 0.0


def test_breaker_threshold_opens_cooldown():
    b = res.SourceBreaker(failure_threshold=2, base_cooldown=60.0)
    b.record_failure("ccgp")
    b.record_failure("ccgp")
    remain = b.cooldown_seconds("ccgp")
    assert 55.0 <= remain <= 60.0


def test_breaker_cooldown_doubles_and_caps():
    b = res.SourceBreaker(failure_threshold=1, base_cooldown=60.0, max_cooldown=300.0)
    b.record_failure("ccgp")            # 60
    b.record_failure("ccgp")            # 120
    b.record_failure("ccgp")            # 240
    b.record_failure("ccgp")            # 480 → 封顶 300
    remain = b.cooldown_seconds("ccgp")
    assert remain <= 300.0


def test_breaker_success_resets():
    b = res.SourceBreaker(failure_threshold=1, base_cooldown=60.0)
    b.record_failure("ccgp")
    assert b.cooldown_seconds("ccgp") > 0
    b.record_success("ccgp")
    assert b.cooldown_seconds("ccgp") == 0.0
    snap = b.snapshot()
    assert snap["ccgp"]["consecutive_failures"] == 0
    assert snap["ccgp"]["last_success"]


def test_breaker_expired_cooldown_is_zero():
    import time
    b = res.SourceBreaker(failure_threshold=1, base_cooldown=60.0)
    b.record_failure("ccgp")
    assert b.cooldown_seconds("ccgp", now=time.time() + 3600) == 0.0


# ---------------------------------------------------------------- 断点续采

def test_checkpoint_persist_and_reload(tmp_path):
    ckpt = tmp_path / "ckpt.json"
    b1 = res.SourceBreaker(failure_threshold=1, base_cooldown=600.0,
                           checkpoint_path=ckpt)
    b1.record_failure("ccgp", error="抓取失败: Timeout")
    b1.record_success("hubei")
    assert ckpt.exists()

    b2 = res.SourceBreaker(failure_threshold=1, base_cooldown=600.0,
                           checkpoint_path=ckpt)
    assert b2.cooldown_seconds("ccgp") > 0          # 熔断跨重启存活
    assert b2.snapshot()["hubei"]["consecutive_failures"] == 0


def test_checkpoint_corrupt_file_fresh_start(tmp_path):
    ckpt = tmp_path / "ckpt.json"
    ckpt.write_text("{不是合法 JSON", encoding="utf-8")
    b = res.SourceBreaker(checkpoint_path=ckpt)
    assert b.snapshot() == {}
    b.record_failure("ccgp")                        # 坏文件不阻碍后续写入
    assert json.loads(ckpt.read_text(encoding="utf-8"))["version"] == 1


# ---------------------------------------------------------------- 降级编排

@pytest.mark.asyncio
async def test_run_fallback_collect(monkeypatch):
    import app.services.realtime_sources as rs

    class _R:
        def __init__(self, source, ok, payloads, status="ok", error=None):
            self.source = source
            self.ok = ok
            self.payloads = payloads
            self.status = status
            self.error = error

    async def _fake_collect(adapters, limit=3, max_concurrent=3, robots_checker=None):
        return {
            "results": [
                _R("hubei", True, [{"source_url": "u1"}]),
                _R("henan", False, [], status="blocked_403", error="403"),
            ],
            "total_payloads": 1,
            "ok_count": 1,
        }

    monkeypatch.setattr(rs, "collect_realtime", _fake_collect)
    monkeypatch.setattr(rs, "resolve_adapters", lambda names=None: [])
    out = await res.run_fallback_collect(limit=1)
    assert out == [{"source_url": "u1"}]


# ---------------------------------------------------------------- collector 集成

async def _instant_sleep(_seconds):
    pass


class _FakeScraper:
    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    async def scrape(self, request):
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _install(monkeypatch, outcomes):
    import app.core.scraper as scraper_mod
    from app.llm.schemas import ParsedFilters
    from app.scheduler import collector as cc

    fake = _FakeScraper(outcomes)
    monkeypatch.setattr(scraper_mod, "scraper", fake)
    monkeypatch.setattr(cc, "_retry_sleep", _instant_sleep)
    monkeypatch.setattr(res, "get_breaker",
                        lambda: res.SourceBreaker())  # 每个用例全新熔断器
    return cc, fake, ParsedFilters(raw_query="教育")


@pytest.mark.asyncio
async def test_collector_retry_then_success(monkeypatch):
    from app.core.scraper import ScrapeError

    cc, fake, filters = _install(monkeypatch, [
        ScrapeError("抓取失败: Timeout 30000ms exceeded"),
        {"url": "x", "data": []},
    ])
    out = await cc._scrape_one_platform("ccgp", filters, asyncio.Semaphore(1))
    assert "result" in out and out["result"]["data"] == []
    assert fake.calls == 2  # 超时一次后重试成功


@pytest.mark.asyncio
async def test_collector_403_no_retry(monkeypatch):
    from app.core.scraper import ScrapeError

    cc, fake, filters = _install(monkeypatch, [
        ScrapeError("抓取失败: HTTP 403 Forbidden, 停止抓取: https://search.ccgp.gov.cn"),
        {"url": "x", "data": []},
    ])
    out = await cc._scrape_one_platform("ccgp", filters, asyncio.Semaphore(1))
    assert "error" in out
    assert fake.calls == 1  # 403 零重试（不得加重封禁）


@pytest.mark.asyncio
async def test_collector_retries_exhausted_records_failure(monkeypatch):
    from app.core.scraper import ScrapeError

    breaker = res.SourceBreaker(failure_threshold=1, base_cooldown=60.0)
    cc, fake, filters = _install(monkeypatch, [
        ScrapeError("抓取失败: Timeout"),
        ScrapeError("抓取失败: Timeout"),
        ScrapeError("抓取失败: Timeout"),
    ])
    monkeypatch.setattr(res, "get_breaker", lambda: breaker)
    out = await cc._scrape_one_platform("ccgp", filters, asyncio.Semaphore(1))
    assert "error" in out
    assert fake.calls == 3  # 默认 3 次尝试用尽
    assert breaker.cooldown_seconds("ccgp") > 0  # 熔断生效


@pytest.mark.asyncio
async def test_collector_breaker_cooldown_zero_request(monkeypatch):
    breaker = res.SourceBreaker(failure_threshold=1, base_cooldown=60.0)
    breaker.record_failure("ccgp")  # 触发冷却
    cc, fake, filters = _install(monkeypatch, [])
    monkeypatch.setattr(res, "get_breaker", lambda: breaker)
    out = await cc._scrape_one_platform("ccgp", filters, asyncio.Semaphore(1))
    assert "error" in out and "熔断冷却" in out["error"]
    assert fake.calls == 0  # 冷却期内零请求


@pytest.mark.asyncio
async def test_collector_fatal_not_recorded(monkeypatch):
    from app.core.scraper import ScrapeError

    breaker = res.SourceBreaker(failure_threshold=1, base_cooldown=60.0)
    cc, fake, filters = _install(monkeypatch, [
        ScrapeError("robots.txt 禁止采集该 URL: https://search.ccgp.gov.cn"),
    ])
    monkeypatch.setattr(res, "get_breaker", lambda: breaker)
    out = await cc._scrape_one_platform("ccgp", filters, asyncio.Semaphore(1))
    assert "error" in out
    assert fake.calls == 1
    # robots 拒绝是政策不是故障：不计入熔断
    assert breaker.cooldown_seconds("ccgp") == 0.0
