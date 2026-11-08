"""守卫测试：_build_tender 不得丢弃 win_amount/win_company（D2 前置修复）。

背景：4 个分站采集器 build_payload 均输出 win_amount/win_company，
原 pick 链缺失导致入库时静默丢弃 → 湖北/江苏/云南/山东中标数据丢失。
"""

from __future__ import annotations

from decimal import Decimal

from app.processors.tender_utils import _build_tender


def _item(**overrides):
    base = {
        "project_name": "示例项目",
        "source_url": "https://www.ccgp-hubei.gov.cn/x",
        "win_amount": Decimal("1234567"),
        "win_company": "示例科技有限公司",
    }
    base.update(overrides)
    return base


def test_win_amount_kept_from_decimal():
    t = _build_tender(_item(), "", "hubei", None)
    assert t.win_amount == Decimal("1234567")


def test_win_company_kept():
    t = _build_tender(_item(), "", "hubei", None)
    assert t.win_company == "示例科技有限公司"


def test_win_amount_alias_and_unit_parsing():
    t = _build_tender(_item(win_amount=None, **{"中标金额": "123.45万元"}),
                      "", "yunnan", None)
    assert t.win_amount == Decimal("1234500")


def test_win_company_aliases():
    for alias in ("中标供应商", "中标人"):
        t = _build_tender(_item(win_company=None, **{alias: "某某公司"}),
                          "", "shandong", None)
        assert t.win_company == "某某公司"


def test_win_fields_none_when_absent():
    t = _build_tender(_item(win_amount=None, win_company=None),
                      "", "ccgp", None)
    assert t.win_amount is None
    assert t.win_company is None


def test_win_company_truncated_to_300():
    t = _build_tender(_item(win_company="甲" * 500), "", "hubei", None)
    assert t.win_company is not None
    assert len(t.win_company) == 300


def test_budget_amount_still_works_no_regression():
    t = _build_tender(_item(budget_amount="50万元"), "", "hubei", None)
    assert t.budget_amount == Decimal("500000")
