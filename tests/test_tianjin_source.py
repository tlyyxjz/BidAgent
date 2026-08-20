# -*- coding: utf-8 -*-
"""天津市政府采购网采集器+适配器离线守卫测试（大件二 15 城·首城）。

夹具基于 2026-08-20 实机核对的真实页面形态构造：
- 首页列表：/viewer.do?id=<数字>&ver=2 + title 属性 + Java Date 日期 span；
- 详情表格：表头「供应商名称/供应商地址/统一社会信用代码/企业办公电话/
  &#20013;&#26631;金额(万元)/评审得分」+ 数据行（金额列为实体编码，必须解码）。
覆盖：列表解析/日期解析/实体解码/单元格抽取/build_payload 契约/
中标类过滤/适配器 happy path/403 熔断/空归一/注册表守卫。
"""
from __future__ import annotations

import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import collect_tianjin as ct  # noqa: E402

# ------------------------------------------------------------ 契约夹具

HOME_HTML = """
<html><body>
<ul class="txtList">
<li><b>·</b><a href="/viewer.do?id=1093822394&ver=2" target="v1"
 title="示例大学 示例大学数字孪生系统采购项目 (项目编号:TJTEST-2026-001)中标公告">
 同文</a><span class="times">Thu Aug 20 16:19:39 CST 2026</span></li>
<li><b>·</b><a href="/viewer.do?id=1093822001&ver=2" target="v2"
 title="某单位食堂食材采购项目成交公告">同文</a>
 <span class="times">Wed Aug 19 09:00:00 CST 2026</span></li>
<li><b>·</b><a href="/viewer.do?id=1093800002&ver=2" target="v3"
 title="市财政局关于印发采购管理办法的通知">同文</a>
 <span class="times">Tue Aug 18 08:00:00 CST 2026</span></li>
<li><b>·</b><a href="/viewer.do?id=1093822394&ver=2" target="v1"
 title="重复ID脏记录中标公告">重复</a>
 <span class="times">Thu Aug 20 16:19:39 CST 2026</span></li>
</ul>
</body></html>
"""

DETAIL_HTML = (
    "<html><head><title>中标公告</title></head><body>"
    "<h1>示例大学数字孪生系统采购项目中标公告</h1>"
    "<p>一、项目编号：TJTEST-2026-001</p>"
    "<p>二、项目名称：示例大学数字孪生系统采购项目</p>"
    "<p>三、中标信息</p>"
    "<table><tr><td>供应商名称</td><td>供应商地址</td><td>统一社会信用代码</td>"
    "<td>企业办公电话</td><td>&#20013;&#26631;金额(万元)</td><td>评审得分</td></tr>"
    "<tr><td>天津市示例科技有限公司</td><td>天津市滨海高新区示例路1号</td>"
    "<td>91120116075907467P</td><td>022-23774899</td><td>149.8</td>"
    "<td>97.04</td></tr></table>"
    "<p>四、评审专家名单：张三，李四</p>"
    "</body></html>"
)


# ---------------------------------------------------------------- 列表解析

def test_parse_list_contract_shape():
    items = ct.parse_list(HOME_HTML)
    # 重复 id 去重：4 条链接 → 3 条
    assert len(items) == 3
    assert items[0]["id"] == "1093822394"
    assert items[0]["title"].startswith("示例大学")
    assert items[0]["url"] == (
        "https://www.ccgp-tianjin.gov.cn/viewer.do?id=1093822394&ver=2")
    assert items[0]["publish_dt"] == datetime(2026, 8, 20, 16, 19, 39)


def test_parse_list_empty_html():
    assert ct.parse_list("") == []
    assert ct.parse_list("<html><body>无链接</body></html>") == []


def test_parse_java_date_variants():
    assert ct.parse_java_date("Thu Aug 20 16:19:39 CST 2026") == \
        datetime(2026, 8, 20, 16, 19, 39)
    assert ct.parse_java_date("Wed Aug 19 09:00:00 CST 2026") == \
        datetime(2026, 8, 19, 9, 0, 0)
    assert ct.parse_java_date("不是日期") is None
    assert ct.parse_java_date(None) is None


def test_filter_result_items_only_award():
    items = ct.parse_list(HOME_HTML)
    got = ct.filter_result_items(items)
    assert len(got) == 2  # 政策通知被过滤
    assert all(any(k in it["title"] for k in ("中标", "成交")) for it in got)
    assert len(ct.filter_result_items(items, all_types=True)) == 3


# ---------------------------------------------------------------- 详情抽取

def test_strip_tags_decodes_entities():
    """实机关键形态：表头'中标金额'用 HTML 实体编码，必须解码。"""
    text = ct.strip_tags(DETAIL_HTML)
    assert "中标金额(万元)" in text
    assert "&#20013;" not in text


def test_parse_cells_extracts_table():
    cells = ct.parse_cells(DETAIL_HTML)
    assert "供应商名称" in cells
    assert "天津市示例科技有限公司" in cells
    assert "149.8" in cells


def test_extract_from_cells_winner_and_amount():
    cells = ct.parse_cells(DETAIL_HTML)
    winners, amount = ct.tj_extract_from_cells(cells)
    assert winners == ["天津市示例科技有限公司"]
    assert amount == Decimal("149.8")


def test_extract_from_cells_no_credit_code_as_amount():
    """统一社会信用代码（18位含字母）与电话（带-）绝不能被当金额。"""
    cells = ["供应商名称", "供应商地址", "统一社会信用代码", "企业办公电话",
             "91120116075907467P", "022-23774899", "天津市示例科技有限公司",
             "天津市某路1号"]
    winners, amount = ct.tj_extract_from_cells(cells)
    assert winners == ["天津市示例科技有限公司"]
    assert amount is None  # 无金额列数值 → 诚实为空


def test_extract_from_cells_empty():
    assert ct.tj_extract_from_cells([]) == ([], None)


def test_build_payload_contract():
    item = {"id": "1093822394",
            "title": "示例大学数字孪生系统采购项目 (项目编号:TJTEST-2026-001)中标公告",
            "url": "https://www.ccgp-tianjin.gov.cn/viewer.do?id=1093822394&ver=2",
            "publish_dt": datetime(2026, 8, 20, 16, 19, 39)}
    p = ct.build_payload(item, DETAIL_HTML)
    assert p["source_platform"] == "tianjin"
    assert p["bid_number"] == "TJTEST-2026-001"
    assert p["win_company"] == "天津市示例科技有限公司"
    assert p["win_amount"] == Decimal("149.8")
    assert p["notice_type"] == "award"
    assert p["source_url"].startswith("https://www.ccgp-tianjin.gov.cn/viewer.do")
    assert p["publish_time"] == datetime(2026, 8, 20, 16, 19, 39)
    assert "中标信息" in p["core_content"]
    assert p["simhash"] is not None
    # 大件五：collect 直入路径也必须入库即存证
    assert len(p["content_sha256"]) == 64
    assert len(p["raw_text_sha256"]) == 64


# ---------------------------------------------------------------- 适配器离线

from app.core.rate_limiter import domain_rate_limiter  # noqa: E402
from app.services import realtime_adapters as ra  # noqa: E402
from app.services.realtime_adapters import TianjinAdapter  # noqa: E402
from app.services.realtime_sources import (  # noqa: E402
    DEFAULT_SOURCES,
    STATUS_BLOCKED_403,
    STATUS_EMPTY,
    STATUS_OK,
    _ADAPTER_REGISTRY,
    resolve_adapters,
)

HOME_URL = "https://www.ccgp-tianjin.gov.cn/"
DETAIL_URL = ("https://www.ccgp-tianjin.gov.cn/"
              "viewer.do?id=1093822394&ver=2")


class _Resp:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


class _FakeClient:
    def __init__(self, get_map=None, default: int = 404):
        self.get_map = get_map or {}
        self.default = default
        self.requested: list[str] = []

    async def get(self, url: str):
        self.requested.append(url)
        spec = self.get_map.get(url, self.default)
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
    domain_rate_limiter.set_interval("www.ccgp-tianjin.gov.cn", 0)
    yield


def _patch(monkeypatch, client: _FakeClient) -> None:
    monkeypatch.setattr(ra.httpx, "AsyncClient", _FakeClientFactory(client))


@pytest.mark.asyncio
async def test_adapter_happy_path(monkeypatch):
    client = _FakeClient(get_map={
        HOME_URL: (200, HOME_HTML),
        DETAIL_URL: (200, DETAIL_HTML),
    })
    _patch(monkeypatch, client)
    r = await TianjinAdapter().fetch_payloads(limit=5)
    assert r.status == STATUS_OK
    # 中标类 2 条中：第一条有详情夹具；第二条详情 404 → RuntimeError 跳过
    assert r.fetched == 1
    p = r.payloads[0]
    assert p["source_platform"] == "tianjin"
    assert p["win_company"] == "天津市示例科技有限公司"


@pytest.mark.asyncio
async def test_adapter_empty_home(monkeypatch):
    client = _FakeClient(get_map={HOME_URL: (200, "<html></html>")})
    _patch(monkeypatch, client)
    r = await TianjinAdapter().fetch_payloads(limit=5)
    assert r.status == STATUS_EMPTY and r.fetched == 0


@pytest.mark.asyncio
async def test_adapter_403_breaker(monkeypatch):
    client = _FakeClient(get_map={HOME_URL: 403}, default=403)
    _patch(monkeypatch, client)
    a = TianjinAdapter()
    r1 = await a.fetch_payloads(limit=5)
    assert r1.status == STATUS_BLOCKED_403
    before = len(client.requested)
    r2 = await a.fetch_payloads(limit=5)
    assert r2.status == STATUS_BLOCKED_403
    assert len(client.requested) == before  # 熔断后不再请求


# ---------------------------------------------------------------- 注册表守卫

def test_registry_contains_tianjin():
    """守卫：TianjinAdapter 已注册进实时链路（防 patch 接线被回退）。"""
    assert "tianjin" in _ADAPTER_REGISTRY
    assert "tianjin" in DEFAULT_SOURCES
    adapters = resolve_adapters(["tianjin"])
    assert len(adapters) == 1
    assert adapters[0].source == "tianjin"
    assert adapters[0].domain == "www.ccgp-tianjin.gov.cn"
