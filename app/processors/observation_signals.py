"""组织实体公开活动观察信号引擎（v4.1 第九章）。

对应 v4.1 第 9.2 节 MVP 信号，严格遵循第 9.3 节严谨表述要求。

核心原则（v4.1 第 9.1 节）：
- 标小智不输出供应商信用评分
- 所有信号只反映系统覆盖来源和时间范围内观察到的公开招投标活动
- 供人工尽调参考，不作正负定性

六个 MVP 信号（v4.1 第 9.2 节）：
1. 中标活跃度：近 90 天公开中标次数和金额趋势，不作正负定性
2. 公开中标集中度：当前覆盖数据中 Top 3 采购人及地区占比
3. 废标公告关联：企业在废标或流标公告中被观察到的次数，不直接归因
4. 明确投标否决：公告明确写明企业投标被否决，并记录原因
5. 信息冲突观察：相同事实断言在不同有效来源中出现矛盾
6. 高频共现提示（选做）：企业与其他企业在同一标段被反复观察到

严谨表述（v4.1 第 9.3 节）：
- 使用"公开公告中观察到的投标出现次数"，不使用"企业实际投标次数"
- 使用"当前覆盖公开中标记录中的采购人集中度"，不使用"企业客户集中度"
- 高频共现必须附带说明，仅凭共现不能判断围标
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from app.models.organization import SupplierProfile, build_supplier_profile
from app.utils.logger import get_logger

logger = get_logger("observation_signals")


# ========== 信号名称常量（v4.1 第 9.2 节）==========

SIGNAL_AWARD_ACTIVITY = "中标活跃度"
SIGNAL_AWARD_CONCENTRATION = "公开中标集中度"
SIGNAL_CANCELLATION_LINK = "废标公告关联"
SIGNAL_EXPLICIT_REJECTION = "明确投标否决"
SIGNAL_INFO_CONFLICT = "信息冲突观察"
SIGNAL_HIGH_FREQ_COOCCURRENCE = "高频共现提示"  # 选做

# 所有 MVP 信号列表
ALL_MVP_SIGNALS = [
    SIGNAL_AWARD_ACTIVITY,
    SIGNAL_AWARD_CONCENTRATION,
    SIGNAL_CANCELLATION_LINK,
    SIGNAL_EXPLICIT_REJECTION,
    SIGNAL_INFO_CONFLICT,
    SIGNAL_HIGH_FREQ_COOCCURRENCE,
]

# 必做信号（5 个，高频共现为选做）
REQUIRED_SIGNALS = ALL_MVP_SIGNALS[:5]


# ========== 严谨表述常量（v4.1 第 9.3 节）==========

STRICT_TERM_BID_COUNT = "公开公告中观察到的投标出现次数"
FORBIDDEN_TERM_BID_COUNT = "企业实际投标次数"

STRICT_TERM_CONCENTRATION = "当前覆盖公开中标记录中的采购人集中度"
FORBIDDEN_TERM_CONCENTRATION = "企业客户集中度"

COOCCURRENCE_DISCLAIMER = (
    "高频共现可能由行业集中度、区域市场和项目准入条件等多种因素造成。"
    "仅凭共现不能判断企业关联关系或围标行为。"
)


# ========== 数据结构 ==========

@dataclass
class ObservationSignal:
    """单个观察信号结果。"""
    signal_name: str          # 信号名称（v4.1 第 9.2 节）
    observed_value: float     # 观察到的数值
    observation_period: str   # 观察时间范围描述
    coverage_note: str        # 覆盖说明（v4.1 第 9.4 节）
    details: dict = field(default_factory=dict)  # 详细数据
    disclaimer: str = ""      # 严谨表述免责声明（v4.1 第 9.3 节）


@dataclass
class ObservationResult:
    """组织实体公开活动观察信号汇总结果。"""
    organization_id: str
    normalized_name: str
    # 数据完整性展示（v4.1 第 9.4 节）
    coverage_platforms: list = field(default_factory=list)      # 覆盖平台
    coverage_time_range: str = ""                               # 覆盖时间
    valid_notice_count: int = 0                                 # 有效公告数量
    bidder_list_notice_count: int = 0                          # 包含投标人名单的公告数量
    entity_resolution_status: str = "unresolved"               # 企业消歧状态
    possible_omissions: str = ""                               # 可能的遗漏
    signal_caliber: str = ""                                   # 信号计算口径
    # 六个 MVP 信号
    signals: list = field(default_factory=list)  # list[ObservationSignal]
    # 供应商画像
    profile: Optional[SupplierProfile] = None
    # 分析时间
    analyzed_at: str = ""
    # 总结
    summary: str = ""


# ========== 信号计算函数 ==========

def _get_recent_records(
    win_records: list,
    days: int = 90,
) -> list:
    """获取近 N 天的中标记录。"""
    if not win_records:
        return []
    cutoff = datetime.now() - timedelta(days=days)
    recent = []
    for rec in win_records:
        win_date_str = rec.get("win_date") or rec.get("award_date") or ""
        if not win_date_str:
            continue
        try:
            win_date = datetime.fromisoformat(win_date_str.replace("Z", ""))
        except (ValueError, TypeError):
            try:
                win_date = datetime.strptime(win_date_str[:10], "%Y-%m-%d")
            except (ValueError, TypeError):
                continue
        if win_date >= cutoff:
            recent.append(rec)
    return recent


def assess_award_activity(
    win_records: list,
    days: int = 90,
) -> ObservationSignal:
    """信号 1：中标活跃度（v4.1 第 9.2 节）。

    近 90 天公开中标次数和金额趋势，不作正负定性。
    """
    recent_records = _get_recent_records(win_records, days)
    win_count = len(recent_records)
    total_amount = sum(
        float(r.get("win_amount", 0) or 0) for r in recent_records
    )

    # 金额趋势（按月统计）
    monthly_trend = {}
    for rec in recent_records:
        win_date_str = rec.get("win_date") or rec.get("award_date") or ""
        if not win_date_str:
            continue
        try:
            month_key = win_date_str[:7]  # YYYY-MM
            monthly_trend[month_key] = monthly_trend.get(month_key, 0) + float(
                rec.get("win_amount", 0) or 0
            )
        except (ValueError, TypeError):
            continue

    return ObservationSignal(
        signal_name=SIGNAL_AWARD_ACTIVITY,
        observed_value=win_count,
        observation_period=f"近 {days} 天",
        coverage_note=f"覆盖 {len(recent_records)} 条公开中标记录",
        details={
            "win_count": win_count,
            "total_amount": round(total_amount, 2),
            "monthly_trend": monthly_trend,
            "avg_amount": round(total_amount / win_count, 2) if win_count > 0 else 0,
        },
        disclaimer="不作正负定性，仅反映观察到的公开中标活动",
    )


def assess_award_concentration(
    win_records: list,
) -> ObservationSignal:
    """信号 2：公开中标集中度（v4.1 第 9.2 节）。

    当前覆盖数据中 Top 3 采购人及地区占比。
    严谨表述：使用"当前覆盖公开中标记录中的采购人集中度"（v4.1 第 9.3 节）。
    """
    if not win_records:
        return ObservationSignal(
            signal_name=SIGNAL_AWARD_CONCENTRATION,
            observed_value=0,
            observation_period="全部覆盖时间",
            coverage_note="无公开中标记录",
            details={},
            disclaimer=f"使用严谨表述：{STRICT_TERM_CONCENTRATION}",
        )

    # 采购人集中度
    purchaser_counts: dict[str, int] = {}
    region_counts: dict[str, int] = {}
    for rec in win_records:
        purchaser = rec.get("purchaser", "") or "未知"
        region = rec.get("region", "") or "未知"
        purchaser_counts[purchaser] = purchaser_counts.get(purchaser, 0) + 1
        region_counts[region] = region_counts.get(region, 0) + 1

    total = len(win_records)
    # Top 3 采购人占比
    top3_purchasers = sorted(purchaser_counts.items(), key=lambda x: -x[1])[:3]
    top3_purchaser_ratio = sum(c for _, c in top3_purchasers) / total if total > 0 else 0

    # Top 3 地区占比
    top3_regions = sorted(region_counts.items(), key=lambda x: -x[1])[:3]
    top3_region_ratio = sum(c for _, c in top3_regions) / total if total > 0 else 0

    return ObservationSignal(
        signal_name=SIGNAL_AWARD_CONCENTRATION,
        observed_value=round(top3_purchaser_ratio * 100, 2),
        observation_period="全部覆盖时间",
        coverage_note=f"覆盖 {total} 条公开中标记录",
        details={
            "top3_purchasers": [{"name": n, "count": c, "ratio": round(c / total, 4)} for n, c in top3_purchasers],
            "top3_regions": [{"name": n, "count": c, "ratio": round(c / total, 4)} for n, c in top3_regions],
            "top3_purchaser_ratio": round(top3_purchaser_ratio, 4),
            "top3_region_ratio": round(top3_region_ratio, 4),
        },
        disclaimer=f"使用严谨表述：{STRICT_TERM_CONCENTRATION}，不使用'{FORBIDDEN_TERM_CONCENTRATION}'",
    )


def assess_cancellation_link(
    cancellation_records: list,
) -> ObservationSignal:
    """信号 3：废标公告关联（v4.1 第 9.2 节）。

    企业在废标或流标公告中被观察到的次数，不直接归因。
    """
    observed_count = len(cancellation_records) if cancellation_records else 0

    return ObservationSignal(
        signal_name=SIGNAL_CANCELLATION_LINK,
        observed_value=observed_count,
        observation_period="全部覆盖时间",
        coverage_note=f"覆盖 {observed_count} 条废标/流标公告观察记录",
        details={
            "cancellation_notices": [
                {
                    "notice_title": r.get("notice_title", ""),
                    "notice_url": r.get("notice_url", ""),
                    "publish_date": r.get("publish_date", ""),
                }
                for r in (cancellation_records or [])
            ],
        },
        disclaimer="不直接归因，仅记录在废标/流标公告中被观察到",
    )


def assess_explicit_rejection(
    rejection_records: list,
) -> ObservationSignal:
    """信号 4：明确投标否决（v4.1 第 9.2 节）。

    公告明确写明企业投标被否决，并记录原因。
    """
    observed_count = len(rejection_records) if rejection_records else 0

    return ObservationSignal(
        signal_name=SIGNAL_EXPLICIT_REJECTION,
        observed_value=observed_count,
        observation_period="全部覆盖时间",
        coverage_note=f"覆盖 {observed_count} 条明确投标否决记录",
        details={
            "rejection_notices": [
                {
                    "notice_title": r.get("notice_title", ""),
                    "notice_url": r.get("notice_url", ""),
                    "publish_date": r.get("publish_date", ""),
                    "rejection_reason": r.get("rejection_reason", ""),
                }
                for r in (rejection_records or [])
            ],
        },
        disclaimer="仅记录公告中明确写明的否决，不推测",
    )


def assess_info_conflict(
    conflict_records: list,
) -> ObservationSignal:
    """信号 5：信息冲突观察（v4.1 第 9.2 节）。

    相同事实断言在不同有效来源中出现矛盾。
    """
    observed_count = len(conflict_records) if conflict_records else 0

    return ObservationSignal(
        signal_name=SIGNAL_INFO_CONFLICT,
        observed_value=observed_count,
        observation_period="全部覆盖时间",
        coverage_note=f"覆盖 {observed_count} 条信息冲突观察记录",
        details={
            "conflicts": [
                {
                    "fact_assertion_key": r.get("fact_assertion_key", ""),
                    "source_a": r.get("source_a", ""),
                    "source_b": r.get("source_b", ""),
                    "value_a": r.get("value_a", ""),
                    "value_b": r.get("value_b", ""),
                    "field_name": r.get("field_name", ""),
                }
                for r in (conflict_records or [])
            ],
        },
        disclaimer="仅观察记录，不判断真伪",
    )


def assess_high_freq_cooccurrence(
    cooccurrence_records: list,
) -> ObservationSignal:
    """信号 6：高频共现提示（选做，v4.1 第 9.2 节）。

    企业与其他企业在同一标段被反复观察到，不用于判断围标。
    """
    observed_count = len(cooccurrence_records) if cooccurrence_records else 0

    return ObservationSignal(
        signal_name=SIGNAL_HIGH_FREQ_COOCCURRENCE,
        observed_value=observed_count,
        observation_period="全部覆盖时间",
        coverage_note=f"覆盖 {observed_count} 条高频共现记录（选做信号）",
        details={
            "cooccurrences": [
                {
                    "partner_org": r.get("partner_org", ""),
                    "co_occurrence_count": r.get("co_occurrence_count", 0),
                    "project_names": r.get("project_names", []),
                }
                for r in (cooccurrence_records or [])
            ],
        },
        disclaimer=COOCCURRENCE_DISCLAIMER,
    )


# ========== 主入口函数 ==========

def analyze_observation_signals(
    org_id: str,
    org_name: str,
    win_records: list,
    cancellation_records: list = None,
    rejection_records: list = None,
    conflict_records: list = None,
    cooccurrence_records: list = None,
) -> ObservationResult:
    """计算组织实体公开活动观察信号（v4.1 第九章）。

    Args:
        org_id: 组织实体 ID
        org_name: 组织名称
        win_records: 公开中标记录列表
        cancellation_records: 废标/流标公告关联记录（可选）
        rejection_records: 明确投标否决记录（可选）
        conflict_records: 信息冲突观察记录（可选）
        cooccurrence_records: 高频共现记录（可选，选做信号）

    Returns:
        ObservationResult: 观察信号汇总结果
    """
    logger.info("analyze_observation_signals started org_id={} org_name={}", org_id, org_name)

    result = ObservationResult(
        organization_id=org_id,
        normalized_name=org_name,
        analyzed_at=datetime.now().isoformat(),
    )

    # 数据完整性展示（v4.1 第 9.4 节）
    result.coverage_platforms = list(set(
        r.get("source_platform", "") for r in win_records if r.get("source_platform")
    ))
    if win_records:
        dates = []
        for rec in win_records:
            d = rec.get("win_date") or rec.get("award_date") or ""
            if d:
                dates.append(d[:10])
        if dates:
            result.coverage_time_range = f"{min(dates)} ~ {max(dates)}"
    result.valid_notice_count = len(win_records)
    result.entity_resolution_status = "resolved" if org_id else "unresolved"
    result.signal_caliber = "基于公开招投标公告观察，不输出信用评分"

    # 构建供应商画像
    try:
        result.profile = build_supplier_profile(org_id, org_name, win_records)
    except Exception as exc:
        logger.warning("build_supplier_profile failed: {}", exc)

    # 计算六个 MVP 信号
    result.signals = [
        assess_award_activity(win_records),
        assess_award_concentration(win_records),
        assess_cancellation_link(cancellation_records or []),
        assess_explicit_rejection(rejection_records or []),
        assess_info_conflict(conflict_records or []),
        assess_high_freq_cooccurrence(cooccurrence_records or []),
    ]

    # 汇总摘要
    signal_summary = "; ".join(
        f"{s.signal_name}={s.observed_value}" for s in result.signals
    )
    result.summary = (
        f"组织 {org_name} 公开活动观察信号：{signal_summary}。"
        f"覆盖平台 {len(result.coverage_platforms)} 个，"
        f"有效公告 {result.valid_notice_count} 条。"
        "所有信号仅反映系统覆盖范围内的公开招投标活动观察，不构成授信或投资依据。"
    )

    logger.info("analyze_observation_signals completed signals={}", len(result.signals))
    return result
