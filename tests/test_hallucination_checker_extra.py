"""hallucination_checker.py 补充测试：提升覆盖率 93% -> 95%+.

覆盖未覆盖行: 77, 122, 145, 158, 184-186, 216, 237-238

策略:
- _normalize_amount 小数金额返回 (行 77)
- CheckReport.to_dict 序列化 (行 122)
- extract_facts 重复事实跳过 (行 145)
- _fact_in_source 空原文/日期不匹配/其他类别不匹配 (行 158, 184-186)
- check_content 无事实提取时通过 (行 216)
- check_content 非严格模式非严格类别视为通过 (行 237-238)
"""
from __future__ import annotations

import pytest

from app.processors.hallucination_checker import (
    CheckReport,
    Fact,
    _normalize_amount,
    _normalize_date,
    _fact_in_source,
    check_content,
    check_items,
    extract_facts,
)


# ============================================================
# 测试套件 1: _normalize_amount 小数返回 (行 77)
# ============================================================

class TestNormalizeAmountDecimal:
    """覆盖 _normalize_amount 返回小数字符串的分支."""

    def test_decimal_amount_wan(self):
        """行 77: '1.5万元' 归一化为 '15000' (小数运算后 rstrip)."""
        result = _normalize_amount("1.5万元")
        assert result == "15000"

    def test_decimal_amount_yi(self):
        """'1.5亿元' 归一化为 '150000000'."""
        result = _normalize_amount("1.5亿元")
        assert result == "150000000"

    def test_decimal_amount_with_trailing_zeros(self):
        """'1.50万元' 归一化后 rstrip 去掉末尾零."""
        result = _normalize_amount("1.50万元")
        # 1.50 * 10000 = 15000.00 -> rstrip("0").rstrip(".") -> "15000"
        assert result == "15000"

    def test_decimal_amount_small_decimal(self):
        """'0.5万元' 归一化为 '5000'."""
        result = _normalize_amount("0.5万元")
        assert result == "5000"

    def test_decimal_amount_yuan(self):
        """'1.5元' 归一化为 '1.5'."""
        result = _normalize_amount("1.5元")
        assert result == "1.5"

    def test_integer_amount_returns_int_string(self):
        """整数金额返回整数字符串 (不走到行 77)."""
        result = _normalize_amount("100万元")
        assert result == "1000000"


# ============================================================
# 测试套件 2: CheckReport.to_dict (行 122)
# ============================================================

class TestCheckReportToDict:
    """覆盖 CheckReport.to_dict 序列化."""

    def test_to_dict_passed_report(self):
        """行 122: 通过的报告 to_dict 序列化."""
        report = CheckReport(
            passed=True,
            total_facts=3,
            verified_facts=3,
            hallucinated_facts=0,
            facts=[
                Fact(category="金额", value="100万元", in_source=True),
                Fact(category="日期", value="2024-01-01", in_source=True),
            ],
        )
        d = report.to_dict()
        assert d["passed"] is True
        assert d["total_facts"] == 3
        assert d["verified_facts"] == 3
        assert d["hallucinated_facts"] == 0
        assert len(d["hallucinated_values"]) == 0

    def test_to_dict_with_hallucinated_facts(self):
        """有幻觉事实时 to_dict 包含幻觉值列表."""
        report = CheckReport(
            passed=False,
            total_facts=2,
            verified_facts=1,
            hallucinated_facts=1,
            facts=[
                Fact(category="金额", value="100万元", in_source=True),
                Fact(category="日期", value="2024-13-45", in_source=False),
            ],
        )
        d = report.to_dict()
        assert d["passed"] is False
        assert d["hallucinated_facts"] == 1
        assert len(d["hallucinated_values"]) == 1
        assert d["hallucinated_values"][0]["category"] == "日期"
        assert d["hallucinated_values"][0]["value"] == "2024-13-45"

    def test_to_dict_empty_report(self):
        """空报告的 to_dict."""
        report = CheckReport(passed=True)
        d = report.to_dict()
        assert d["passed"] is True
        assert d["total_facts"] == 0
        assert d["hallucinated_values"] == []


# ============================================================
# 测试套件 3: extract_facts 重复跳过 (行 145)
# ============================================================

class TestExtractFactsDuplicateSkip:
    """覆盖 extract_facts 中重复事实被跳过的分支."""

    def test_duplicate_amount_skipped(self):
        """行 145: 同一金额事实出现多次只提取一次."""
        text = "预算100万元，追加100万元，再追加100万元"
        facts = extract_facts(text)
        amount_facts = [f for f in facts if f.category == "金额"]
        # 100万元 只出现一次 (去重)
        assert len(amount_facts) == 1

    def test_duplicate_date_skipped(self):
        """同一日期出现多次只提取一次."""
        text = "截止日期2024-05-01，再次强调2024-05-01"
        facts = extract_facts(text)
        date_facts = [f for f in facts if f.category == "日期"]
        assert len(date_facts) == 1

    def test_different_amounts_not_deduplicated(self):
        """不同金额各自提取."""
        text = "预算100万元，决算200万元"
        facts = extract_facts(text)
        amount_facts = [f for f in facts if f.category == "金额"]
        assert len(amount_facts) == 2

    def test_duplicate_percentage_skipped(self):
        """相同百分比只提取一次."""
        text = "完成率90%，达标率90%"
        facts = extract_facts(text)
        pct_facts = [f for f in facts if f.category == "百分比"]
        assert len(pct_facts) == 1


# ============================================================
# 测试套件 4: _fact_in_source 边界 (行 158, 184-186)
# ============================================================

class TestFactInSourceEdge:
    """覆盖 _fact_in_source 中各种不匹配分支."""

    def test_empty_source_returns_false(self):
        """行 158: 空原文返回 False."""
        fact = Fact(category="金额", value="100万元")
        assert _fact_in_source(fact, "") is False

    def test_none_source_returns_false(self):
        """行 158: None 原文返回 False."""
        fact = Fact(category="金额", value="100万元")
        assert _fact_in_source(fact, None) is False

    def test_date_fact_not_in_source_returns_false(self):
        """行 184: 日期事实不在原文中返回 False."""
        fact = Fact(category="日期", value="2024-05-01")
        source = "本项目无日期信息"
        assert _fact_in_source(fact, source) is False

    def test_date_fact_unnormalizable_returns_false(self):
        """行 184: 日期事实无法归一化时返回 False."""
        fact = Fact(category="日期", value="not-a-date")
        source = "原文中有2024-05-01"
        assert _fact_in_source(fact, source) is False

    def test_date_fact_in_source_different_format_returns_true(self):
        """日期事实以不同格式在原文中, 归一化后匹配."""
        fact = Fact(category="日期", value="2024-05-01")
        source = "截止时间2024年5月1日"
        assert _fact_in_source(fact, source) is True

    def test_percentage_fact_not_in_source_returns_false(self):
        """行 186: 百分比事实不在原文中返回 False (非金额/日期类别)."""
        fact = Fact(category="百分比", value="95%")
        source = "完成率90%"
        assert _fact_in_source(fact, source) is False

    def test_quantity_fact_not_in_source_returns_false(self):
        """行 186: 数量事实不在原文中返回 False."""
        fact = Fact(category="数量", value="100台")
        source = "采购50台设备"
        assert _fact_in_source(fact, source) is False

    def test_phone_fact_not_in_source_returns_false(self):
        """行 186: 联系电话事实不在原文中返回 False."""
        fact = Fact(category="联系电话", value="010-12345678")
        source = "联系电话010-87654321"
        assert _fact_in_source(fact, source) is False

    def test_email_fact_in_source_returns_true(self):
        """邮箱事实在原文中 (子串匹配)."""
        fact = Fact(category="邮箱", value="test@example.com")
        source = "联系邮箱: test@example.com"
        assert _fact_in_source(fact, source) is True

    def test_amount_fact_unnormalizable_returns_false(self):
        """金额事实无法归一化时不匹配."""
        fact = Fact(category="金额", value="预算100")
        # _normalize_amount("预算100") returns None (no unit)
        source = "预算100万元"
        # 但子串匹配 "预算100" in "预算100万元" -> True (去空格后)
        result = _fact_in_source(fact, source)
        # "预算100" 去空格后 = "预算100", 原文去空格后 = "预算100万元"
        # "预算100" in "预算100万元" -> True
        assert result is True

    def test_bid_number_fact_not_in_source_returns_false(self):
        """行 186: 招标编号不在原文中返回 False."""
        fact = Fact(category="招标编号", value="SH-2024-001")
        source = "项目编号ZB-2024-002"
        assert _fact_in_source(fact, source) is False

    def test_bid_number_fact_in_source_returns_true(self):
        """招标编号在原文中 (子串匹配)."""
        fact = Fact(category="招标编号", value="SH-2024-001")
        source = "项目编号: SH-2024-001"
        assert _fact_in_source(fact, source) is True


# ============================================================
# 测试套件 5: check_content 无事实/非严格模式 (行 216, 237-238)
# ============================================================

class TestCheckContentEdgeCases:
    """覆盖 check_content 中无事实和非严格模式分支."""

    def test_no_facts_extracted_returns_passed(self):
        """行 216: 提取不到事实时默认通过."""
        content = "这是一段普通文字，没有数字、日期或金额。"
        source = "这是一段普通文字。"
        report = check_content(content, source)
        assert report.passed is True
        assert report.total_facts == 0
        assert report.verified_facts == 0
        assert report.hallucinated_facts == 0

    def test_non_strict_non_strict_category_treated_as_verified(self):
        """行 237-238: 非严格模式下，非严格类别不在原文也视为通过."""
        # 百分比是非严格类别 (不在 strict_categories 中)
        content = "完成率95%"
        source = "完成率90%"
        report = check_content(content, source, strict=False)
        # 95% 不在原文 (90% 才在), 但非严格模式下百分比被视为通过
        assert report.passed is True
        assert report.verified_facts == 1
        assert report.hallucinated_facts == 0

    def test_non_strict_quantity_treated_as_verified(self):
        """行 237-238: 非严格模式下，数量不在原文也视为通过."""
        content = "采购100台设备"
        source = "采购50台设备"
        report = check_content(content, source, strict=False)
        assert report.passed is True
        assert report.verified_facts == 1

    def test_strict_mode_non_strict_category_detected(self):
        """严格模式下，非严格类别不在原文也视为幻觉."""
        content = "完成率95%"
        source = "完成率90%"
        report = check_content(content, source, strict=True)
        assert report.passed is False
        assert report.hallucinated_facts == 1

    def test_non_strict_amount_still_strict(self):
        """非严格模式下，金额仍然必须找到 (strict_categories)."""
        content = "预算200万元"
        source = "预算100万元"
        report = check_content(content, source, strict=False)
        assert report.passed is False
        assert report.hallucinated_facts == 1

    def test_non_strict_date_still_strict(self):
        """非严格模式下，日期仍然必须找到."""
        content = "截止日期2024-12-31"
        source = "截止日期2024-01-01"
        report = check_content(content, source, strict=False)
        assert report.passed is False

    def test_empty_content_returns_passed(self):
        """空 core_content 返回通过."""
        report = check_content("", "原文内容")
        assert report.passed is True

    def test_none_content_returns_passed(self):
        """None core_content 返回通过."""
        report = check_content(None, "原文内容")  # type: ignore[arg-type]
        assert report.passed is True

    def test_empty_source_skips_check(self):
        """空原文时跳过校验."""
        report = check_content("预算100万元", "")
        assert report.passed is True
        assert report.total_facts == 0


# ============================================================
# 测试套件 6: check_items 批量校验
# ============================================================

class TestCheckItemsExtra:
    """补充 check_items 批量校验测试."""

    def test_check_items_mixed_results(self):
        """批量校验中部分通过部分失败."""
        items = [
            {"core_content": "预算100万元", "source_url": "url1"},
            {"core_content": "预算200万元", "source_url": "url2"},
        ]
        source_texts = {
            "url1": "预算100万元，项目启动",
            "url2": "预算50万元，项目启动",
        }
        result = check_items(items, source_texts)
        assert result["total_items"] == 2
        assert result["passed_items"] == 1
        assert result["failed_items"] == 1
        assert result["hallucinated_total"] == 1

    def test_check_items_empty_list(self):
        """空列表返回全零结果."""
        result = check_items([])
        assert result["total_items"] == 0
        assert result["passed_items"] == 0
        assert result["failed_items"] == 0

    def test_check_items_no_source_texts(self):
        """无原文映射时所有项通过 (空原文跳过校验)."""
        items = [
            {"core_content": "预算100万元", "source_url": "url1"},
        ]
        result = check_items(items, None)
        assert result["total_items"] == 1
        assert result["passed_items"] == 1

    def test_check_items_missing_core_content(self):
        """缺少 core_content 字段的条目视为空内容."""
        items = [
            {"source_url": "url1"},
        ]
        result = check_items(items, {"url1": "原文"})
        assert result["total_items"] == 1
        assert result["passed_items"] == 1

    def test_check_items_missing_source_url(self):
        """缺少 source_url 的条目用空原文校验."""
        items = [
            {"core_content": "预算100万元"},
        ]
        result = check_items(items, {})
        assert result["total_items"] == 1
        assert result["passed_items"] == 1

    def test_check_items_details_structure(self):
        """验证 details 结构完整."""
        items = [
            {"core_content": "预算100万元", "source_url": "url1"},
        ]
        result = check_items(items, {"url1": "预算100万元"})
        assert len(result["details"]) == 1
        detail = result["details"][0]
        assert "index" in detail
        assert "source_url" in detail
        assert "passed" in detail
        assert "total_facts" in detail
        assert "hallucinated_facts" in detail
