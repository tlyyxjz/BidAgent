# -*- coding: utf-8 -*-
"""浙江省政府采购网采集器守卫测试（省级批量·浏览器通道站）。

覆盖：列表解析去重/脏块、废标过滤、模板 HTML class 标记抽取、
金额边界、payload 契约与降级、适配器 happy/blocked、注册表守卫。
全部离线，零网络。
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import collect_zhejiang as ch  # noqa: E402


def _fake_list_json(items: list[dict]) -> dict:
    return {"success": True, "result": {"data": {"total": len(items),
                                                  "data": items}}}


def _item(aid="AAA==", title="关于某某项目中标(成交)结果公告",
          path="中标（成交）结果公告", **kw):
    base = {"articleId": aid, "title": title, "pathName": path,
            "publishDate": 1787281683000, "districtName": "杭州市",
            "author": "某代理公司", "projectName": "某某项目",
            "projectCode": "330100X", "procurementMethod": "公开招标",
            "purchaseName": "杭州市某局", "supplierName": None,
            "totalContractAmount": None}
    base.update(kw)
    return base


DETAIL_HTML = (
    '<style>x{}</style><div><p>一、项目编号：</p>'
    '<span>330100X</span>'
    '<table><tr><td class="code-summaryPrice">总价：539000（元）</td>'
    '<td class="code-winningSupplierName">杭州测试科技有限公司</td>'
    '</tr></table><p>三、中标（成交）信息</p></div>')


def _fake_detail_json(content=DETAIL_HTML):
    return {"success": True, "result": {"data": {
        "title": "某某项目中标(成交)结果公告", "articleId": "AAA==",
        "projectCode": "330100X", "projectName": "某某项目",
        "publishDate": 1787281683000, "content": content}}}


# ---------------------------------------------------------------- 列表解析

def test_parse_list_items_basic():
    items = ch.parse_list_items(_fake_list_json([_item(), _item(aid="BBB==")]))
    assert len(items) == 2
    assert items[0]["article_id"] == "AAA=="
    assert items[0]["purchase_name"] == "杭州市某局"


def test_parse_list_items_dedup_and_dirty():
    items = ch.parse_list_items(_fake_list_json([
        _item(), _item(),                                # 重复 articleId
        _item(aid="C", title="   "),                     # 无标题脏块
        {"articleId": None, "title": "无ID"},            # 无 ID 脏块
    ]))
    assert len(items) == 1


def test_parse_list_items_bad_shape():
    assert ch.parse_list_items({"success": False}) == []
    assert ch.parse_list_items(None) == []  # type: ignore[arg-type]


# ---------------------------------------------------------------- 过滤

def test_filter_excludes_feibiao():
    raw = [_item(), _item(aid="F", title="某某项目废标公告", path="废标公告")]
    items = ch.parse_list_items(_fake_list_json(raw))
    kept = ch.filter_result_items(items)
    assert len(kept) == 1
    assert kept[0]["article_id"] == "AAA=="


def test_filter_all_types_passthrough():
    raw = [_item(aid="F", title="废标公告", path="废标公告")]
    items = ch.parse_list_items(_fake_list_json(raw))
    assert ch.filter_result_items(items, all_types=True) == items


# ---------------------------------------------------------------- 详情解析

def test_parse_detail_class_markers():
    d = ch.parse_detail(_fake_detail_json())
    assert d["win_company"] == "杭州测试科技有限公司"
    assert d["win_amount"] == Decimal("539000")
    assert d["bid_number"] == "330100X"
    assert d["publish_time"] is not None
    assert "中标（成交）信息" in d["plain_text"]
    assert "<" not in d["plain_text"]


def test_parse_detail_none_degrade():
    d = ch.parse_detail(None)
    assert d["win_company"] is None
    assert d["win_amount"] is None
    assert d["publish_time"] is None


def test_parse_amount_cell_edges():
    assert ch.parse_amount_cell("总价：539000（元）") == Decimal("539000")
    assert ch.parse_amount_cell("1,234,567.89元") == Decimal("1234567.89")
    assert ch.parse_amount_cell("无") is None
    assert ch.parse_amount_cell("") is None


# ---------------------------------------------------------------- payload

def test_build_payload_contract():
    rec = ch.parse_list_items(_fake_list_json([_item()]))[0]
    detail = ch.parse_detail(_fake_detail_json())
    p = ch.build_payload(rec, detail)
    assert p["source_platform"] == "zhejiang"
    assert p["source_url"].startswith(
        "http://www.ccgp-zhejiang.gov.cn/site/detail?articleId=")
    assert p["win_company"] == "杭州测试科技有限公司"
    assert p["win_amount"] == Decimal("539000")
    assert p["tender_org"] == "杭州市某局"
    assert len(p["content_sha256"]) == 64
    assert len(p["raw_text_sha256"]) == 64
    assert p["publish_time"] == detail["publish_time"]


def test_build_payload_detail_missing_fallback():
    rec = ch.parse_list_items(_fake_list_json([_item()]))[0]
    p = ch.build_payload(rec, None)
    assert p["win_amount"] is None
    assert p["publish_time"] is not None  # 回落列表毫秒时间戳
    assert len(p["content_sha256"]) == 64


# ---------------------------------------------------------------- 适配器

class _FakeZJModule:
    CollectBlocked = ch.CollectBlocked

    def __init__(self, payloads):
        self._payloads = payloads

    async def collect_payloads(self, limit, interval):
        return self._payloads[:limit]


@pytest.mark.asyncio
async def test_adapter_happy(monkeypatch):
    from app.services import realtime_adapters as ra
    fake = _FakeZJModule([{"source_platform": "zhejiang", "project_name": "X"}])
    monkeypatch.setattr(ra, "_load_script_module", lambda name: fake)
    adapter = ra.ZhejiangAdapter()
    out = await adapter._fetch_and_build(3)
    assert len(out) == 1
    assert out[0]["source_platform"] == "zhejiang"


@pytest.mark.asyncio
async def test_adapter_blocked_raises(monkeypatch):
    from app.services import realtime_adapters as ra

    class _BlockedModule:
        CollectBlocked = ch.CollectBlocked

        async def collect_payloads(self, limit, interval):
            raise ch.CollectBlocked("浏览器通道仍被 WAF 挑战拦截，即停")

    monkeypatch.setattr(ra, "_load_script_module", lambda name: _BlockedModule())
    adapter = ra.ZhejiangAdapter()
    with pytest.raises(ra.SourceBlockedError):
        await adapter._fetch_and_build(3)


# ---------------------------------------------------------------- 注册表

def test_registry_contains_zhejiang():
    from app.services.realtime_sources import _ADAPTER_REGISTRY, DEFAULT_SOURCES
    assert "zhejiang" in _ADAPTER_REGISTRY
    assert "zhejiang" in DEFAULT_SOURCES


def test_registry_adapter_count_guard():
    from app.services.realtime_sources import resolve_adapters
    adapters = resolve_adapters()
    assert len(adapters) == 9
