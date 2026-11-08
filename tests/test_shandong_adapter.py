"""ShandongAdapter 离线测试（D3 契约夹具，D6 实机核对后更新），假客户端零网络。

D6 实机：API 已搬到 :8087/api（主站 :443 对 API 路径返 405），
列表响应为双层包装；本文件夹具同步新地址，并新增双层包装守卫测试。
覆盖：POST 列表→详情→payload 契约 / 中标类过滤 / 空 records 归一 empty /
详情 JSON 包裹形态解析 / 403 熔断 / 5xx 归一 error / 实机双层包装。
"""

from __future__ import annotations

import json

import pytest

from app.core.rate_limiter import domain_rate_limiter
from app.services import realtime_adapters as ra
from app.services.realtime_adapters import ShandongAdapter
from app.services.realtime_sources import (
    STATUS_BLOCKED_403,
    STATUS_EMPTY,
    STATUS_ERROR,
    STATUS_OK,
)

LIST_URL = "https://www.ccgp-shandong.gov.cn:8087/api/website/site/getListByCode"
LIST_JSON = json.dumps({"records": [
    {"id": "SD001", "colCode": "29", "title": "示例大学设备采购项目中标公告",
     "date": "2026-08-15", "areaName": "济南市", "userName": ""},
    {"id": "SD002", "colCode": "29", "title": "某服务招标公告",
     "date": "2026-08-15", "areaName": "青岛市", "userName": ""},
    {"id": "", "colCode": "29", "title": "空ID脏记录",
     "date": "2026-08-15", "areaName": "", "userName": ""},
]}, ensure_ascii=False)
# D6 实机响应形态：外层 {status,message,data:{code,message,data:{records}}}
LIST_JSON_WRAPPED = json.dumps({
    "timestamp": "2026-08-19T10:51:37.252+00:00", "status": 200, "error": "",
    "exception": "", "message": "OK",
    "path": "/api/website/site/getListByCode",
    "data": {"code": 100, "message": "接口调用成功并成功返回",
             "data": {"records": [
                 {"id": "SD001", "colCode": "29",
                  "title": "示例大学设备采购项目中标公告",
                  "date": "2026-08-15", "areaName": "济南市", "userName": ""}]}}
}, ensure_ascii=False)
DETAIL_URL = ("https://www.ccgp-shandong.gov.cn:8087/api/website/site/"
              "getDetail?id=SD001&colCode=29")
# 展示用 source_url 为可浏览详情页（主站 :443）
SOURCE_URL = ("https://www.ccgp-shandong.gov.cn/detail"
              "?id=SD001&colCode=29")
DETAIL_HTML = ("<html><head><title>示例大学设备采购项目中标公告</title></head>"
               "<body><p>项目编号：SDGP37000000202602001234</p>"
               "<p>中标供应商：山东示例科技有限公司</p></body></html>")
# 契约响应未实机确认：详情接口可能返回 JSON 包裹 HTML
DETAIL_JSON_WRAPPED = json.dumps({"data": {"noticeBody": DETAIL_HTML}},
                                 ensure_ascii=False)
# D6 实机：接口偶发 code=999 限流抖动（HTTP 200 + data=null）
DETAIL_JSON_999 = json.dumps(
    {"status": 200, "message": "OK",
     "data": {"code": 999, "message": "抱歉，服务请求失败，请稍后重试。",
              "data": None, "success": False}, "success": True},
    ensure_ascii=False)


class _Resp:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


class _FakeClient:
    def __init__(self, get_map=None, post_map=None, default: int = 404):
        self.get_map = get_map or {}
        self.post_map = post_map or {}
        self.default = default
        self.requested: list[str] = []

    async def get(self, url: str):
        self.requested.append(url)
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
    domain_rate_limiter.set_interval("www.ccgp-shandong.gov.cn", 0)
    yield


def _patch(monkeypatch, client: _FakeClient) -> None:
    monkeypatch.setattr(ra.httpx, "AsyncClient", _FakeClientFactory(client))


@pytest.mark.asyncio
async def test_happy_path_plain_html_detail(monkeypatch):
    client = _FakeClient(
        post_map={LIST_URL: (200, LIST_JSON)},
        get_map={DETAIL_URL: (200, DETAIL_HTML)},
    )
    _patch(monkeypatch, client)
    payloads = await ShandongAdapter()._fetch_and_build(limit=5)
    # 中标类过滤 + 空 ID 脏记录过滤后只剩 1 条
    assert len(payloads) == 1
    p = payloads[0]
    assert p["source_platform"] == "shandong"
    assert p["source_url"] == SOURCE_URL
    assert p["project_name"]


@pytest.mark.asyncio
async def test_json_wrapped_detail_unwrapped(monkeypatch):
    """契约未实机核对的防御性：JSON 包裹形态也能解出 HTML。"""
    client = _FakeClient(
        post_map={LIST_URL: (200, LIST_JSON)},
        get_map={DETAIL_URL: (200, DETAIL_JSON_WRAPPED)},
    )
    _patch(monkeypatch, client)
    r = await ShandongAdapter().fetch_payloads(limit=5)
    assert r.status == STATUS_OK and r.fetched == 1
    assert "项目编号" in r.payloads[0]["core_content"]


@pytest.mark.asyncio
async def test_real_machine_wrapped_records(monkeypatch):
    """D6 实机契约：双层包装 {status,data:{code,data:{records}}} 也能解析。"""
    client = _FakeClient(
        post_map={LIST_URL: (200, LIST_JSON_WRAPPED)},
        get_map={DETAIL_URL: (200, DETAIL_HTML)},
    )
    _patch(monkeypatch, client)
    payloads = await ShandongAdapter()._fetch_and_build(limit=5)
    assert len(payloads) == 1
    assert payloads[0]["source_url"] == SOURCE_URL


@pytest.mark.asyncio
async def test_detail_code999_skipped_not_crash(monkeypatch):
    """D6 实机：详情 code=999（HTTP 200，无正文）→ 跳过该条不阻断。"""
    client = _FakeClient(
        post_map={LIST_URL: (200, LIST_JSON)},
        get_map={DETAIL_URL: (200, DETAIL_JSON_999)},
    )
    _patch(monkeypatch, client)
    payloads = await ShandongAdapter()._fetch_and_build(limit=5)
    assert payloads == []


@pytest.mark.asyncio
async def test_detail_5xx_skipped_not_crash(monkeypatch):
    """详情 5xx（RuntimeError）→ 跳过该条不阻断。"""
    client = _FakeClient(
        post_map={LIST_URL: (200, LIST_JSON)},
        get_map={DETAIL_URL: 502},
    )
    _patch(monkeypatch, client)
    payloads = await ShandongAdapter()._fetch_and_build(limit=5)
    assert payloads == []


@pytest.mark.asyncio
async def test_empty_records_yields_empty(monkeypatch):
    """契约形态变化/无数据 → empty（待实机核对场景的安全归一）。"""
    client = _FakeClient(post_map={LIST_URL: (200, json.dumps({"records": []}))})
    _patch(monkeypatch, client)
    r = await ShandongAdapter().fetch_payloads(limit=5)
    assert r.status == STATUS_EMPTY and r.fetched == 0


@pytest.mark.asyncio
async def test_403_triggers_breaker(monkeypatch):
    client = _FakeClient(post_map={LIST_URL: 403}, default=403)
    _patch(monkeypatch, client)
    a = ShandongAdapter()
    r1 = await a.fetch_payloads(limit=5)
    assert r1.status == STATUS_BLOCKED_403
    before = len(client.requested)
    r2 = await a.fetch_payloads(limit=5)
    assert r2.status == STATUS_BLOCKED_403
    assert len(client.requested) == before


@pytest.mark.asyncio
async def test_list_5xx_normalized_error(monkeypatch):
    client = _FakeClient(post_map={LIST_URL: 500}, default=500)
    _patch(monkeypatch, client)
    r = await ShandongAdapter().fetch_payloads(limit=5)
    assert r.status == STATUS_ERROR and r.ok is False
