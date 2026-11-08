# -*- coding: utf-8 -*-
"""混合回填器规则裁判守卫测试（大件一-③）。

裁判原则：LLM 提议的值/证据必须原文精确定位，定位不上即拒收（宁缺毋滥）。
"""
from decimal import Decimal

from scripts.backfill_award_hybrid import _adjudicate

BODY = (
    "三、中标（成交）信息供应商名称：北京海智汇科技有限公司"
    "供应商地址：北京市海淀区某路1号中标（成交）金额：120.5万元"
)


def test_accept_company_and_amount():
    prop = {
        "win_company": "北京海智汇科技有限公司",
        "win_amount_raw": "120.5万元",
        "company_evidence": "供应商名称：北京海智汇科技有限公司",
        "amount_evidence": "中标（成交）金额：120.5万元",
    }
    v = _adjudicate(BODY, prop)
    assert v["company"] == "北京海智汇科技有限公司"
    assert v["amount"] == Decimal("1205000")
    assert v["reasons"] == []


def test_reject_hallucinated_company():
    # LLM 编造的公司名不在原文 → 拒收
    prop = {
        "win_company": "北京幻觉科技有限公司",
        "company_evidence": "供应商名称：北京幻觉科技有限公司",
    }
    v = _adjudicate(BODY, prop)
    assert v["company"] is None
    assert "company_not_exact_substring_or_no_org_tail" in v["reasons"]


def test_reject_company_without_evidence_location():
    # 公司名在原文但证据片段定位不上 → 拒收（证据闭环要求）
    prop = {
        "win_company": "北京海智汇科技有限公司",
        "company_evidence": "改写过的证据文本",
    }
    v = _adjudicate(BODY, prop)
    assert v["company"] is None
    assert "company_evidence_not_locatable" in v["reasons"]


def test_reject_company_no_org_tail():
    prop = {
        "win_company": "北京市海淀区某路1号",
        "company_evidence": "供应商地址：北京市海淀区某路1号",
    }
    v = _adjudicate(BODY, prop)
    assert v["company"] is None


def test_reject_amount_digits_not_in_body():
    prop = {
        "win_amount_raw": "999.9万元",
        "amount_evidence": "金额999.9万元",
    }
    v = _adjudicate(BODY, prop)
    assert v["amount"] is None
    assert "amount_digits_not_in_body" in v["reasons"]


def test_amount_unit_conversion_wan():
    body = "中标金额：2,345.67万元 供应商名称：某公司"
    prop = {
        "win_amount_raw": "2,345.67万元",
        "amount_evidence": "中标金额：2,345.67万元",
    }
    v = _adjudicate(body, prop)
    assert v["amount"] == Decimal("23456700")


def test_all_null_proposal():
    v = _adjudicate(BODY, {"win_company": None, "win_amount_raw": None})
    assert v["company"] is None
    assert v["amount"] is None


def test_company_label_prefix_and_consortium_first_segment():
    # 库内 id=836 真实结构：LLM 返回带标签前缀+联合体多值 → 取牵头供应商
    body = "牵头供应商：云南东熔建设工程有限公司 投标联合体：云南东熔建设工程有限公司、河北某公司"
    prop = {
        "win_company": "牵头供应商：云南东熔建设工程有限公司 投标联合体：云南东熔建设工程有限公司、河北某公司",
        "company_evidence": "牵头供应商：云南东熔建设工程有限公司",
    }
    v = _adjudicate(body, prop)
    assert v["company"] == "云南东熔建设工程有限公司"


def test_reject_amount_header_paren_unit():
    # 库内 id=36 真实结构：数字后紧跟括号单位是表头变体，单位归属不明 → 拒收
    body = "中标（成交）金额（万元）3055.0000（万元）评审总得分"
    prop = {
        "win_amount_raw": "3055.0000（万元）",
        "amount_evidence": "3055.0000（万元）",
    }
    v = _adjudicate(body, prop)
    assert v["amount"] is None
