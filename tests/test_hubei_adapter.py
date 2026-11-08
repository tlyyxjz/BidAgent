"""HubeiAdapter 离线测试（D2）：假 httpx 客户端，真解析链，零网络。

覆盖：列表→详情→payload 契约字段 / limit 生效 / 空列表 /
403 熔断贯通（Collect403→SourceBlockedError→blocked_403）/ 抓取失败归一。
"""

from __future__ import annotations

import pytest

from app.core.rate_limiter import domain_rate_limiter
from app.services import realtime_adapters as ra
from app.services.realtime_adapters import HubeiAdapter
from app.services.realtime_sources import (
    STATUS_BLOCKED_403,
    STATUS_EMPTY,
    STATUS_ERROR,
    STATUS_OK,
)

_HEX32 = "ab" * 16
LIST_HTML = (
    '<html><body><li><a href="/notice/123456/notice_' + _HEX32
    + '.html">[公开招标]示例大学设备采购项目中标公告</a>'
    "<span>2026-08-18</span></li></body></html>"
)
DETAIL_URL = f"https://www.ccgp-hubei.gov.cn/notice/123456/notice_{_HEX32}.html"
DETAIL_HTML = (
    "<html><body><h2><span>示例大学设备采购项目中标公告</span></h2>"
    "<p>项目编号：HBZC2026-001</p></body></html>"
)


class _Resp:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


class _FakeClient:
    def __init__(self, mapping: dict[str, int | tuple[int, str]] | None = None,
                 default: int = 404):
        self.mapping = mapping or {}
        self.default = default
        self.requested: list[str] = []

    async def get(self, url: str):
        self.requested.append(url)
        spec = self.mapping.get(url, self.default)
        if isinstance(spec, tuple):
            return _Resp(*spec)
        return _Resp(spec)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeClientFactory:
    def __init__(self, client: _FakeClient):
        self.client = client

    def __call__(self, **kwargs):
        return self.client


@pytest.fixture(autouse=True)
def _zero_interval():
    domain_rate_limiter.set_interval("www.ccgp-hubei.gov.cn", 0)
    yield


def _patch_client(monkeypatch, client: _FakeClient) -> None:
    monkeypatch.setattr(ra.httpx, "AsyncClient", _FakeClientFactory(client))


@pytest.mark.asyncio
async def test_happy_path_builds_payload_contract(monkeypatch):
    client = _FakeClient({
        HubeiAdapter.list_url: (200, LIST_HTML),
        DETAIL_URL: (200, DETAIL_HTML),
    })
    _patch_client(monkeypatch, client)
    payloads = await HubeiAdapter()._fetch_and_build(limit=5)
    assert len(payloads) == 1
    p = payloads[0]
    assert p["source_platform"] == "hubei"
    assert p["source_url"] == DETAIL_URL
    assert p["project_name"] == "示例大学设备采购项目中标公告"
    assert p["bid_number"] == "HBZC2026-001"
    assert p["core_content"]


@pytest.mark.asyncio
async def test_fetch_payloads_full_wrap_ok(monkeypatch):
    client = _FakeClient({
        HubeiAdapter.list_url: (200, LIST_HTML),
        DETAIL_URL: (200, DETAIL_HTML),
    })
    _patch_client(monkeypatch, client)
    r = await HubeiAdapter().fetch_payloads(limit=5)
    assert r.status == STATUS_OK and r.ok is True
    assert r.fetched == 1 and len(r.payloads) == 1
    assert r.elapsed_ms >= 0


@pytest.mark.asyncio
async def test_empty_list_page_yields_empty_status(monkeypatch):
    client = _FakeClient({HubeiAdapter.list_url: (200, "<html>no items</html>")})
    _patch_client(monkeypatch, client)
    r = await HubeiAdapter().fetch_payloads(limit=5)
    assert r.status == STATUS_EMPTY and r.ok is True and r.fetched == 0


@pytest.mark.asyncio
async def test_403_triggers_circuit_breaker(monkeypatch):
    client = _FakeClient(default=403)
    _patch_client(monkeypatch, client)
    a = HubeiAdapter()
    r1 = await a.fetch_payloads(limit=5)
    assert r1.status == STATUS_BLOCKED_403 and r1.ok is False
    # 熔断后不再发起任何请求
    before = len(client.requested)
    r2 = await a.fetch_payloads(limit=5)
    assert r2.status == STATUS_BLOCKED_403
    assert len(client.requested) == before


@pytest.mark.asyncio
async def test_fetch_failure_normalized_to_error(monkeypatch):
    client = _FakeClient(default=500)
    _patch_client(monkeypatch, client)
    r = await HubeiAdapter().fetch_payloads(limit=5)
    assert r.status == STATUS_ERROR and r.ok is False
    assert r.error


_HEX32_B = "cd" * 16
_LIST_MIXED_HTML = (
    '<html><body>'
    '<li><a href="/notice/123456/notice_' + _HEX32
    + '.html">[公开招标]示例大学设备采购项目中标公告</a>'
    '<span>2026-08-18</span></li>'
    '<li><a href="/notice/222222/notice_' + _HEX32_B
    + '.html">[公开招标]示例医院物业资格预审公告</a>'
    '<span>2026-08-18</span></li>'
    '</body></html>'
)


@pytest.mark.asyncio
async def test_non_award_notices_filtered_before_fetch(monkeypatch):
    """D4 真机修复守卫：列表混入非中标类 → 只保留中标/成交/结果类，
    且不为被过滤项发起详情请求。"""
    client = _FakeClient({
        HubeiAdapter.list_url: (200, _LIST_MIXED_HTML),
        DETAIL_URL: (200, DETAIL_HTML),
    })
    _patch_client(monkeypatch, client)
    payloads = await HubeiAdapter()._fetch_and_build(limit=5)
    assert len(payloads) == 1
    assert payloads[0]["project_name"] == "示例大学设备采购项目中标公告"
    # 被过滤项的详情页未请求：只请求了列表页 + 中标项详情
    assert len(client.requested) == 2
    assert client.requested[1] == DETAIL_URL
