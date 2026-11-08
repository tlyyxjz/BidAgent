# -*- coding: utf-8 -*-
"""河南源守卫测试：首页 SSR 元数据模式采集器 + HenanAdapter + 注册表。"""
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
        "collect_henan", SCRIPTS / "collect_henan.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["collect_henan"] = mod
    spec.loader.exec_module(mod)
    return mod


ch = _load_collector()


_HOME_HTML = """
<ul>
<li><span class="Right Gray">2026-08-20</span>
<span class="danwei">河南省胸科医院</span>
<a href="/henan/content?infoId=2005001&channelCode=H600102"
   title="河南省胸科医院设备采购项目中标公告">标题缩略…</a></li>
<li><span class="Right Gray">2026-08-19</span>
<a href="/henan/content?infoId=2005002&channelCode=H600101"
   title="某单位办公楼施工招标公告">标题缩略…</a></li>
<li><span class="Right Gray">2026-08-19</span>
<a href="/henan/content?infoId=2005001&channelCode=H600102"
   title="河南省胸科医院设备采购项目中标公告">重复 infoId 脏块</a></li>
<li>无链接脏块</li>
</ul>
"""

_DETAIL_HTML = """
<div class="article-info">
<span>发布机构</span><span>河南省胸科医院</span>
<span>发布日期：2026-08-20 15:21</span>
</div>
<div class="article-body">正文为 PDF 附件渠道。</div>
"""


# ---------- parse_list ----------

def test_parse_list_fields():
    items = ch.parse_list(_HOME_HTML)
    assert len(items) == 2  # 重复 infoId 与无链接脏块被剔除
    it = items[0]
    assert it["info_id"] == "2005001"
    assert it["title"] == "河南省胸科医院设备采购项目中标公告"
    assert it["date"] == "2026-08-20"
    assert it["unit"] == "河南省胸科医院"
    assert it["url"] == (
        "http://www.ccgp-henan.gov.cn"
        "/henan/content?infoId=2005001&channelCode=H600102")


def test_parse_list_dirty_empty():
    assert ch.parse_list("") == []
    assert ch.parse_list("<li>无链接</li>") == []
    assert ch.parse_list(None) == []


# ---------- filter ----------

def test_filter_result_items():
    items = ch.parse_list(_HOME_HTML)
    kept = ch.filter_result_items(items)
    assert len(kept) == 1  # 招标公告被过滤
    assert kept[0]["info_id"] == "2005001"


def test_filter_all_types_passthrough():
    items = ch.parse_list(_HOME_HTML)
    assert ch.filter_result_items(items, all_types=True) == items


# ---------- parse_detail ----------

def test_parse_detail_time_org():
    d = ch.parse_detail(_DETAIL_HTML)
    assert d["publish_time"] == datetime(2026, 8, 20, 15, 21)
    assert d["publish_org"] == "河南省胸科医院"


def test_parse_detail_garbage():
    d = ch.parse_detail("<html>什么都没有</html>")
    assert d["publish_time"] is None
    assert d["publish_org"] is None


# ---------- build_payload 契约 ----------

def test_build_payload_contract():
    rec = ch.filter_result_items(ch.parse_list(_HOME_HTML))[0]
    p = ch.build_payload(rec, ch.parse_detail(_DETAIL_HTML))
    assert p["project_name"] == "河南省胸科医院设备采购项目中标公告"
    assert p["publish_time"] == datetime(2026, 8, 20, 15, 21)
    assert p["notice_type"] == "award"
    assert p["source_platform"] == "henan"
    assert p["source_url"].startswith("http://www.ccgp-henan.gov.cn/henan/content")
    # 列表元数据模式：拿不到的字段必须为 None（宁可缺、不可编）
    assert p["win_amount"] is None
    assert p["win_company"] is None
    assert p["tender_org"] is None
    assert p["bid_number"] is None
    # 大件五存证：64 位 SHA-256 + 64 位有符号 simhash
    assert len(p["content_sha256"]) == 64
    assert len(p["raw_text_sha256"]) == 64
    assert isinstance(p["simhash"], int)
    assert -(1 << 63) <= p["simhash"] < (1 << 63)
    # 口径说明如实落库
    assert "口径说明" in p["core_content"]


def test_build_payload_fallback_list_date():
    rec = ch.filter_result_items(ch.parse_list(_HOME_HTML))[0]
    p = ch.build_payload(rec, None)
    assert p["publish_time"] == datetime(2026, 8, 20)  # 回落列表日期 0 点


# ---------- 适配器 ----------

class _Resp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


class _FakeClient:
    def __init__(self, responses: list[_Resp]):
        self._responses = responses
        self.gets: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kw):
        self.gets.append(url)
        idx = min(len(self.gets) - 1, len(self._responses) - 1)
        return self._responses[idx]


class _FakeClientFactory:
    def __init__(self, responses: list[_Resp]):
        self._responses = responses
        self.instances: list[_FakeClient] = []

    def __call__(self, *a, **kw):
        c = _FakeClient(self._responses)
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
    factory = _FakeClientFactory([_Resp(200, _HOME_HTML), _Resp(200, _DETAIL_HTML)])
    monkeypatch.setattr(ra.httpx, "AsyncClient", factory)
    adapter = ra.HenanAdapter()
    payloads = await adapter._fetch_and_build(limit=5)
    assert len(payloads) == 1  # 招标公告被过滤，仅中标类入库
    assert payloads[0]["source_platform"] == "henan"
    assert factory.instances[0].gets[0] == ra.HenanAdapter.list_url


@pytest.mark.asyncio
async def test_adapter_403_blocked(monkeypatch, _zero_interval):
    import app.services.realtime_adapters as ra
    factory = _FakeClientFactory([_Resp(403)])
    monkeypatch.setattr(ra.httpx, "AsyncClient", factory)
    adapter = ra.HenanAdapter()
    with pytest.raises(ra.SourceBlockedError):
        await adapter._fetch_and_build(limit=3)


@pytest.mark.asyncio
async def test_adapter_empty_list(monkeypatch, _zero_interval):
    import app.services.realtime_adapters as ra
    factory = _FakeClientFactory([_Resp(200, "<html>无公告</html>")])
    monkeypatch.setattr(ra.httpx, "AsyncClient", factory)
    adapter = ra.HenanAdapter()
    assert await adapter._fetch_and_build(limit=3) == []


# ---------- 注册表守卫 ----------

def test_registry_contains_henan():
    from app.services.realtime_sources import _ADAPTER_REGISTRY, resolve_adapters
    assert "henan" in _ADAPTER_REGISTRY
    adapters = resolve_adapters(["henan"])
    assert len(adapters) == 1
    assert adapters[0].source == "henan"


def test_default_sources_include_henan():
    from app.services.realtime_sources import DEFAULT_SOURCES
    assert "henan" in DEFAULT_SOURCES
