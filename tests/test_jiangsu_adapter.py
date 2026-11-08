"""JiangsuAdapter 离线测试（D2）：假 httpx 客户端，真解析链，零网络。

覆盖：首页链接→公告接口→payload 契约 / msg!=OK 单条跳过（宁可少给）/
空首页 / 403 熔断 / 抓取失败归一。
"""

from __future__ import annotations

import json

import pytest

from app.core.rate_limiter import domain_rate_limiter
from app.services import realtime_adapters as ra
from app.services.realtime_adapters import JiangsuAdapter
from app.services.realtime_sources import (
    STATUS_BLOCKED_403,
    STATUS_EMPTY,
    STATUS_ERROR,
    STATUS_OK,
)

GGID = "a" * 24
HOME_HTML = (
    '<html><body><a href="/jiangsu/js_cggg/details.html?gglb=zbgs&ggid='
    + GGID + '">示例大学设备采购项目中标公告</a></body></html>'
)
DETAIL_URL = (f"http://www.ccgp-jiangsu.gov.cn/jiangsu/js_cggg/"
              f"details.html?gglb=zbgs&ggid={GGID}")
API_URL = (f"http://www.ccgp-jiangsu.gov.cn/pss/jsp/"
           f"relevantCgggListByProjId.jsp?gglb=zbgs&ggid={GGID}&projId=")
API_JSON = json.dumps({
    "msg": "OK",
    "cgxm": {
        "projName": "示例大学设备采购项目",
        "projNumber": "JSCG2026-001",
        "buyerName": "示例大学",
        "agentName": "示例代理机构",
    },
    "data": [{
        "title": "示例大学设备采购项目中标公告",
        "summary": "项目编号：JSCG2026-001。采购内容：设备一批。",
        "publishDate": "2026-08-18 10:00:00",
        "zoneName": "南京市",
        "url": "",
    }],
}, ensure_ascii=False)


class _Resp:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


class _FakeClient:
    def __init__(self, mapping=None, default: int = 404):
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
    domain_rate_limiter.set_interval("www.ccgp-jiangsu.gov.cn", 0)
    yield


def _patch_client(monkeypatch, client: _FakeClient) -> None:
    monkeypatch.setattr(ra.httpx, "AsyncClient", _FakeClientFactory(client))


@pytest.mark.asyncio
async def test_happy_path_builds_payload_contract(monkeypatch):
    client = _FakeClient({
        JiangsuAdapter.list_url: (200, HOME_HTML),
        API_URL: (200, API_JSON),
    })
    _patch_client(monkeypatch, client)
    payloads = await JiangsuAdapter()._fetch_and_build(limit=5)
    assert len(payloads) == 1
    p = payloads[0]
    assert p["source_platform"] == "jiangsu"
    assert p["source_url"] == DETAIL_URL
    assert p["project_name"] == "示例大学设备采购项目中标公告"
    assert p["bid_number"] == "JSCG2026-001"
    assert p["tender_org"] == "示例大学"
    assert p["publish_time"] is not None


@pytest.mark.asyncio
async def test_api_msg_not_ok_skipped_not_fabricated(monkeypatch):
    bad = json.dumps({"msg": "FAIL"}, ensure_ascii=False)
    client = _FakeClient({
        JiangsuAdapter.list_url: (200, HOME_HTML),
        API_URL: (200, bad),
    })
    _patch_client(monkeypatch, client)
    r = await JiangsuAdapter().fetch_payloads(limit=5)
    assert r.status == STATUS_EMPTY and r.ok is True and r.fetched == 0


@pytest.mark.asyncio
async def test_empty_homepage(monkeypatch):
    client = _FakeClient({JiangsuAdapter.list_url: (200, "<html>无链接</html>")})
    _patch_client(monkeypatch, client)
    r = await JiangsuAdapter().fetch_payloads(limit=5)
    assert r.status == STATUS_EMPTY and r.fetched == 0


@pytest.mark.asyncio
async def test_403_triggers_circuit_breaker(monkeypatch):
    client = _FakeClient(default=403)
    _patch_client(monkeypatch, client)
    a = JiangsuAdapter()
    r1 = await a.fetch_payloads(limit=5)
    assert r1.status == STATUS_BLOCKED_403
    before = len(client.requested)
    r2 = await a.fetch_payloads(limit=5)
    assert r2.status == STATUS_BLOCKED_403
    assert len(client.requested) == before


@pytest.mark.asyncio
async def test_fetch_failure_normalized(monkeypatch):
    client = _FakeClient(default=500)
    _patch_client(monkeypatch, client)
    r = await JiangsuAdapter().fetch_payloads(limit=5)
    assert r.status == STATUS_ERROR and r.ok is False


GGID_B = "b" * 24
_HOME_MIXED_HTML = (
    '<html><body>'
    '<a href="/jiangsu/js_cggg/details.html?gglb=gkzb&ggid=' + GGID
    + '">示例大学设备采购项目中标公告</a>'
    '<a href="/jiangsu/js_cggg/details.html?gglb=gkzb&ggid=' + GGID_B
    + '">示例医院物业服务采购公告</a>'
    '</body></html>'
)
_API_URL_B = (f"http://www.ccgp-jiangsu.gov.cn/pss/jsp/"
              f"relevantCgggListByProjId.jsp?gglb=gkzb&ggid={GGID_B}&projId=")
_API_URL_A = (f"http://www.ccgp-jiangsu.gov.cn/pss/jsp/"
              f"relevantCgggListByProjId.jsp?gglb=gkzb&ggid={GGID}&projId=")
_API_JSON_B = json.dumps({
    "msg": "OK",
    "cgxm": {"projName": "示例医院物业服务项目", "projNumber": "JSCG2026-002"},
    "data": [{"title": "示例医院物业服务采购公告",
              "summary": "项目编号：JSCG2026-002。",
              "publishDate": "2026-08-18 09:00:00", "zoneName": "南京市"}],
}, ensure_ascii=False)


@pytest.mark.asyncio
async def test_non_award_notices_filtered_after_parse(monkeypatch):
    """D4 真机修复守卫：首页链接混有多类型公告 → 解析后只保留中标类。"""
    api_json_a = json.dumps({
        "msg": "OK",
        "cgxm": {"projName": "示例大学设备采购项目", "projNumber": "JSCG2026-001"},
        "data": [{"title": "示例大学设备采购项目中标公告",
                  "summary": "项目编号：JSCG2026-001。",
                  "publishDate": "2026-08-18 10:00:00", "zoneName": "南京市"}],
    }, ensure_ascii=False)
    client = _FakeClient({
        JiangsuAdapter.list_url: (200, _HOME_MIXED_HTML),
        _API_URL_A: (200, api_json_a),
        _API_URL_B: (200, _API_JSON_B),
    })
    _patch_client(monkeypatch, client)
    payloads = await JiangsuAdapter()._fetch_and_build(limit=5)
    assert len(payloads) == 1
    assert payloads[0]["project_name"] == "示例大学设备采购项目中标公告"
