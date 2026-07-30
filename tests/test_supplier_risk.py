"""W3-03 供应商风险分析测试。

覆盖：
- 集中度风险
- 金额异常风险
- 频率异常风险
- 地域集中风险
- 采购人集中风险
- 综合风险评分
- 空数据/边界场景
"""
from __future__ import annotations

import pytest

from app.processors.supplier_risk import (
    RISK_DIMENSION_AMOUNT_ANOMALY,
    RISK_DIMENSION_CONCENTRATION,
    RISK_DIMENSION_FREQUENCY_ANOMALY,
    RISK_DIMENSION_PURCHASER_CONCENTRATION,
    RISK_DIMENSION_REGION_CONCENTRATION,
    RISK_LEVEL_HIGH,
    RISK_LEVEL_LOW,
    RISK_LEVEL_MEDIUM,
    SupplierRiskResult,
    analyze_supplier,
    assess_amount_anomaly,
    assess_concentration_risk,
    assess_frequency_anomaly,
    assess_purchaser_concentration,
    assess_region_concentration,
)


# ========== 集中度风险测试 ==========

class TestAssessConcentrationRisk:
    """集中度风险。"""

    def test_no_records(self):
        """无中标记录 → 低风险。"""
        d = assess_concentration_risk(0, 0)
        assert d.level == RISK_LEVEL_LOW
        assert d.score == 0

    def test_few_wins_large_amount(self):
        """中标次数少 + 金额大 → 高风险。"""
        d = assess_concentration_risk(2, 20000000)  # 2 次，2000 万
        assert d.level == RISK_LEVEL_HIGH
        assert d.score == 70

    def test_many_wins(self):
        """中标次数多 → 低风险。"""
        d = assess_concentration_risk(15, 30000000)
        assert d.level == RISK_LEVEL_LOW
        assert d.score == 10

    def test_moderate_wins(self):
        """中标次数适中 → 中风险。"""
        d = assess_concentration_risk(5, 5000000)
        assert d.score == 30


# ========== 金额异常风险测试 ==========

class TestAssessAmountAnomaly:
    """金额异常风险。"""

    def test_no_amount_data(self):
        """无金额数据 → 低风险。"""
        d = assess_amount_anomaly([])
        assert d.level == RISK_LEVEL_LOW

    def test_outlier_high_amount(self):
        """单笔金额显著高于均值 → 高风险。"""
        records = [
            {"win_amount": 100000},
            {"win_amount": 120000},
            {"win_amount": 5000000},  # 离群值
        ]
        d = assess_amount_anomaly(records, outlier_factor=3.0)
        assert d.level in (RISK_LEVEL_HIGH, RISK_LEVEL_MEDIUM)
        assert d.score >= 40

    def test_stable_amounts(self):
        """金额稳定 → 低风险。"""
        records = [
            {"win_amount": 100000},
            {"win_amount": 110000},
            {"win_amount": 105000},
        ]
        d = assess_amount_anomaly(records)
        assert d.level == RISK_LEVEL_LOW

    def test_invalid_amounts_ignored(self):
        """无效金额被忽略。"""
        records = [
            {"win_amount": "invalid"},
            {"win_amount": None},
            {"win_amount": 0},
        ]
        d = assess_amount_anomaly(records)
        assert d.level == RISK_LEVEL_LOW


# ========== 频率异常风险测试 ==========

class TestAssessFrequencyAnomaly:
    """频率异常风险。"""

    def test_no_date_data(self):
        """无日期数据 → 低风险。"""
        d = assess_frequency_anomaly([])
        assert d.level == RISK_LEVEL_LOW

    def test_high_frequency(self):
        """同年中标次数过多 → 高风险。"""
        records = [{"win_date": "2026-01-15"} for _ in range(12)]
        d = assess_frequency_anomaly(records, high_frequency_threshold=10)
        assert d.level == RISK_LEVEL_HIGH
        assert d.score == 70

    def test_normal_frequency(self):
        """正常频率 → 低风险。"""
        records = [
            {"win_date": "2024-01-15"},
            {"win_date": "2025-03-20"},
            {"win_date": "2026-06-10"},
        ]
        d = assess_frequency_anomaly(records)
        assert d.level == RISK_LEVEL_LOW


# ========== 地域集中风险测试 ==========

class TestAssessRegionConcentration:
    """地域集中风险。"""

    def test_no_region_data(self):
        """无地区数据 → 低风险。"""
        d = assess_region_concentration([])
        assert d.level == RISK_LEVEL_LOW

    def test_high_concentration(self):
        """地域高度集中 → 高风险。"""
        records = [{"region": "上海"} for _ in range(8)]
        d = assess_region_concentration(records, concentration_threshold=0.8)
        assert d.score == 55

    def test_dispersed_regions(self):
        """地域分散 → 低风险。"""
        records = [
            {"region": "上海"},
            {"region": "北京"},
            {"region": "广州"},
            {"region": "深圳"},
        ]
        d = assess_region_concentration(records)
        assert d.level == RISK_LEVEL_LOW


# ========== 采购人集中风险测试 ==========

class TestAssessPurchaserConcentration:
    """采购人集中风险。"""

    def test_no_purchaser_data(self):
        """无采购人数据 → 低风险。"""
        d = assess_purchaser_concentration([])
        assert d.level == RISK_LEVEL_LOW

    def test_high_concentration(self):
        """采购人高度集中 → 高风险。"""
        records = [{"purchaser_name": "教育局"} for _ in range(8)]
        d = assess_purchaser_concentration(records, concentration_threshold=0.8)
        assert d.score == 65

    def test_dispersed_purchasers(self):
        """采购人分散 → 低风险。"""
        records = [
            {"purchaser_name": "教育局"},
            {"purchaser_name": "卫生局"},
            {"purchaser_name": "公安局"},
            {"purchaser_name": "民政局"},
        ]
        d = assess_purchaser_concentration(records)
        assert d.level == RISK_LEVEL_LOW


# ========== 综合风险分析测试 ==========

class TestAnalyzeSupplier:
    """综合风险分析。"""

    def test_empty_records(self):
        """无中标记录。"""
        result = analyze_supplier("org1", "某某公司", [])
        assert isinstance(result, SupplierRiskResult)
        assert result.organization_id == "org1"
        assert result.risk_level == RISK_LEVEL_LOW
        assert result.total_score == 0
        assert len(result.dimensions) == 5
        assert result.profile is not None
        assert result.analyzed_at != ""

    def test_low_risk_supplier(self):
        """低风险供应商：多次中标 + 金额稳定 + 分散。"""
        records = [
            {"win_amount": 100000, "purchaser_name": "教育局", "region": "上海", "win_date": "2024-01-15"},
            {"win_amount": 110000, "purchaser_name": "卫生局", "region": "北京", "win_date": "2024-06-20"},
            {"win_amount": 105000, "purchaser_name": "公安局", "region": "广州", "win_date": "2025-03-10"},
            {"win_amount": 120000, "purchaser_name": "民政局", "region": "深圳", "win_date": "2025-09-15"},
            {"win_amount": 115000, "purchaser_name": "交通局", "region": "杭州", "win_date": "2026-01-20"},
        ]
        result = analyze_supplier("org1", "某某公司", records)
        assert result.risk_level == RISK_LEVEL_LOW
        assert result.total_score <= 30
        assert "无高风险维度" in result.summary

    def test_high_risk_supplier(self):
        """高风险供应商：中标次数少 + 单笔金额大 + 采购人集中。"""
        records = [
            {"win_amount": 20000000, "purchaser_name": "教育局", "region": "上海", "win_date": "2026-01-15"},
            {"win_amount": 22000000, "purchaser_name": "教育局", "region": "上海", "win_date": "2026-03-20"},
            {"win_amount": 25000000, "purchaser_name": "教育局", "region": "上海", "win_date": "2026-06-10"},
        ]
        result = analyze_supplier("org1", "某某公司", records)
        # 集中度 + 采购人集中 + 地域集中都会加分
        assert result.total_score > 30
        assert result.profile.win_count == 3
        assert result.profile.total_win_amount == 67000000.0

    def test_dimensions_count(self):
        """5 个风险维度。"""
        result = analyze_supplier("org1", "某某公司", [])
        assert len(result.dimensions) == 5
        dim_names = {d.name for d in result.dimensions}
        assert dim_names == {
            RISK_DIMENSION_CONCENTRATION,
            RISK_DIMENSION_AMOUNT_ANOMALY,
            RISK_DIMENSION_FREQUENCY_ANOMALY,
            RISK_DIMENSION_REGION_CONCENTRATION,
            RISK_DIMENSION_PURCHASER_CONCENTRATION,
        }

    def test_summary_format(self):
        """摘要格式。"""
        result = analyze_supplier("org1", "某某公司", [])
        assert "总分" in result.summary
        assert result.risk_level in result.summary

    def test_score_in_range(self):
        """总分在 0-100 范围内。"""
        records = [
            {"win_amount": 1000000, "purchaser_name": "教育局", "region": "上海", "win_date": "2026-01-15"},
        ]
        result = analyze_supplier("org1", "某某公司", records)
        assert 0 <= result.total_score <= 100


# ========== 边界场景测试 ==========

class TestEdgeCases:
    """边界场景。"""

    def test_single_record(self):
        """单条记录。"""
        records = [{"win_amount": 500000, "purchaser_name": "教育局", "region": "上海", "win_date": "2026-01-15"}]
        result = analyze_supplier("org1", "某某公司", records)
        assert result.profile.win_count == 1

    def test_all_none_fields(self):
        """所有字段为 None。"""
        records = [{"win_amount": None, "purchaser_name": None, "region": None, "win_date": None}]
        result = analyze_supplier("org1", "某某公司", records)
        assert result.risk_level == RISK_LEVEL_LOW
