"""云南省政府采购网采集器离线夹具测试（collect_yunnan.py）。"""
import json
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import collect_yunnan as cy  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "yunnan"

# 列表 JSON 夹具：含正常记录、占位脏记录（id=sddfucggg / FINISHDAY="/"）、无效日期
LIST_PAYLOAD = [
    {
        "BULLETIN_ID": "-67668ce6.1a00e7bb429.33ac",
        "BULLETINTITLE": "云南中医药大学医学信息工程智能化协同平台建设项目中标结果公告",
        "FINISHDAY": "2026-08-18",
        "BULLETINCLASS": "bxlx007",
    },
    {
        "BULLETIN_ID": "sddfucggg",  # 占位脏记录：非 ID 形态
        "BULLETINTITLE": "云南省采购成交纪录",
        "FINISHDAY": "/",
        "BULLETINCLASS": "bxlx007",
    },
    {
        "BULLETIN_ID": "abc123_ok.id",
        "BULLETINTITLE": "  某单位办公设备   采购中标公告  ",
        "FINISHDAY": "2026-08-17",
        "BULLETINCLASS": "bxlx007",
    },
    {
        "BULLETIN_ID": "bad_date_001",
        "BULLETINTITLE": "无效日期占位记录",
        "FINISHDAY": "2026/08/17",  # 非法日期格式 → 整条跳过
        "BULLETINCLASS": "bxlx007",
    },
    {"BULLETIN_ID": "", "BULLETINTITLE": "空ID记录", "FINISHDAY": "2026-08-01"},
]


@pytest.fixture(scope="module")
def detail_html() -> str:
    return (FIX / "yunnan_detail.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------- 列表解析

def test_parse_list_json_filters_dirty_rows():
    items = cy.parse_list_json(LIST_PAYLOAD)
    assert len(items) == 2  # 脏 ID/无效日期/空 ID 均被整条过滤
    assert items[0]["bulletin_id"] == "-67668ce6.1a00e7bb429.33ac"
    assert items[0]["date"] == "2026-08-18"
    # 标题空白归一
    assert items[1]["title"] == "某单位办公设备 采购中标公告"
    assert items[1]["date"] == "2026-08-17"


def test_parse_list_json_wrapped_form():
    wrapped = {"code": "200", "data": LIST_PAYLOAD}
    assert cy.parse_list_json(wrapped) == cy.parse_list_json(LIST_PAYLOAD)
    assert cy.parse_list_json({"code": "200"}) == []
    assert cy.parse_list_json("not-a-list") == []


# ---------------------------------------------------------------- 详情解析

def test_parse_detail_title(detail_html):
    assert cy.parse_detail_title(detail_html) == (
        "云南中医药大学医学信息工程智能化协同平台建设项目中标结果公告"
    )
    assert cy.parse_detail_title("<div>无 title 标签</div>") is None


def test_strip_tags_keeps_field_text(detail_html):
    text = cy.strip_tags(detail_html)
    assert "中标供应商" in text
    assert "253.5912" in text
    assert "云南中医药大学" in text
    assert "<td" not in text


def test_extract_winners_from_table_kv_and_header(detail_html):
    """键值行（中标供应商）与结果表（供应商名称列）两种形态均命中且去重。"""
    winners = cy.extract_winners_from_table(detail_html)
    assert winners == ["昆明道实科技有限公司"]
    # 纯表头形态
    html = ("<table><tr><th>标段</th><th>供应商名称</th></tr>"
            "<tr><td>1</td><td>云南某某科技有限公司</td></tr>"
            "<tr><td>2</td><td>123.45</td></tr></table>")
    assert cy.extract_winners_from_table(html) == ["云南某某科技有限公司"]
    assert cy.extract_winners_from_table("无表格") == []


def test_build_payload_full_fields(detail_html):
    item = {
        "bulletin_id": "-67668ce6.1a00e7bb429.33ac",
        "title": "云南中医药大学医学信息工程智能化协同平台建设项目中标结果公告",
        "date": "2026-08-18",
    }
    p = cy.build_payload(item, detail_html)
    assert p["project_name"].startswith("云南中医药大学")
    assert p["bid_number"] == "YNZC2026-G1-04457-YNZZ-0450"
    assert p["win_amount"] == Decimal("2535912")
    assert p["tender_org"] == "云南中医药大学"
    assert p["win_company"] == "昆明道实科技有限公司"
    assert p["notice_type"] == "award"
    assert p["source_platform"] == "yunnan"
    assert p["source_url"] == (
        "http://www.ccgp-yunnan.gov.cn/ggInfo.html"
        "?bulletin_id=-67668ce6.1a00e7bb429.33ac"
    )
    # 云南正文无"发布日期"→ 回落列表 FINISHDAY
    assert p["publish_time"] == datetime(2026, 8, 18)
    assert isinstance(p["simhash"], int) and -(2 ** 63) <= p["simhash"] < 2 ** 63
    assert "中标供应商" in p["source_raw_text"]
    assert len(p["core_content"]) <= 2000


def test_build_payload_publish_time_none_when_no_date(detail_html):
    item = {"bulletin_id": "x1", "title": "t", "date": ""}
    p = cy.build_payload(item, detail_html)
    assert p["publish_time"] is None


def test_to_signed_simhash_matches_ingestor_convention():
    """uint64 simhash 归一为 int64 有符号（与 tender_ingestor 一致）。"""
    assert cy._to_signed_simhash(0x7FFFFFFFFFFFFFFF) == 0x7FFFFFFFFFFFFFFF
    assert cy._to_signed_simhash(0x8000000000000000) == -0x8000000000000000
    assert cy._to_signed_simhash(0xFFFFFFFFFFFFFFFF) == -1


# ---------------------------------------------------------------- 403 即停

class _FakeResp:
    def __init__(self, status, text=""):
        self.status_code = status
        self.text = text


class _FakeClient:
    def __init__(self, status):
        self._status = status

    async def get(self, url):
        return _FakeResp(self._status)

    async def post(self, url, data=None, headers=None):
        return _FakeResp(self._status)


@pytest.mark.asyncio
async def test_fetch_403_raises_collect403():
    from app.core.rate_limiter import domain_rate_limiter

    domain_rate_limiter.set_interval("www.ccgp-yunnan.gov.cn", 0)
    with pytest.raises(cy.Collect403):
        await cy._fetch(_FakeClient(403), "http://www.ccgp-yunnan.gov.cn/ggInfo.html")


@pytest.mark.asyncio
async def test_fetch_200_returns_text():
    from app.core.rate_limiter import domain_rate_limiter

    domain_rate_limiter.set_interval("www.ccgp-yunnan.gov.cn", 0)

    class _OkClient(_FakeClient):
        async def get(self, url):
            return _FakeResp(200, "<html>ok</html>")

    assert await cy._fetch(_OkClient(200), "http://www.ccgp-yunnan.gov.cn/x") == "<html>ok</html>"


@pytest.mark.asyncio
async def test_fetch_list_403_raises_collect403():
    from app.core.rate_limiter import domain_rate_limiter

    domain_rate_limiter.set_interval("www.ccgp-yunnan.gov.cn", 0)
    with pytest.raises(cy.Collect403):
        await cy._fetch_list(_FakeClient(403))


@pytest.mark.asyncio
async def test_fetch_list_parses_json():
    from app.core.rate_limiter import domain_rate_limiter

    domain_rate_limiter.set_interval("www.ccgp-yunnan.gov.cn", 0)

    class _ListClient(_FakeClient):
        async def post(self, url, data=None, headers=None):
            return _FakeResp(200, json.dumps(LIST_PAYLOAD, ensure_ascii=False))

    items = await cy._fetch_list(_ListClient(200))
    assert len(items) == 2
    assert items[0]["bulletin_id"] == "-67668ce6.1a00e7bb429.33ac"
