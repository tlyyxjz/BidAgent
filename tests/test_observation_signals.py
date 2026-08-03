"""observation_signals 单元测试（v4.1 第九章）。

覆盖六个 MVP 信号 + 主入口 + 严谨表述常量。
约束：被测函数均为同步，故不使用 @pytest.mark.asyncio；
测试数据使用真实 dict 结构，不使用 mock。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.processors.observation_signals import (
    ALL_MVP_SIGNALS,
    COOCCURRENCE_DISCLAIMER,
    FORBIDDEN_TERM_BID_COUNT,
    FORBIDDEN_TERM_CONCENTRATION,
    ObservationResult,
    ObservationSignal,
    REQUIRED_SIGNALS,
    SIGNAL_AWARD_ACTIVITY,
    SIGNAL_AWARD_CONCENTRATION,
    SIGNAL_CANCELLATION_LINK,
    SIGNAL_EXPLICIT_REJECTION,
    SIGNAL_HIGH_FREQ_COOCCURRENCE,
    SIGNAL_INFO_CONFLICT,
    STRICT_TERM_BID_COUNT,
    STRICT_TERM_CONCENTRATION,
    analyze_observation_signals,
    assess_award_activity,
    assess_award_concentration,
    assess_cancellation_link,
    assess_explicit_rejection,
    assess_high_freq_cooccurrence,
    assess_info_conflict,
)


# ========== 测试数据构造（真实数据结构，非 mock）==========

def _date(days_ago: int) -> str:
    """生成相对今天 N 天前的日期字符串，保证测试不依赖固定日期。"""
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")


# 近 90 天内的两条中标记录
win_records_recent = [
    {"purchaser": "北京大学第三医院", "win_amount": 1000000, "win_date": _date(10), "region": "北京", "source_platform": "ccgp"},
    {"purchaser": "上海交通大学", "win_amount": 2000000, "win_date": _date(45), "region": "上海", "source_platform": "ccgp"},
]

# 三条记录，其中第三条超出 90 天窗口（100 天前）
win_records_with_old = win_records_recent + [
    {"purchaser": "北京大学第三医院", "win_amount": 500000, "win_date": _date(100), "region": "北京", "source_platform": "chinabidding"},
]

# 三条记录用于集中度测试（含重复采购人）
win_records_concentration = [
    {"purchaser": "北京大学第三医院", "win_amount": 1000000, "win_date": _date(10), "region": "北京", "source_platform": "ccgp"},
    {"purchaser": "上海交通大学", "win_amount": 2000000, "win_date": _date(45), "region": "上海", "source_platform": "ccgp"},
    {"purchaser": "北京大学第三医院", "win_amount": 500000, "win_date": _date(20), "region": "北京", "source_platform": "chinabidding"},
]


# ========== 信号 1：中标活跃度 ==========

def test_assess_award_activity_basic():
    """信号1：中标活跃度基本计算（近 90 天次数与金额趋势）。"""
    signal = assess_award_activity(win_records_recent, days=90)
    assert signal.signal_name == SIGNAL_AWARD_ACTIVITY
    # 两条记录均在 90 天内
    assert signal.observed_value == 2
    assert signal.details["win_count"] == 2
    # 累计金额 = 1,000,000 + 2,000,000
    assert signal.details["total_amount"] == 3000000.0
    # 平均金额
    assert signal.details["avg_amount"] == 1500000.0
    # 月度趋势字典非空
    assert len(signal.details["monthly_trend"]) >= 1
    # 不作正负定性
    assert "不作正负定性" in signal.disclaimer


def test_assess_award_activity_empty_records():
    """信号1：空记录返回 0。"""
    signal = assess_award_activity([], days=90)
    assert signal.observed_value == 0
    assert signal.details["win_count"] == 0
    assert signal.details["total_amount"] == 0
    assert signal.details["avg_amount"] == 0
    assert signal.details["monthly_trend"] == {}


def test_assess_award_activity_recent_only():
    """信号1：只统计近 90 天，超出窗口的记录不计数。"""
    signal = assess_award_activity(win_records_with_old, days=90)
    # 第三条记录为 100 天前，应被排除
    assert signal.observed_value == 2
    assert signal.details["win_count"] == 2
    # 金额仅累计近 90 天两条
    assert signal.details["total_amount"] == 3000000.0


# ========== 信号 2：公开中标集中度 ==========

def test_assess_award_concentration_top3():
    """信号2：Top3 采购人集中度。"""
    signal = assess_award_concentration(win_records_concentration)
    assert signal.signal_name == SIGNAL_AWARD_CONCENTRATION
    # 共 3 条记录，Top3 采购人占比 100%
    assert signal.observed_value == 100.0
    # 北京大学第三医院出现 2 次，占比最高
    top3 = signal.details["top3_purchasers"]
    assert top3[0]["name"] == "北京大学第三医院"
    assert top3[0]["count"] == 2
    assert top3[0]["ratio"] == round(2 / 3, 4)
    # 严谨表述免责声明
    assert STRICT_TERM_CONCENTRATION in signal.disclaimer
    assert FORBIDDEN_TERM_CONCENTRATION in signal.disclaimer


def test_assess_award_concentration_empty():
    """信号2：空记录集中度为 0。"""
    signal = assess_award_concentration([])
    assert signal.observed_value == 0
    assert signal.details == {}
    assert "无公开中标记录" in signal.coverage_note
    assert STRICT_TERM_CONCENTRATION in signal.disclaimer


# ========== 信号 3：废标公告关联 ==========

def test_assess_cancellation_link():
    """信号3：废标公告关联。"""
    cancellation_records = [
        {"notice_title": "XX项目废标公告", "notice_url": "http://example.com/1", "publish_date": _date(5)},
        {"notice_title": "YY项目流标公告", "notice_url": "http://example.com/2", "publish_date": _date(15)},
    ]
    signal = assess_cancellation_link(cancellation_records)
    assert signal.signal_name == SIGNAL_CANCELLATION_LINK
    assert signal.observed_value == 2
    assert len(signal.details["cancellation_notices"]) == 2
    assert signal.details["cancellation_notices"][0]["notice_title"] == "XX项目废标公告"
    # 不直接归因
    assert "不直接归因" in signal.disclaimer


# ========== 信号 4：明确投标否决 ==========

def test_assess_explicit_rejection():
    """信号4：明确投标否决。"""
    rejection_records = [
        {
            "notice_title": "ZZ项目中标公告",
            "notice_url": "http://example.com/3",
            "publish_date": _date(8),
            "rejection_reason": "投标文件未响应实质性要求",
        },
    ]
    signal = assess_explicit_rejection(rejection_records)
    assert signal.signal_name == SIGNAL_EXPLICIT_REJECTION
    assert signal.observed_value == 1
    notice = signal.details["rejection_notices"][0]
    assert notice["rejection_reason"] == "投标文件未响应实质性要求"
    assert "不推测" in signal.disclaimer


# ========== 信号 5：信息冲突观察 ==========

def test_assess_info_conflict():
    """信号5：信息冲突观察。"""
    conflict_records = [
        {
            "fact_assertion_key": "org:注册资本",
            "source_a": "ccgp",
            "source_b": "chinabidding",
            "value_a": "1000万元",
            "value_b": "500万元",
            "field_name": "registered_capital",
        },
    ]
    signal = assess_info_conflict(conflict_records)
    assert signal.signal_name == SIGNAL_INFO_CONFLICT
    assert signal.observed_value == 1
    conflict = signal.details["conflicts"][0]
    assert conflict["field_name"] == "registered_capital"
    assert conflict["value_a"] != conflict["value_b"]
    assert "不判断真伪" in signal.disclaimer


# ========== 信号 6：高频共现提示 ==========

def test_assess_high_freq_cooccurrence_has_disclaimer():
    """信号6：高频共现必须附带免责声明（v4.1 第 9.3 节）。"""
    cooccurrence_records = [
        {"partner_org": "某建工集团", "co_occurrence_count": 5, "project_names": ["项目A", "项目B"]},
    ]
    signal = assess_high_freq_cooccurrence(cooccurrence_records)
    assert signal.signal_name == SIGNAL_HIGH_FREQ_COOCCURRENCE
    assert signal.observed_value == 1
    # 免责声明完整等于常量
    assert signal.disclaimer == COOCCURRENCE_DISCLAIMER
    # 仅凭共现不能判断围标
    assert "仅凭共现不能判断" in signal.disclaimer


# ========== 主入口 ==========

def test_analyze_observation_signals_full():
    """主入口：完整调用返回 6 个 MVP 信号。"""
    result = analyze_observation_signals(
        org_id="org-test-001",
        org_name="测试供应商",
        win_records=win_records_concentration,
        cancellation_records=[
            {"notice_title": "废标A", "notice_url": "u1", "publish_date": _date(3)},
        ],
        rejection_records=[
            {"notice_title": "否决B", "notice_url": "u2", "publish_date": _date(4), "rejection_reason": "原因"},
        ],
        conflict_records=[
            {"fact_assertion_key": "k", "source_a": "a", "source_b": "b",
             "value_a": "1", "value_b": "2", "field_name": "f"},
        ],
        cooccurrence_records=[
            {"partner_org": "P", "co_occurrence_count": 3, "project_names": []},
        ],
    )
    assert isinstance(result, ObservationResult)
    assert result.organization_id == "org-test-001"
    assert result.normalized_name == "测试供应商"
    # 恰好 6 个信号
    assert len(result.signals) == 6
    # 信号名称与 ALL_MVP_SIGNALS 顺序一致
    names = [s.signal_name for s in result.signals]
    assert names == ALL_MVP_SIGNALS
    # 供应商画像已生成
    assert result.profile is not None
    # 覆盖平台去重
    assert set(result.coverage_platforms) == {"ccgp", "chinabidding"}
    # 摘要包含组织名
    assert "测试供应商" in result.summary


# ========== 严谨表述常量（v4.1 第 9.3 节）==========

def test_strict_term_used():
    """验证严谨表述常量存在（v4.1 第 9.3 节）。"""
    # 投标次数严谨表述
    assert STRICT_TERM_BID_COUNT == "公开公告中观察到的投标出现次数"
    assert FORBIDDEN_TERM_BID_COUNT == "企业实际投标次数"
    # 集中度严谨表述
    assert STRICT_TERM_CONCENTRATION == "当前覆盖公开中标记录中的采购人集中度"
    assert FORBIDDEN_TERM_CONCENTRATION == "企业客户集中度"
    # 高频共现免责声明存在且非空
    assert COOCCURRENCE_DISCLAIMER
    assert "仅凭共现不能判断" in COOCCURRENCE_DISCLAIMER
    # 必做信号 5 个，选做 1 个，合计 6 个
    assert len(REQUIRED_SIGNALS) == 5
    assert len(ALL_MVP_SIGNALS) == 6


def test_no_credit_score_output():
    """验证结果中不含"信用评分"输出（v4.1 第 9.1 节：不输出供应商信用评分）。"""
    result = analyze_observation_signals("org-2", "测试公司", win_records_recent)
    # 用户可见汇总摘要不含"信用评分"
    assert "信用评分" not in result.summary
    # 六个信号的名称、免责声明、详情均不含"信用评分"
    for sig in result.signals:
        assert "信用评分" not in sig.signal_name
        assert "信用评分" not in sig.disclaimer
        assert "信用评分" not in str(sig.details)
    # 结果对象与信号对象均无 credit_score 字段
    assert "credit_score" not in ObservationResult.__dataclass_fields__
    assert "credit_score" not in ObservationSignal.__dataclass_fields__
