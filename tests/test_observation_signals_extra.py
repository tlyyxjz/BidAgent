"""observation_signals.py 补充测试：提升覆盖率 91% -> 95%+.

覆盖未覆盖行: 122, 125-129, 154, 160-161, 397-398

策略:
- _get_recent_records 直接调用, 覆盖空日期/非法日期分支
- assess_award_activity 覆盖月度趋势中空日期/非法金额分支
- analyze_observation_signals 通过 monkeypatch 让 build_supplier_profile 抛异常
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app.processors import observation_signals as obs_mod
from app.processors.observation_signals import (
    ObservationResult,
    ObservationSignal,
    SIGNAL_AWARD_ACTIVITY,
    analyze_observation_signals,
    assess_award_activity,
    assess_award_concentration,
    _get_recent_records,
)


def _date(days_ago):
    """生成相对今天 N 天前的日期字符串."""
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")


# ============================================================
# 测试套件 1: _get_recent_records 边界 (行 122, 125-129)
# ============================================================

class TestGetRecentRecordsEdge:
    """覆盖 _get_recent_records 中空日期和非法日期分支."""

    def test_empty_win_records_returns_empty(self):
        """空列表返回空."""
        assert _get_recent_records([]) == []

    def test_none_win_records_returns_empty(self):
        """None 返回空."""
        assert _get_recent_records(None) == []

    def test_record_without_date_skipped(self):
        """行 122: 没有 win_date 和 award_date 的记录被跳过."""
        records = [
            {"win_amount": 100, "win_date": _date(10)},
            {"win_amount": 200},
            {"win_amount": 300, "award_date": _date(20)},
        ]
        recent = _get_recent_records(records, days=90)
        assert len(recent) == 2

    def test_record_with_empty_date_string_skipped(self):
        """行 122: 空字符串日期被跳过."""
        records = [
            {"win_date": "", "win_amount": 100},
            {"win_date": _date(5), "win_amount": 200},
        ]
        recent = _get_recent_records(records, days=90)
        assert len(recent) == 1

    def test_record_with_invalid_iso_date_skipped(self):
        """行 125-129: 非法日期字符串触发异常后被跳过."""
        records = [
            {"win_date": "2024-13-45", "win_amount": 100},
        ]
        recent = _get_recent_records(records, days=9999)
        assert len(recent) == 0

    def test_record_with_garbage_date_skipped(self):
        """行 125-129: 完全无法解析的日期字符串被跳过."""
        records = [
            {"win_date": "not-a-date-at-all", "win_amount": 100},
            {"win_date": " garbage ", "win_amount": 200},
        ]
        recent = _get_recent_records(records, days=9999)
        assert len(recent) == 0

    def test_record_with_valid_iso_date(self):
        """合法 ISO 日期被正确解析."""
        records = [
            {"win_date": _date(5), "win_amount": 100},
        ]
        recent = _get_recent_records(records, days=90)
        assert len(recent) == 1

    def test_old_record_filtered_out(self):
        """超出时间窗口的记录被过滤."""
        records = [
            {"win_date": _date(10), "win_amount": 100},
            {"win_date": _date(200), "win_amount": 200},
        ]
        recent = _get_recent_records(records, days=90)
        assert len(recent) == 1
        assert recent[0]["win_amount"] == 100


# ============================================================
# 测试套件 2: assess_award_activity 月度趋势边界 (行 154, 160-161)
# ============================================================

class TestAwardActivityMonthlyTrend:
    """覆盖 assess_award_activity 中月度趋势计算的边界分支."""

    def test_monthly_trend_skips_empty_date(self):
        """行 154: 月度趋势计算中跳过空日期记录."""
        records = [
            {"win_date": _date(10), "win_amount": 100},
            {"win_amount": 200},
            {"win_date": "", "win_amount": 300},
        ]
        signal = assess_award_activity(records, days=90)
        assert len(signal.details["monthly_trend"]) >= 1

    def test_monthly_trend_with_none_amount(self):
        """行 160-161: 月度趋势计算中非法金额被跳过."""
        records = [
            {"win_date": _date(10), "win_amount": 100},
            {"win_date": _date(20), "win_amount": None},
            {"win_date": _date(30), "win_amount": None},
        ]
        signal = assess_award_activity(records, days=90)
        assert len(signal.details["monthly_trend"]) >= 1
        assert signal.details["win_count"] == 3

    def test_monthly_trend_aggregates_by_month(self):
        """同月记录金额被汇总."""
        records = [
            {"win_date": _date(5), "win_amount": 100},
            {"win_date": _date(10), "win_amount": 200},
        ]
        signal = assess_award_activity(records, days=90)
        trend = signal.details["monthly_trend"]
        assert len(trend) >= 1
        for month, amount in trend.items():
            assert amount > 0

    def test_total_amount_with_none_uses_zero(self):
        """win_amount 为 None 时按 0 处理."""
        records = [
            {"win_date": _date(5), "win_amount": None},
            {"win_date": _date(10), "win_amount": 100},
        ]
        signal = assess_award_activity(records, days=90)
        assert signal.details["total_amount"] == 100.0

    def test_total_amount_with_string_uses_zero(self):
        """win_amount 为字符串时 float() or 0 处理."""
        records = [
            {"win_date": _date(5), "win_amount": None},
            {"win_date": _date(10), "win_amount": 200},
        ]
        signal = assess_award_activity(records, days=90)
        assert signal.details["total_amount"] == 200.0


# ============================================================
# 测试套件 3: analyze_observation_signals 异常处理 (行 397-398)
# ============================================================

class TestAnalyzeSignalsProfileFailure:
    """覆盖 build_supplier_profile 失败时的异常处理."""

    def test_profile_failure_does_not_crash(self):
        """行 397-398: build_supplier_profile 抛异常时不崩溃, profile=None."""
        with patch(
            "app.processors.observation_signals.build_supplier_profile",
            side_effect=RuntimeError("profile build failed"),
        ):
            result = analyze_observation_signals(
                org_id="org-1",
                org_name="测试公司",
                win_records=[
                    {"win_date": _date(5), "win_amount": 100, "purchaser": "A"},
                ],
            )
        assert isinstance(result, ObservationResult)
        assert result.profile is None
        assert len(result.signals) == 6

    def test_profile_failure_with_type_error(self):
        """build_supplier_profile 抛 TypeError 时也不崩溃."""
        with patch(
            "app.processors.observation_signals.build_supplier_profile",
            side_effect=TypeError("bad type"),
        ):
            result = analyze_observation_signals(
                org_id="org-2",
                org_name="测试公司2",
                win_records=[],
            )
        assert result.profile is None
        assert len(result.signals) == 6

    def test_profile_failure_summary_still_generated(self):
        """build_supplier_profile 失败后摘要仍然正确生成."""
        with patch(
            "app.processors.observation_signals.build_supplier_profile",
            side_effect=ValueError("value error"),
        ):
            result = analyze_observation_signals(
                org_id="org-3",
                org_name="测试公司3",
                win_records=[
                    {
                        "win_date": _date(5),
                        "win_amount": 500,
                        "purchaser": "B",
                        "source_platform": "ccgp",
                    },
                ],
            )
        assert "测试公司3" in result.summary
        assert "覆盖平台" in result.summary


# ============================================================
# 测试套件 4: analyze_observation_signals 覆盖与完整性
# ============================================================

class TestAnalyzeCoverage:
    """覆盖 analyze_observation_signals 中数据完整性展示分支."""

    def test_empty_win_records(self):
        """空 win_records 不崩溃, 各字段为默认值."""
        result = analyze_observation_signals(
            org_id="org-empty",
            org_name="空数据公司",
            win_records=[],
        )
        assert result.valid_notice_count == 0
        assert result.coverage_platforms == []
        assert result.coverage_time_range == ""
        assert result.entity_resolution_status == "resolved"
        assert len(result.signals) == 6

    def test_records_without_source_platform(self):
        """无 source_platform 的记录不加入覆盖平台列表."""
        records = [
            {"win_date": _date(5), "win_amount": 100, "purchaser": "A"},
            {
                "win_date": _date(10),
                "win_amount": 200,
                "purchaser": "B",
                "source_platform": "",
            },
        ]
        result = analyze_observation_signals("org-4", "公司", records)
        assert result.coverage_platforms == []

    def test_records_with_dates_coverage_range(self):
        """有日期的记录生成覆盖时间范围."""
        records = [
            {"win_date": "2024-01-15", "win_amount": 100, "purchaser": "A"},
            {"win_date": "2024-06-20", "win_amount": 200, "purchaser": "B"},
        ]
        result = analyze_observation_signals("org-5", "公司", records)
        assert "2024-01-15" in result.coverage_time_range
        assert "2024-06-20" in result.coverage_time_range

    def test_records_without_dates_no_coverage_range(self):
        """无日期的记录不生成覆盖时间范围."""
        records = [
            {"win_amount": 100, "purchaser": "A"},
        ]
        result = analyze_observation_signals("org-6", "公司", records)
        assert result.coverage_time_range == ""

    def test_org_id_empty_sets_unresolved(self):
        """org_id 为空时 entity_resolution_status 为 unresolved."""
        result = analyze_observation_signals("", "公司", [])
        assert result.entity_resolution_status == "unresolved"

    def test_award_date_field_used_for_coverage(self):
        """award_date 字段也被用于覆盖时间范围."""
        records = [
            {"award_date": "2024-03-15", "win_amount": 100, "purchaser": "A"},
        ]
        result = analyze_observation_signals("org-7", "公司", records)
        assert "2024-03-15" in result.coverage_time_range

    def test_all_six_signals_present(self):
        """验证 6 个信号都存在且名称正确."""
        result = analyze_observation_signals("org-8", "公司", [])
        signal_names = [s.signal_name for s in result.signals]
        assert "中标活跃度" in signal_names
        assert "公开中标集中度" in signal_names
        assert "废标公告关联" in signal_names
        assert "明确投标否决" in signal_names
        assert "信息冲突观察" in signal_names
        assert "高频共现提示" in signal_names


# ============================================================
# 测试套件 5: assess_award_concentration 补充
# ============================================================

class TestAwardConcentrationExtra:
    """补充集中度信号测试."""

    def test_single_record_concentration(self):
        """单条记录集中度为 100%."""
        records = [
            {
                "purchaser": "采购人A",
                "win_amount": 100,
                "region": "北京",
                "win_date": _date(5),
            },
        ]
        signal = assess_award_concentration(records)
        assert signal.observed_value == 100.0
        assert len(signal.details["top3_purchasers"]) == 1

    def test_records_with_empty_purchaser(self):
        """空采购人被归为'未知'."""
        records = [
            {
                "purchaser": "",
                "win_amount": 100,
                "region": "北京",
                "win_date": _date(5),
            },
            {
                "purchaser": None,
                "win_amount": 200,
                "region": "上海",
                "win_date": _date(10),
            },
        ]
        signal = assess_award_concentration(records)
        top3 = signal.details["top3_purchasers"]
        assert any(p["name"] == "未知" for p in top3)

    def test_records_with_empty_region(self):
        """空地区被归为'未知'."""
        records = [
            {
                "purchaser": "A",
                "win_amount": 100,
                "region": "",
                "win_date": _date(5),
            },
            {
                "purchaser": "B",
                "win_amount": 200,
                "region": None,
                "win_date": _date(10),
            },
        ]
        signal = assess_award_concentration(records)
        top3_regions = signal.details["top3_regions"]
        assert any(r["name"] == "未知" for r in top3_regions)

    def test_more_than_three_purchasers(self):
        """超过 3 个采购人时只返回 Top 3."""
        records = [
            {
                "purchaser": f"P{i}",
                "win_amount": 100 * (10 - i),
                "region": "R",
                "win_date": _date(i + 1),
            }
            for i in range(5)
        ]
        signal = assess_award_concentration(records)
        assert len(signal.details["top3_purchasers"]) == 3
        assert signal.observed_value == 60.0
