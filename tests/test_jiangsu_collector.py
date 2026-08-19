# -*- coding: utf-8 -*-
"""江苏站采集器测试：首页链接解析 + 接口 JSON 解析（真实夹具）。"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import collect_jiangsu as cj  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "jiangsu"


@pytest.fixture(autouse=True)
def _no_rate_limit():
    """离线测试关闭同域限流（真实默认 8s）。"""
    from app.core.rate_limiter import domain_rate_limiter
    domain_rate_limiter.set_interval("www.ccgp-jiangsu.gov.cn", 0)
    yield


# ---------------------------------------------------------------- 首页链接解析

def test_parse_homepage_links():
    html = (FIX / "home_snippet.html").read_text(encoding="utf-8")
    links = cj.parse_homepage_links(html)
    assert len(links) >= 1
    for link in links:
        assert link["gglb"] in ("gkzb", "jzcs", "jztp")
        assert len(link["ggid"]) >= 20
        assert link["url"].startswith("http://www.ccgp-jiangsu.gov.cn/jiangsu/js_cggg/details.html")


# ---------------------------------------------------------------- 接口 JSON 解析

@pytest.fixture(scope="module")
def payload() -> dict:
    raw = (FIX / "api_sample.json").read_text(encoding="utf-8")
    return json.loads(raw.lstrip("\ufeff"))


def test_parse_notice_payload_fields(payload):
    p = cj.parse_notice_payload(payload)
    assert p["project_name"].startswith("2026年“苏心游”技术保障和推广服务项目")
    assert p["bid_number"] == "JSZC-320000-HWZX-G2026-0469"
    assert p["tender_org"] == "江苏省数字文化和智慧旅游发展中心"
    assert p["agency"] == "江苏海外集团国际工程咨询有限公司"
    assert p["source_platform"] == "jiangsu"
    assert "项目概况" in p["source_raw_text"]
    assert isinstance(p["simhash"], int)


def test_parse_notice_payload_empty_safe():
    p = cj.parse_notice_payload({"msg": "OK", "data": [], "cgxm": None})
    assert p["project_name"] == ""
    assert p["source_raw_text"] == ""


def test_parse_notice_payload_winner_from_summary(payload):
    """中标公告正文里的供应商名称能被确定性正则抽出。"""
    payload2 = dict(payload)
    payload2["data"] = [dict(payload["data"][0],
                             summary="供应商名称：江苏XX科技有限公司\n中标（成交）金额：80万元")]
    p = cj.parse_notice_payload(payload2)
    assert p["win_company"] == "江苏XX科技有限公司"
    assert p["win_amount"] is not None


# ---------------------------------------------------------------- 中标列表接口（D5）

class _FakeResp:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code


class _FakeClient:
    def __init__(self, body: str):
        self.body = body
        self.requested: list[str] = []

    async def get(self, url, **kw):
        self.requested.append(url)
        return _FakeResp(self.body)


_LIST_PAYLOAD = {"code": 200, "result": [
    {"title": "甲单位设备采购中标结果公告", "id": "a" * 32, "ggCode": "zbgg"},
    {"title": "乙单位服务采购公告", "id": "b" * 32, "ggCode": "gkzb"},
    {"title": "丙单位系统成交公告", "id": "c" * 32, "ggCode": "zbgg"},
]}


def test_fetch_award_links_filters_non_award():
    """列表条目按标题防御性过滤：非中标/成交/结果类丢弃。"""
    client = _FakeClient(json.dumps(_LIST_PAYLOAD))
    links = asyncio.run(cj.fetch_award_links(client, 5))
    assert [l["ggid"] for l in links] == ["a" * 32, "c" * 32]
    assert "gglb=zbgg" in links[0]["url"] and f"ggid={'a' * 32}" in links[0]["url"]


def test_fetch_award_links_respects_limit():
    client = _FakeClient(json.dumps(_LIST_PAYLOAD))
    links = asyncio.run(cj.fetch_award_links(client, 1))
    assert len(links) == 1 and links[0]["ggid"] == "a" * 32


def test_fetch_award_links_bad_code_raises():
    """接口非 200 code 报错（宁可少给、不可编造）。"""
    client = _FakeClient(json.dumps({"code": 500, "result": []}))
    with pytest.raises(RuntimeError, match="code=500"):
        asyncio.run(cj.fetch_award_links(client, 5))
