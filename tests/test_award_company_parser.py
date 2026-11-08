# -*- coding: utf-8 -*-
"""award 中标人解析器守卫测试（大件一-②）。

每个用例对应库内真实归因样本的结构变体；关键修复（剪噪/拒收/span）均需守卫。
"""
import pytest

from app.processors.award_company_parser import (
    WinCompanyResult,
    _strip_noise_prefix,
    parse_win_company,
)


def _check_span(text: str, r: WinCompanyResult) -> None:
    """span 必须能回原文定位，且切出来的文本就是抽取值（证据闭环）。"""
    assert text[r.span_start:r.span_end] == r.value


# ========== 冒号直给锚点 ==========

def test_colon_direct_win_unit():
    # 库内 id=3 真实结构：中标单位：沈阳朗鑫科技有限公司，综合得分：88.14分。
    text = "八、其它补充事宜\n中标单位：沈阳朗鑫科技有限公司，综合得分：88.14分。\n九、其他"
    r = parse_win_company(text)
    assert r is not None
    assert r.value == "沈阳朗鑫科技有限公司"
    assert r.anchor == "colon_direct"
    _check_span(text, r)


def test_colon_direct_win_person():
    # 库内 id=32：中标人：唐山冀禹港航技术咨询服务有限公司   评审得分：79.00
    text = "采购包01：河北测量作业水域1\n中标人：唐山冀禹港航技术咨询服务有限公司    评审得分：79.00"
    r = parse_win_company(text)
    assert r is not None
    assert r.value == "唐山冀禹港航技术咨询服务有限公司"
    _check_span(text, r)


def test_colon_direct_chengjiao_supplier():
    text = "三、成交信息 成交供应商名称：北京华信时代科技有限公司 成交金额：120万元"
    r = parse_win_company(text)
    assert r is not None
    assert r.value == "北京华信时代科技有限公司"
    _check_span(text, r)


# ========== 评审列锚点（压平表格） ==========

def test_review_anchor_flattened_table():
    # 库内 id=566 真实结构：表头压平后评审总得分后紧跟机构名
    text = ("供应商名称供应商地址中标（成交）金额执行标准评审总得分"
            "成都鲸弘医疗消毒供应中心有限公司成都市温江区xxx路123号")
    r = parse_win_company(text)
    assert r is not None
    assert r.value == "成都鲸弘医疗消毒供应中心有限公司"
    assert r.anchor == "review_score_anchor"
    _check_span(text, r)


def test_review_anchor_with_row_number():
    # 库内 id=475/477 真实结构：表头压平后评审列后数字为首行序号，首行即中标人
    text = "序号供应商名称中标（成交）金额评审总得分1山东康都城市服务有限公司88.5"
    r = parse_win_company(text)
    assert r is not None
    assert r.value == "山东康都城市服务有限公司"
    assert r.anchor == "review_score_anchor"
    _check_span(text, r)


def test_colon_supplier_name_direct():
    # 库内 id=466 真实结构：中标（成交）信息供应商名称：XXX供应商地址：XXX
    text = ("三、中标（成交）信息供应商名称：北京海智汇科技有限公司"
            "供应商地址：北京市海淀区某路1号")
    r = parse_win_company(text)
    assert r is not None
    assert r.value == "北京海智汇科技有限公司"
    assert r.anchor == "colon_supplier_name"
    _check_span(text, r)


def test_review_anchor_noise_prefix_trimmed():
    # 库内 id=12 真实结构：01包中标人XX公司评审总得分：75.14分 → 前缀粘连需剪噪
    text = "1、评审得分\n01包中标人知云时代（北京）教育科技有限公司评审总得分：75.14分"
    r = parse_win_company(text)
    assert r is not None
    assert r.value == "知云时代（北京）教育科技有限公司"
    assert "包中标人" not in r.value
    _check_span(text, r)


# ========== 拒收场景（宁可少给不可编造） ==========

def test_reject_no_org_suffix():
    # 无机构后缀收尾 → 拒收
    text = "中标供应商：张三明"
    assert parse_win_company(text) is None


def test_reject_header_itself():
    # 抓到的是表头自身 → 拒收
    text = "供应商名称供应商地址中标（成交）金额评审总得分"
    assert parse_win_company(text) is None


def test_reject_empty_and_none():
    assert parse_win_company(None) is None
    assert parse_win_company("") is None
    assert parse_win_company("这是一段没有任何结构的文本。") is None


# ========== 剪噪工具 ==========

def test_strip_noise_prefix_variants():
    assert _strip_noise_prefix("01包中标人ABC公司") == "ABC公司"
    assert _strip_noise_prefix("包中标供应商XYZ中心") == "XYZ中心"
    assert _strip_noise_prefix("ABC公司") == "ABC公司"  # 无前缀不动


# ========== 锚点优先级 ==========

def test_colon_wins_over_review_anchor():
    text = ("评审总得分某甲科技有限公司"
            "中标供应商：某乙（北京）信息技术有限公司")
    r = parse_win_company(text)
    assert r is not None
    assert r.anchor == "colon_direct"
    assert r.value == "某乙（北京）信息技术有限公司"


# ========== 入库管线接线（_build_tender 兜底） ==========

def test_build_tender_win_company_fallback():
    # 采集侧未给 win_company → 从正文确定性抽取兜底
    from app.processors.tender_utils import _build_tender

    item = {
        "project_name": "某采购项目",
        "notice_type": "award",
        "core_content": "三、中标（成交）信息供应商名称：北京海智汇科技有限公司供应商地址：某地址",
    }
    t = _build_tender(item, source_url="http://x", source_platform="ccgp", simhash_value=None)
    assert t.win_company == "北京海智汇科技有限公司"


def test_build_tender_win_company_collector_priority():
    # 采集侧已给 win_company → 优先用采集值，不走兜底
    from app.processors.tender_utils import _build_tender

    item = {
        "project_name": "某采购项目",
        "notice_type": "award",
        "win_company": "采集侧给定有限公司",
        "core_content": "中标供应商：另一家公司有限公司",
    }
    t = _build_tender(item, source_url="http://x", source_platform="ccgp", simhash_value=None)
    assert t.win_company == "采集侧给定有限公司"
