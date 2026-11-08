# -*- coding: utf-8 -*-
"""award 中标金额解析器守卫测试（大件一-③）。"""
from decimal import Decimal

from app.processors.award_amount_parser import parse_win_amount


def _check_span(text: str, r) -> None:
    assert text[r.span_start:r.span_end] == r.raw


def test_colon_plain_yuan():
    text = "三、中标（成交）信息供应商名称：XX公司中标（成交）金额：1234567.89元"
    r = parse_win_amount(text)
    assert r is not None
    assert r.value == Decimal("1234567.89")
    _check_span(text, r)


def test_colon_wan_unit_conversion():
    text = "成交金额：120.5万元"
    r = parse_win_amount(text)
    assert r is not None
    assert r.value == Decimal("1205000.0")


def test_paren_unit_header_style():
    # 表头带单位括号 + 冒号数字
    text = "中标（成交）金额(万元): 88.5 评审总得分"
    r = parse_win_amount(text)
    assert r is not None
    assert r.value == Decimal("885000")


def test_thousands_separator():
    text = "中标金额：1,234,567.00元"
    r = parse_win_amount(text)
    assert r is not None
    assert r.value == Decimal("1234567.00")


def test_reject_flattened_header_no_colon():
    # 压平表头无冒号 → 拒收（归因摸底时的误报结构）
    assert parse_win_amount("中标（成交）金额评审总得分1山东康都城市服务有限公司88.5") is None
    assert parse_win_amount("中标（成交金额）备注1") is None


def test_reject_no_anchor():
    assert parse_win_amount("预算金额为100万元") is None
    assert parse_win_amount(None) is None
    assert parse_win_amount("") is None


def test_reject_zero():
    assert parse_win_amount("中标金额：0元") is None


# ========== 入库管线接线（_build_tender 兜底） ==========

def test_build_tender_win_amount_fallback():
    from app.processors.tender_utils import _build_tender

    item = {
        "project_name": "某采购项目",
        "notice_type": "award",
        "core_content": "中标（成交）金额：120.5万元",
    }
    t = _build_tender(item, source_url="http://x", source_platform="ccgp", simhash_value=None)
    assert t.win_amount == Decimal("1205000.0")


def test_build_tender_win_amount_collector_priority():
    from app.processors.tender_utils import _build_tender

    item = {
        "project_name": "某采购项目",
        "win_amount": "99万元",
        "core_content": "中标金额：1万元",
    }
    t = _build_tender(item, source_url="http://x", source_platform="ccgp", simhash_value=None)
    assert t.win_amount == Decimal("990000")
