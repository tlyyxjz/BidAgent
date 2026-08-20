# -*- coding: utf-8 -*-
"""青岛源守卫测试：列表元数据模式采集器 + QingdaoAdapter + 注册表。"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"


def _load_collector():
    spec = importlib.util.spec_from_file_location(
        "collect_qingdao", SCRIPTS / "collect_qingdao.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["collect_qingdao"] = mod
    spec.loader.exec_module(mod)
    return mod


cq = _load_collector()


def _rec(**kw) -> dict:
    base = {
        "id": "ENC_ID_A==",
        "stringId": "232325201",
        "subject": "青岛市某单位设备采购项目中标公告",
        "projectName": "设备采购项目",
        "projectCode": "SDGP370200000202602000929",
        "pdate": "2026-08-20T21:11:50.000+08:00",
        "regionName": "市级",
    }
    base.update(kw)
    return base


def _list_payload(*records) -> dict:
    return {"status": 200, "message": "OK",
            "data": {"code": 100, "message": "接口调用成功并成功返回",
                     "data": {"records": list(records), "total": len(records),
                              "size": 10, "current": 1}, "success": True},
            "success": True}


# ---------- parse_records ----------

def test_parse_records_fields():
    items = cq.parse_records(_list_payload(_rec()))
    assert len(items) == 1
    it = items[0]
    assert it["id"] == "ENC_ID_A=="
    assert it["title"] == "青岛市某单位设备采购项目中标公告"
    assert it["project_code"] == "SDGP370200000202602000929"
    assert it["region"] == "市级"


def test_parse_records_skip_dirty():
    payload = _list_payload(
        _rec(subject=""),            # 无标题
        _rec(id=""),                 # 无 ID
        {"not": "a record"},         # 缺关键字段
    )
    assert cq.parse_records(payload) == []


def test_parse_records_missing_project_code_is_none():
    items = cq.parse_records(_list_payload(_rec(projectCode=None)))
    assert items[0]["project_code"] is None


def test_parse_records_bad_shape_empty():
    assert cq.parse_records({}) == []
    assert cq.parse_records({"data": None}) == []
    assert cq.parse_records("not json") == []


# ---------- filter / pdate ----------

def test_filter_result_items():
    items = [
        {"title": "某项目中标公告"},
        {"title": "某项目成交公告"},
        {"title": "某项目结果公告"},
        {"title": "某项目招标公告"},
        {"title": "政策法规解读"},
    ]
    kept = cq.filter_result_items(items)
    assert [x["title"] for x in kept] == [
        "某项目中标公告", "某项目成交公告", "某项目结果公告"]


def test_filter_all_types_passthrough():
    items = [{"title": "招标公告"}, {"title": "中标公告"}]
    assert cq.filter_result_items(items, all_types=True) == items


def test_parse_pdate():
    assert cq.parse_pdate("2026-08-20T21:11:50.000+08:00") == "2026-08-20 21:11:50"
    assert cq.parse_pdate("2026-08-20 09:00:00") == "2026-08-20 09:00:00"
    assert cq.parse_pdate("") is None
    assert cq.parse_pdate("garbage") is None


def test_pdate_to_datetime():
    assert cq.pdate_to_datetime("2026-08-20T21:11:50.000+08:00") == \
        datetime(2026, 8, 20, 21, 11, 50)
    assert cq.pdate_to_datetime("") is None
    assert cq.pdate_to_datetime("garbage") is None


# ---------- build_payload 契约 ----------

def test_build_payload_contract():
    rec = cq.parse_records(_list_payload(_rec()))[0]
    p = cq.build_payload(rec)
    assert p["project_name"].endswith("中标公告")
    assert p["bid_number"] == "SDGP370200000202602000929"
    assert p["publish_time"] == datetime(2026, 8, 20, 21, 11, 50)
    assert p["notice_type"] == "award"
    assert p["source_platform"] == "qingdao"
    assert p["source_url"].startswith(
        "http://www.ccgp-qingdao.gov.cn/#/readnotice?id=")
    # 列表元数据模式：拿不到的字段必须为 None（宁可缺、不可编）
    assert p["win_amount"] is None
    assert p["win_company"] is None
    assert p["tender_org"] is None
    # 大件五存证：64 位 SHA-256
    assert len(p["content_sha256"]) == 64
    assert len(p["raw_text_sha256"]) == 64
    assert isinstance(p["simhash"], int)
    # 口径说明如实落库
    assert "口径说明" in p["core_content"]


def test_build_payload_no_code():
    rec = cq.parse_records(_list_payload(_rec(projectCode=None)))[0]
    p = cq.build_payload(rec)
    assert p["bid_number"] is None


# ---------- 适配器 ----------

class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, resp: _Resp):
        self._resp = resp
        self.posts: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None, **kw):  # noqa: A002
        self.posts.append((url, json))
        return self._resp


class _FakeClientFactory:
    def __init__(self, resp: _Resp):
        self._resp = resp
        self.instances: list[_FakeClient] = []

    def __call__(self, *a, **kw):
        c = _FakeClient(self._resp)
        self.instances.append(c)
        return c


@pytest.fixture()
def _zero_interval(monkeypatch):
    from app.core import rate_limiter
    async def _noop(url):
        return None
    monkeypatch.setattr(rate_limiter.domain_rate_limiter, "wait", _noop)


@pytest.mark.asyncio
async def test_adapter_happy_path(monkeypatch, _zero_interval):
    import app.services.realtime_adapters as ra
    resp = _Resp(200, _list_payload(_rec(), _rec(subject="政策解读", projectCode=None)))
    factory = _FakeClientFactory(resp)
    monkeypatch.setattr(ra.httpx, "AsyncClient", factory)
    adapter = ra.QingdaoAdapter()
    payloads = await adapter._fetch_and_build(limit=5)
    assert len(payloads) == 1  # 非中标类标题被过滤
    assert payloads[0]["source_platform"] == "qingdao"
    assert factory.instances[0].posts[0][0] == cq.LIST_API


@pytest.mark.asyncio
async def test_adapter_403_blocked(monkeypatch, _zero_interval):
    import app.services.realtime_adapters as ra
    factory = _FakeClientFactory(_Resp(403))
    monkeypatch.setattr(ra.httpx, "AsyncClient", factory)
    adapter = ra.QingdaoAdapter()
    with pytest.raises(ra.SourceBlockedError):
        await adapter._fetch_and_build(limit=3)


@pytest.mark.asyncio
async def test_adapter_empty_list(monkeypatch, _zero_interval):
    import app.services.realtime_adapters as ra
    factory = _FakeClientFactory(_Resp(200, _list_payload()))
    monkeypatch.setattr(ra.httpx, "AsyncClient", factory)
    adapter = ra.QingdaoAdapter()
    assert await adapter._fetch_and_build(limit=3) == []


# ---------- 注册表守卫 ----------

def test_registry_contains_qingdao():
    from app.services.realtime_sources import _ADAPTER_REGISTRY, resolve_adapters
    assert "qingdao" in _ADAPTER_REGISTRY
    adapters = resolve_adapters(["qingdao"])
    assert len(adapters) == 1
    assert adapters[0].source == "qingdao"


def test_default_sources_include_qingdao():
    from app.services.realtime_sources import DEFAULT_SOURCES
    assert "qingdao" in DEFAULT_SOURCES
