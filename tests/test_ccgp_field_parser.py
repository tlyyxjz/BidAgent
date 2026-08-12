# -*- coding: utf-8 -*-
"""
ccgp_field_parser 单元测试。
覆盖：正常用例 / 空值用例 / 边界用例 / 异常用例。
"""
import pytest
from datetime import datetime
from decimal import Decimal
from app.processors.ccgp_field_parser import (
    parse_tender_org,
    parse_location,
    parse_publish_time,
    parse_budget_amount,
    parse_win_amount,
    parse_notice_type,
    parse_bid_number,
    parse_fields,
)


# ========== 真实数据 fixture ==========
REAL_AWARD_TEXT = """## 大连海事大学体检设备采购（二次）中标公告
2026年08月05日 15:57 来源： 【打印】
## 公告概要：公告信息：采购项目名称大连海事大学体检设备采购（二次）品目
服务/其他服务
采购单位大连海事大学行政区域辽宁省公告时间2026年08月05日 15:57评审专家名单陈*（采购人代表）、蔡*、孙*、王*、李*总中标金额￥63.860000 万元（人民币）联系人及联系方式：项目联系人孟莎项目联系电话17804712320采购单位大连海事大学采购单位
一、项目编号：GHHX2026000062
二、项目名称：大连海事大学体检设备采购（二次）
三、中标（成交）信息
供应商名称：辽宁鸿源电子有限公司
中标（成交）金额：63.8600000（万元）
"""

REAL_TENDER_TEXT = """## 某采购项目招标公告
2026年08月04日 09:00 来源： 【打印】
## 公告概要：公告信息：采购项目名称某采购项目品目
货物/医疗设备
采购单位北京市第一医院行政区域北京市公告时间2026年08月04日 09:00预算金额：100.000000 万元（人民币）
项目编号：BJYY2026001234
"""

REAL_CORRECTION_TEXT = """## 某项目更正公告
2026年08月03日 14:30 来源： 【打印】
采购单位国家税务总局行政区域北京市公告时间2026年08月03日 14:30
一、项目编号：GHHX2026000062
原公告日期：2026年08月01日
"""


# ========== 正常用例 ==========
class TestParseTenderOrg:
    def test_award_text(self):
        assert parse_tender_org(REAL_AWARD_TEXT) == "大连海事大学"

    def test_tender_text(self):
        assert parse_tender_org(REAL_TENDER_TEXT) == "北京市第一医院"

    def test_correction_text(self):
        result = parse_tender_org(REAL_CORRECTION_TEXT)
        assert result is not None
        assert "国家税务总局" in result


class TestParseLocation:
    def test_liaoning(self):
        assert parse_location(REAL_AWARD_TEXT) == "辽宁省"

    def test_beijing(self):
        assert parse_location(REAL_TENDER_TEXT) == "北京市"


class TestParsePublishTime:
    def test_full_datetime(self):
        result = parse_publish_time(REAL_AWARD_TEXT)
        assert result == datetime(2026, 8, 5, 15, 57)

    def test_date_only(self):
        text = "公告时间2026年08月04日"
        result = parse_publish_time(text)
        assert result == datetime(2026, 8, 4)


class TestParseWinAmount:
    def test_yuan_format(self):
        result = parse_win_amount(REAL_AWARD_TEXT)
        assert result == Decimal("638600")  # 63.86万 → 638600元

    def test_explicit_amount(self):
        text = "中标（成交）金额：22.5320000（万元）"
        result = parse_win_amount(text)
        assert result == Decimal("225320")


class TestParseBudgetAmount:
    def test_budget_wan(self):
        result = parse_budget_amount(REAL_TENDER_TEXT)
        assert result == Decimal("1000000")  # 100万 → 1000000元

    def test_budget_yi(self):
        text = "预算金额：1.5 亿元"
        result = parse_budget_amount(text)
        assert result == Decimal("150000000")


class TestParseNoticeType:
    def test_award_from_title(self):
        assert parse_notice_type("某项目中标公告") == "award"

    def test_award_from_chengjiao(self):
        assert parse_notice_type("某项目成交公告") == "award"

    def test_tender_from_title(self):
        assert parse_notice_type("某项目招标公告") == "tender"

    def test_correction_from_title(self):
        assert parse_notice_type("某项目更正公告") == "correction"

    def test_correction_from_biangeng(self):
        assert parse_notice_type("某项目变更公告") == "correction"

    def test_cancel(self):
        assert parse_notice_type("某项目废标公告") == "cancel"


class TestParseBidNumber:
    def test_normal(self):
        result = parse_bid_number(REAL_AWARD_TEXT)
        assert result == "GHHX2026000062"

    def test_with_prefix(self):
        text = "一、项目编号：BJYY2026001234"
        result = parse_bid_number(text)
        assert result == "BJYY2026001234"


class TestParseFields:
    def test_full_parse(self):
        result = parse_fields("大连海事大学体检设备采购（二次）中标公告", REAL_AWARD_TEXT)
        assert result["tender_org"] == "大连海事大学"
        assert result["location"] == "辽宁省"
        assert result["notice_type"] == "award"
        assert result["bid_number"] == "GHHX2026000062"
        assert result["win_amount"] == Decimal("638600")
        assert "evidence" in result
        assert "tender_org" in result["evidence"]


# ========== 空值用例 ==========
class TestEmptyValues:
    def test_empty_string(self):
        assert parse_tender_org("") is None
        assert parse_location("") is None
        assert parse_publish_time("") is None
        assert parse_budget_amount("") is None
        assert parse_win_amount("") is None
        assert parse_notice_type("") is None
        assert parse_bid_number("") is None

    def test_none_input(self):
        assert parse_tender_org(None) is None  # type: ignore
        assert parse_location(None) is None  # type: ignore

    def test_parse_fields_empty(self):
        result = parse_fields("", "")
        assert result["tender_org"] is None
        assert result["location"] is None
        assert result["notice_type"] is None
        assert result["evidence"] == {}


# ========== 边界用例 ==========
class TestBoundary:
    def test_amount_with_comma(self):
        text = "总中标金额：1,000.500000 万元"
        result = parse_win_amount(text)
        assert result == Decimal("10005000")

    def test_amount_yuan_unit(self):
        text = "预算金额：50000元"
        result = parse_budget_amount(text)
        assert result == Decimal("50000")

    def test_zero_amount(self):
        text = "总中标金额：￥0.000000 万元（人民币）"
        result = parse_win_amount(text)
        assert result == Decimal("0")

    def test_publish_time_invalid_date(self):
        text = "2026年13月45日 25:61"
        result = parse_publish_time(text)
        assert result is None  # 无效日期返回None

    def test_no_keywords_text(self):
        text = "这是一段不含任何采购关键词的普通文本，只有一些无关紧要的内容。"
        assert parse_tender_org(text) is None
        assert parse_location(text) is None
        assert parse_bid_number(text) is None

    def test_multiple_amounts(self):
        """多个金额时应匹配第一个。"""
        text = "预算金额：100万元，实际金额：80万元"
        result = parse_budget_amount(text)
        assert result == Decimal("1000000")


# ========== 异常用例 ==========
class TestExceptions:
    def test_garbled_text(self):
        """乱码文本不崩溃。"""
        text = "！@#￥%……&*（）——+"
        result = parse_fields("乱码标题", text)
        assert isinstance(result, dict)
        assert result["tender_org"] is None

    def test_very_long_text(self):
        """超长文本不崩溃（真实格式：采购单位XXX行政区域）。"""
        org_name = "测试单位"
        text = f"采购单位{org_name}行政区域北京市" + "填充内容" * 50000
        result = parse_tender_org(text)
        assert result == "测试单位"

    def test_very_long_text_no_match(self):
        """超长无匹配文本不崩溃，返回None。"""
        text = "x" * 100000
        result = parse_tender_org(text)
        assert result is None

    def test_unicode_edge(self):
        """含特殊Unicode字符不崩溃。"""
        text = "采购单位：①测试单位②\n行政区域：北京市"
        result = parse_fields("测试", text)
        assert isinstance(result, dict)
