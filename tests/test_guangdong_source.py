# -*- coding: utf-8 -*-
"""广东省政府采购网采集器守卫测试（省级批量·浏览器通道站）。

覆盖：列表解析去重/脏块、非结果类过滤、采购结果表抽取（多包求和/
单包/评分表不命中）、金额边界、payload 契约与降级、适配器
happy/blocked、注册表守卫。全部离线，零网络。
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import collect_guangdong as cg  # noqa: E402


def _fake_list_json(rows: list[dict]) -> dict:
    return {"code": "200", "msg": "操作成功",
            "data": {"total": len(rows), "rows": rows}}


def _row(rid="ID-A", title="某某项目结果公告", **kw):
    base = {"id": rid, "title": title, "noticeTime": "2026-08-21 12:23:41",
            "budget": "1890000.0000", "purchaser": "广东省某局",
            "agency": "某代理公司", "purchaseManner": "6",
            "purchaseMannerName": None, "regionName": "广东省省本级",
            "openTenderCode": "GZYL26FC073463", "noticeType": "001026"}
    base.update(kw)
    return base


def _supplier_table(rows: list[tuple[str, str]]) -> str:
    """构造"三、采购结果"表（表头=供应商名称/供应商地址/中标（成交）金额）。"""
    body = "".join(
        f"<tr><td>{s}</td><td>某地址</td>"
        f"<td class=\"alignright\"><span>{a}</span></td></tr>"
        for s, a in rows)
    return ("<table><thead><tr><th>供应商名称</th><th>供应商地址</th>"
            "<th>中标（成交）金额</th></tr></thead>"
            f"<tbody>{body}</tbody></table>")


_SCORE_TABLE = ("<table><thead><tr><th>供应商</th><th>综合得分</th>"
                "</tr></thead><tbody><tr><td>甲公司</td>"
                "<td>97.33</td></tr></tbody></table>")


def _fake_detail_json(content: str) -> dict:
    return {"code": "200", "msg": "操作成功", "data": {
        "title": "某某项目结果公告", "noticeTime": "2026-08-21 12:23:41",
        "openTenderCode": "GZYL26FC073463",
        "content": "<h4>二、项目名称：测试项目</h4>" + content}}


# ---------------------------------------------------------------- 列表解析

def test_parse_list_basic():
    items = cg.parse_list(_fake_list_json([_row(), _row(rid="ID-B")]))
    assert len(items) == 2
    assert items[0]["id"] == "ID-A"
    assert items[0]["purchaser"] == "广东省某局"
    assert items[0]["bid_code"] == "GZYL26FC073463"


def test_parse_list_dedup_and_dirty():
    items = cg.parse_list(_fake_list_json([
        _row(), _row(),                          # 重复 id
        _row(rid="C", title="   "),             # 无标题脏块
        {"id": None, "title": "无ID"},          # 无 ID 脏块
    ]))
    assert len(items) == 1


def test_parse_list_bad_shape():
    assert cg.parse_list({"code": "500"}) == []
    assert cg.parse_list(None) == []  # type: ignore[arg-type]


# ---------------------------------------------------------------- 过滤

def test_filter_excludes_non_result():
    raw = [_row(),
           _row(rid="F", title="某某项目废标公告"),
           _row(rid="T", title="某某项目终止公告"),
           _row(rid="G", title="某某项目更正公告"),
           _row(rid="N", title="某某项目招标公告")]
    kept = cg.filter_result_items(cg.parse_list(_fake_list_json(raw)))
    assert len(kept) == 1
    assert kept[0]["id"] == "ID-A"


def test_filter_all_types_passthrough():
    raw = [_row(rid="F", title="废标公告")]
    items = cg.parse_list(_fake_list_json(raw))
    assert cg.filter_result_items(items, all_types=True) == items


# ---------------------------------------------------------------- 结果表抽取

def test_parse_result_pairs_single():
    pairs = cg.parse_result_pairs(_supplier_table([("广东测试公司", "1,420,000.00元")]))
    assert pairs == [("广东测试公司", Decimal("1420000.00"))]


def test_parse_result_pairs_multi_and_score_table_ignored():
    content = (_supplier_table([("甲公司", "540,000.00元"),
                                ("乙公司", "450,000.00元")])
               + _SCORE_TABLE)
    pairs = cg.parse_result_pairs(content)
    assert len(pairs) == 2
    assert sum(a for _, a in pairs) == Decimal("990000")


def test_parse_amount_cell_edges():
    assert cg.parse_amount_cell("540,000.00元") == Decimal("540000.00")
    assert cg.parse_amount_cell("1234567.89") == Decimal("1234567.89")
    assert cg.parse_amount_cell("无") is None
    assert cg.parse_amount_cell("") is None


def test_parse_result_pairs_rate_cell_not_amount():
    """真机案例：金额列为"服务费率 2.70%"时不入金额（宁可缺不可编），
    供应商仍采信；求和条件不满足 → win_amount=None。"""
    body = ("<tr><td>某进出口公司</td><td>某地址</td>"
            "<td><span>服务费率2.70%</span></td></tr>")
    content = ("<table><thead><tr><th>供应商名称</th><th>供应商地址</th>"
               "<th>中标（成交）金额</th></tr></thead>"
               f"<tbody>{body}</tbody></table>")
    pairs = cg.parse_result_pairs(content)
    assert pairs == [("某进出口公司", None)]
    d = cg.parse_detail({"code": "200", "data": {
        "noticeTime": "2026-08-21 11:43:35", "openTenderCode": "M4400",
        "content": content}})
    assert d["win_company"] == "某进出口公司"
    assert d["win_amount"] is None


# ---------------------------------------------------------------- 详情解析

def test_parse_detail_full():
    d = cg.parse_detail(_fake_detail_json(
        _supplier_table([("甲公司", "540,000.00元"),
                         ("乙公司", "450,000.00元")])))
    assert d["win_company"] == "甲公司、乙公司"
    assert d["win_amount"] == Decimal("990000")
    assert d["bid_number"] == "GZYL26FC073463"
    assert d["project_name"] == "测试项目"
    assert d["publish_time"] is not None and d["publish_time"].year == 2026
    assert d["plain_text"] and "<" not in d["plain_text"]


def test_parse_detail_none_degrade():
    d = cg.parse_detail(None)
    assert d["win_company"] is None and d["win_amount"] is None
    assert d["publish_time"] is None
    assert cg.parse_detail({"code": "500"})["win_company"] is None


# ---------------------------------------------------------------- payload

def test_build_payload_contract():
    rec = cg.parse_list(_fake_list_json([_row()]))[0]
    detail = cg.parse_detail(_fake_detail_json(
        _supplier_table([("广东测试公司", "1,420,000.00元")])))
    p = cg.build_payload(rec, detail)
    assert p["source_platform"] == "guangdong"
    assert p["source_url"] == (
        "https://gdgpo.czt.gd.gov.cn/gpcms/rest/web/v2/info/getInfoById?id=ID-A")
    assert p["win_company"] == "广东测试公司"
    assert p["win_amount"] == Decimal("1420000.00")
    assert p["tender_org"] == "广东省某局"
    assert p["notice_type"] == "award"
    assert len(p["content_sha256"]) == 64
    assert len(p["raw_text_sha256"]) == 64
    assert p["publish_time"] == detail["publish_time"]


def test_build_payload_detail_missing_fallback():
    rec = cg.parse_list(_fake_list_json([_row()]))[0]
    p = cg.build_payload(rec, None)
    assert p["win_amount"] is None
    assert p["win_company"] is None
    assert p["bid_number"] == rec["bid_code"]       # 回落列表编号
    assert p["project_name"] == rec["title"]        # 回落列表标题
    assert len(p["content_sha256"]) == 64


# ---------------------------------------------------------------- 适配器

class _FakeGDModule:
    Collect403 = cg.Collect403

    def __init__(self, payloads):
        self._payloads = payloads

    async def collect_payloads(self, limit, all_types=False):
        return self._payloads[:limit]


@pytest.mark.asyncio
async def test_adapter_happy(monkeypatch):
    from app.services import realtime_adapters as ra
    fake = _FakeGDModule([{"source_platform": "guangdong", "project_name": "X"}])
    monkeypatch.setattr(ra, "_load_script_module", lambda name: fake)
    adapter = ra.GuangdongAdapter()
    out = await adapter._fetch_and_build(3)
    assert len(out) == 1
    assert out[0]["source_platform"] == "guangdong"


@pytest.mark.asyncio
async def test_adapter_blocked_raises(monkeypatch):
    from app.services import realtime_adapters as ra

    class _BlockedModule:
        Collect403 = cg.Collect403

        async def collect_payloads(self, limit, all_types=False):
            raise cg.Collect403("403 即停")

    monkeypatch.setattr(ra, "_load_script_module", lambda name: _BlockedModule())
    adapter = ra.GuangdongAdapter()
    with pytest.raises(ra.SourceBlockedError):
        await adapter._fetch_and_build(3)


# ---------------------------------------------------------------- 注册表

def test_registry_contains_guangdong():
    from app.services.realtime_sources import _ADAPTER_REGISTRY, DEFAULT_SOURCES
    assert "guangdong" in _ADAPTER_REGISTRY
    assert "guangdong" in DEFAULT_SOURCES


def test_registry_adapter_count_guard():
    from app.services.realtime_sources import resolve_adapters
    adapters = resolve_adapters()
    assert len(adapters) == 9
