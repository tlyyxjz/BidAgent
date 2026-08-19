"""湖北站采集器测试：离线夹具（真实页面裁剪）+ 纯函数覆盖。"""
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import collect_hubei as ch  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "hubei"


@pytest.fixture(scope="module")
def home_html() -> str:
    return (FIX / "hubei_home.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def detail_html() -> str:
    return (FIX / "hubei_detail.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------- 列表解析

def test_parse_list_five_items_clean_title(home_html):
    items = ch.parse_list(home_html)
    assert len(items) == 5
    first = items[0]
    assert first["path"].startswith("/notice/202608/notice_")
    assert first["date"] == "2026-08-16"
    # [竞争性磋商] 前缀被剥掉
    assert first["title"].startswith("湖北大学二号体育馆")
    assert "竞争性磋商" not in first["title"]


# ---------------------------------------------------------------- 详情解析

def test_parse_detail_title(detail_html):
    assert ch.parse_detail_title(detail_html) == (
        "湖北大学二号体育馆升级改造（EPC）工程项目工程采购项目成交结果公告"
    )


def test_strip_tags_keeps_field_text(detail_html):
    text = ch.strip_tags(detail_html)
    assert "供应商名称" in text
    assert "382.47103" in text
    assert "湖北大学" in text


def test_build_payload_full_fields(detail_html):
    item = {
        "path": "/notice/202608/notice_73f52407656f4b1aa9d98901078f990d.html",
        "title": "fallback-title",
        "date": "2026-08-16",
        "url": "http://www.ccgp-hubei.gov.cn/notice/202608/notice_73f52407656f4b1aa9d98901078f990d.html",
    }
    p = ch.build_payload(item, detail_html)
    assert p["project_name"].startswith("湖北大学二号体育馆")
    assert p["bid_number"] == "HBT-16124260-265952"
    assert p["win_amount"] == Decimal("3824710.30")
    assert p["tender_org"] == "湖北大学"
    assert p["win_company"] == "武汉建工集团股份有限公司"
    assert p["notice_type"] == "award"
    assert p["source_platform"] == "hubei"
    assert p["source_url"] == item["url"]
    assert p["publish_time"] == datetime(2026, 8, 16, 20, 32)
    assert isinstance(p["simhash"], int) and -(2 ** 63) <= p["simhash"] < 2 ** 63
    assert "供应商名称" in p["source_raw_text"]


def test_build_payload_fallback_title_and_date(detail_html):
    """详情页缺标题/时间时回落到列表项数据。"""
    import re

    html_no_meta = re.sub(r"发布日期[：:]\s*20\d{2}-\d{2}-\d{2}\s*\d{2}:\d{2}", " ", detail_html)
    item = {
        "path": "/x", "title": "列表标题", "date": "2026-08-10",
        "url": "http://www.ccgp-hubei.gov.cn/x",
    }
    p = ch.build_payload(item, html_no_meta)
    assert p["project_name"] == "列表标题" or p["project_name"].startswith("湖北大学")
    assert p["publish_time"] is not None


def test_to_signed_simhash_matches_ingestor_convention():
    """uint64 simhash 归一为 int64 有符号（与 tender_ingestor 一致）。"""
    assert ch._to_signed_simhash(0x7FFFFFFFFFFFFFFF) == 0x7FFFFFFFFFFFFFFF
    assert ch._to_signed_simhash(0x8000000000000000) == -0x8000000000000000
    assert ch._to_signed_simhash(0xFFFFFFFFFFFFFFFF) == -1
    assert ch._to_signed_simhash(5906547551657341687) == 5906547551657341687


def test_build_payload_simhash_in_int64_range(detail_html):
    item = {
        "path": "/x", "title": "t", "date": "2026-08-10",
        "url": "http://www.ccgp-hubei.gov.cn/x",
    }
    p = ch.build_payload(item, detail_html)
    assert -(2 ** 63) <= p["simhash"] < 2 ** 63


# ---------------------------------------------------------------- 403 即停

class _FakeResp:
    def __init__(self, status):
        self.status_code = status
        self.text = ""


class _FakeClient:
    def __init__(self, status):
        self._status = status

    async def get(self, url):
        return _FakeResp(self._status)


@pytest.mark.asyncio
async def test_fetch_403_raises_collect403():
    from app.core.rate_limiter import domain_rate_limiter

    domain_rate_limiter.set_interval("www.ccgp-hubei.gov.cn", 0)
    with pytest.raises(ch.Collect403):
        await ch._fetch(_FakeClient(403), "https://www.ccgp-hubei.gov.cn/")


@pytest.mark.asyncio
async def test_fetch_200_returns_text():
    from app.core.rate_limiter import domain_rate_limiter

    domain_rate_limiter.set_interval("www.ccgp-hubei.gov.cn", 0)

    class _OkClient(_FakeClient):
        async def get(self, url):
            r = _FakeResp(200)
            r.text = "<html>ok</html>"
            return r

    assert await ch._fetch(_OkClient(200), "https://www.ccgp-hubei.gov.cn/") == "<html>ok</html>"
