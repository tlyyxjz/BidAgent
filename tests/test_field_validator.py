"""W2-04 金额/日期/编号确定性校验单元测试。

覆盖：
- 金额校验：万元/元转换、格式统一、货币识别、金额类型
- 日期校验：中文日期、ISO 日期、格式统一、合法性
- 编号校验：格式、长度、规范化
- 批量校验
- 边界情况
"""
from __future__ import annotations

import pytest

from app.processors.field_validator import (
    AMOUNT_TYPES,
    CURRENCIES,
    ValidationResult,
    validate_amount,
    validate_amount_batch,
    validate_date,
    validate_date_batch,
    validate_identifier_batch,
    validate_project_identifier,
)


class TestValidateAmount:
    """金额校验测试。"""

    def test_valid_amount_yuan(self):
        result = validate_amount("100元")
        assert result.valid
        assert result.normalized_value == 100.0
        assert result.currency == "CNY"

    def test_valid_amount_wan(self):
        result = validate_amount("100万元")
        assert result.valid
        assert result.normalized_value == 1000000.0  # 100 * 10000
        assert "万元" in result.normalized

    def test_valid_amount_decimal(self):
        result = validate_amount("100.50万元")
        assert result.valid
        assert result.normalized_value == 1005000.0

    def test_valid_amount_with_currency_symbol(self):
        result = validate_amount("￥100万元")
        assert result.valid
        assert result.currency == "CNY"

    def test_valid_amount_rmb(self):
        result = validate_amount("人民币100万元")
        assert result.valid
        assert result.currency == "CNY"

    def test_valid_amount_with_space(self):
        result = validate_amount("100.00 万元")
        assert result.valid

    def test_invalid_amount_empty(self):
        result = validate_amount("")
        assert not result.valid
        assert "空" in result.errors[0]

    def test_invalid_amount_format(self):
        result = validate_amount("一百万元")
        assert not result.valid
        assert "格式" in result.errors[0]

    def test_invalid_amount_type(self):
        result = validate_amount("100万元", amount_type="invalid_type")
        assert not result.valid
        assert "金额类型" in result.errors[0]

    def test_valid_amount_types(self):
        for amount_type in AMOUNT_TYPES:
            result = validate_amount("100万元", amount_type=amount_type)
            assert result.valid

    def test_zero_amount_warning(self):
        result = validate_amount("0元")
        assert result.valid
        assert len(result.warnings) > 0
        assert "0" in result.warnings[0]

    def test_amount_normalization_small(self):
        """小金额规范化为元。"""
        result = validate_amount("500元")
        assert result.valid
        assert "元" in result.normalized
        assert "万元" not in result.normalized


class TestValidateDate:
    """日期校验测试。"""

    def test_valid_cn_date(self):
        result = validate_date("2026年8月1日")
        assert result.valid
        assert result.normalized == "2026-08-01"

    def test_valid_cn_date_with_zero_padding(self):
        result = validate_date("2026年08月01日")
        assert result.valid
        assert result.normalized == "2026-08-01"

    def test_valid_cn_date_with_time(self):
        result = validate_date("2026年8月1日 09:00")
        assert result.valid
        assert result.normalized == "2026-08-01 09:00"

    def test_valid_iso_date(self):
        result = validate_date("2026-08-01")
        assert result.valid
        assert result.normalized == "2026-08-01"

    def test_valid_iso_date_with_time(self):
        result = validate_date("2026-08-01 09:00")
        assert result.valid
        assert result.normalized == "2026-08-01 09:00"

    def test_invalid_date_empty(self):
        result = validate_date("")
        assert not result.valid

    def test_invalid_date_format(self):
        result = validate_date("2026/8/1")
        assert not result.valid
        assert "格式" in result.errors[0]

    def test_invalid_month(self):
        result = validate_date("2026年13月1日")
        assert not result.valid
        assert "月" in result.errors[0]

    def test_invalid_day(self):
        result = validate_date("2026年2月30日")
        assert not result.valid
        assert "日" in result.errors[0] or "没有" in result.errors[0]

    def test_valid_february_leap(self):
        """闰年2月29日合法。"""
        result = validate_date("2024年2月29日")
        assert result.valid  # 2月允许29日


class TestValidateIdentifier:
    """编号校验测试。"""

    def test_valid_identifier_simple(self):
        result = validate_project_identifier("ZFCG2026001")
        assert result.valid
        assert result.normalized == "ZFCG2026001"

    def test_valid_identifier_with_dash(self):
        result = validate_project_identifier("ZFCG-2026-001")
        assert result.valid
        assert result.normalized == "ZFCG-2026-001"

    def test_valid_identifier_lowercase(self):
        result = validate_project_identifier("zfcg2026001")
        assert result.valid
        assert result.normalized == "ZFCG2026001"  # 规范化为大写

    def test_valid_identifier_long(self):
        result = validate_project_identifier("DDWK2026024")
        assert result.valid

    def test_invalid_identifier_empty(self):
        result = validate_project_identifier("")
        assert not result.valid

    def test_invalid_identifier_short(self):
        result = validate_project_identifier("ABC")
        assert not result.valid
        assert "长度" in result.errors[0]

    def test_invalid_identifier_start_with_digit(self):
        result = validate_project_identifier("2026ZFCG")
        assert not result.valid
        assert "字母开头" in result.errors[0]

    def test_invalid_identifier_special_char(self):
        result = validate_project_identifier("ZFCG@2026")
        assert not result.valid


class TestBatchValidation:
    """批量校验测试。"""

    def test_validate_amount_batch(self):
        items = [("100万元", "budget"), ("200元", "award"), ("", "other")]
        results = validate_amount_batch(items)
        assert len(results) == 3
        assert results[0].valid
        assert results[1].valid
        assert not results[2].valid

    def test_validate_date_batch(self):
        items = ["2026年8月1日", "2026-08-01", "invalid"]
        results = validate_date_batch(items)
        assert len(results) == 3
        assert results[0].valid
        assert results[1].valid
        assert not results[2].valid

    def test_validate_identifier_batch(self):
        items = ["ZFCG2026001", "DDWK2026024", "ABC"]
        results = validate_identifier_batch(items)
        assert len(results) == 3
        assert results[0].valid
        assert results[1].valid
        assert not results[2].valid


class TestRealWorldCases:
    """真实场景测试。"""

    def test_real_amount_cases(self):
        """真实金额样本。"""
        cases = [
            ("100.00万元", "budget", True),
            ("1,000,000元", None, False),  # 含逗号，不合法
            ("100.00 万元", "award", True),
            ("人民币100万元", "budget", True),
            ("￥50.5万元", "control_price", True),
        ]
        for raw, amount_type, expected_valid in cases:
            result = validate_amount(raw, amount_type=amount_type)
            assert result.valid == expected_valid, f"Failed: {raw} (expected {expected_valid})"

    def test_real_date_cases(self):
        """真实日期样本。"""
        cases = [
            ("2026年7月15日", True),
            ("2026-07-15", True),
            ("2026年7月15日 09:00", True),
            ("2026-07-15 14:30", True),
            ("2026/7/15", False),
        ]
        for raw, expected_valid in cases:
            result = validate_date(raw)
            assert result.valid == expected_valid, f"Failed: {raw}"

    def test_real_identifier_cases(self):
        """真实编号样本。"""
        cases = [
            ("ZFCG-2026-001", True),
            ("DDWK2026024", True),
            ("GZCG-2026-001-1", True),
            ("abc", False),  # 太短
            ("2026ZFCG", False),  # 数字开头
        ]
        for raw, expected_valid in cases:
            result = validate_project_identifier(raw)
            assert result.valid == expected_valid, f"Failed: {raw}"
