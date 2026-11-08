"""湖北省政府采购网多源支持测试。

模板注册（选择器已对真实页面 2026-08-16 实测）+ 字段解析（真实详情页夹具）。
夹具来源：http://www.ccgp-hubei.gov.cn/ 中标(成交) tab 与一条真实成交公告。
"""
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.processors.ccgp_field_parser import (
    parse_bid_number,
    parse_publish_time,
    parse_tender_org,
    parse_win_amount,
)

FIX = Path(__file__).resolve().parent / "fixtures" / "hubei"


def _strip_tags(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", text)


@pytest.fixture(scope="module")
def detail_text() -> str:
    return _strip_tags((FIX / "hubei_detail.html").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 模板注册

def test_hubei_template_registered():
    from app.templates import get_template, list_templates
    assert "hubei" in list_templates()
    t = get_template("hubei")
    assert t.list_selector == "#area-1-4 ul.news-list-content li"
    assert t.selectors["detail_url"] == "a"
    assert t.selectors["publish_time"] == "span"
    assert t.max_pages == 1


def test_home_fixture_matches_template_selectors():
    raw = (FIX / "hubei_home.html").read_text(encoding="utf-8")
    items = re.findall(r"<li>[\s\S]*?</li>", raw)
    assert len(items) == 5
    hrefs = re.findall(r'href="(/notice/202608/notice_[0-9a-f]{32}\.html)"', raw)
    assert len(hrefs) == 5
    dates = re.findall(r"<span>(20\d{2}-\d{2}-\d{2})</span>", raw)
    assert len(dates) == 5


# ---------------------------------------------------------------- 字段解析（真实详情页）

def test_parse_bid_number_hubei(detail_text):
    assert parse_bid_number(detail_text) == "HBT-16124260-265952"


def test_parse_win_amount_hubei_halfwidth_parens(detail_text):
    """半角括号 (万元) 金额格式。"""
    assert parse_win_amount(detail_text) == Decimal("3824710.3")


def test_parse_tender_org_hubei_purchaser_info(detail_text):
    """'1、采购人信息 名 称： 湖北大学' 结构。"""
    assert parse_tender_org(detail_text) == "湖北大学"


def test_parse_publish_time_hubei_dash_format(detail_text):
    """'发布日期：2026-08-16 20:32' 横杠格式。"""
    assert parse_publish_time(detail_text) == datetime(2026, 8, 16, 20, 32)


def test_tender_org_no_false_positive_from_loose_pattern(detail_text):
    """'以采购人下达的开始日期为准' 不得被宽松模式误抓——特定模式必须优先命中。"""
    assert parse_tender_org(detail_text) == "湖北大学"
