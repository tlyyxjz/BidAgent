"""YunnanAdapter 离线测试（D3）：假 httpx 客户端，真解析链，零网络。

覆盖：POST 列表→详情→payload 契约 / 中标类标题过滤 / 脏记录过滤 /
单条详情 TransportError 降级跳过 / 403 熔断 / 非 JSON 列表归一 empty。
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.rate_limiter import domain_rate_limiter
from app.services import realtime_adapters as ra
from app.services.realtime_adapters import YunnanAdapter
from app.services.realtime_sources import (
    STATUS_BLOCKED_403,
    STATUS_EMPTY,
    STATUS_OK,
)

LIST_URL = "http://www.ccgp-yunnan.gov.cn" + "/api/firstpage/firstpage.gghtlist.svc"
LIST_JSON = json.dumps({"code": 200, "data": [
    {"BULLETIN_ID": "YNZC2026-001", "BULLETINTITLE": "示例大学设备采购项目中标公告",
     "FINISHDAY": "2026-08-18"},
    {"BULLETIN_ID": "YNZC2026-002", "BULLETINTITLE": "某服务招标公告",
     "FINISHDAY": "2026-08-18"},          # 非中标类：应被过滤
    {"BULLETIN_ID": "sddfucggg", "BULLETINTITLE": "脏记录",
     "FINISHDAY": "2026-08-18"},          # 脏 ID：脚本层已过滤
    {"BULLETIN_ID": "YNZC2026-003", "BULLETINTITLE": "占位记录",
     "FINISHDAY": "/"},                   # 无效日期：脚本层已过滤
]}, ensure_ascii=False)
DETAIL_URL = ("http://www.ccgp-yunnan.gov.cn/ggInfo.html"
              "?bulletin_id=YNZC2026-001")
DETAIL_HTML = ("<html><head><title>示例大学设备采购项目中标公告</title></head>"
               "<body><p>项目编号：YNZC2026-G1-001</p></body></html>")


class _Resp:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


class _FakeClient:
    def __init__(self, get_map=None, post_map=None, default: int = 404,
                 get_raise: Exception | None = None):
        self.get_map = get_map or {}
        self.post_map = post_map or {}
        self.default = default
        self.get_raise = get_raise
        self.requested: list[str] = []

    async def get(self, url: str):
        self.requested.append(url)
        if self.get_raise is not None:
            raise self.get_raise
        spec = self.get_map.get(url, self.default)
        return _Resp(*spec) if isinstance(spec, tuple) else _Resp(spec)

    async def post(self, url: str, data=None, json=None, headers=None):
        self.requested.append(url)
        spec = self.post_map.get(url, self.default)
        return _Resp(*spec) if isinstance(spec, tuple) else _Resp(spec)

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
    domain_rate_limiter.set_interval("www.ccgp-yunnan.gov.cn", 0)
    yield


def _patch(monkeypatch, client: _FakeClient) -> None:
    monkeypatch.setattr(ra.httpx, "AsyncClient", _FakeClientFactory(client))


@pytest.mark.asyncio
async def test_happy_path_filters_and_builds(monkeypatch):
    client = _FakeClient(
        post_map={LIST_URL: (200, LIST_JSON)},
        get_map={DETAIL_URL: (200, DETAIL_HTML)},
    )
    _patch(monkeypatch, client)
    payloads = await YunnanAdapter()._fetch_and_build(limit=5)
    # 中标类标题过滤 + 脏记录过滤后只剩 1 条
    assert len(payloads) == 1
    p = payloads[0]
    assert p["source_platform"] == "yunnan"
    assert p["source_url"] == DETAIL_URL
    assert p["project_name"]
    assert p["core_content"]


@pytest.mark.asyncio
async def test_fetch_payloads_wrap_ok(monkeypatch):
    client = _FakeClient(
        post_map={LIST_URL: (200, LIST_JSON)},
        get_map={DETAIL_URL: (200, DETAIL_HTML)},
    )
    _patch(monkeypatch, client)
    r = await YunnanAdapter().fetch_payloads(limit=5)
    assert r.status == STATUS_OK and r.fetched == 1


@pytest.mark.asyncio
async def test_detail_transport_error_skips_item_not_source(monkeypatch):
    client = _FakeClient(
        post_map={LIST_URL: (200, LIST_JSON)},
        get_raise=httpx.ConnectError("connection reset"),
    )
    _patch(monkeypatch, client)
    r = await YunnanAdapter().fetch_payloads(limit=5)
    assert r.status == STATUS_EMPTY and r.ok is True and r.fetched == 0


@pytest.mark.asyncio
async def test_non_json_list_yields_empty(monkeypatch):
    client = _FakeClient(post_map={LIST_URL: (200, "<html>spa shell</html>")})
    _patch(monkeypatch, client)
    r = await YunnanAdapter().fetch_payloads(limit=5)
    assert r.status == STATUS_EMPTY and r.fetched == 0


@pytest.mark.asyncio
async def test_403_on_list_post_triggers_breaker(monkeypatch):
    client = _FakeClient(post_map={LIST_URL: 403}, default=403)
    _patch(monkeypatch, client)
    a = YunnanAdapter()
    r1 = await a.fetch_payloads(limit=5)
    assert r1.status == STATUS_BLOCKED_403
    before = len(client.requested)
    r2 = await a.fetch_payloads(limit=5)
    assert r2.status == STATUS_BLOCKED_403
    assert len(client.requested) == before


@pytest.mark.asyncio
async def test_empty_list_retries_once_then_recovers(monkeypatch):
    """D4 真机守卫：列表偶发“系统异常”（HTTP 200 但解析为空）
    → 重试一次后恢复，且列表接口恰好请求两次。"""
    error_body = json.dumps(
        {"code": "", "data": {}, "message": "系统异常", "status": 500},
        ensure_ascii=False)

    class _FlakyClient(_FakeClient):
        def __init__(self):
            super().__init__(
                post_map={LIST_URL: (200, LIST_JSON)},
                get_map={DETAIL_URL: (200, DETAIL_HTML)},
            )
            self._first = True

        async def post(self, url, data=None, json=None, headers=None):
            self.requested.append(url)
            if self._first:
                self._first = False
                return _Resp(200, error_body)
            return _Resp(200, LIST_JSON)

    client = _FlakyClient()
    _patch(monkeypatch, client)
    r = await YunnanAdapter().fetch_payloads(limit=5)
    assert r.status == STATUS_OK and r.fetched == 1
    assert client.requested.count(LIST_URL) == 2
