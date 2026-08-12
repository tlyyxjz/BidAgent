"""组织实体公开活动观察信号：信号计算函数（v4.1 第九章）。

从 observation_signal.py 拆分而来，承载六个 MVP 信号的计算逻辑：
1. assess_award_activity：中标活跃度
2. assess_award_concentration：公开中标集中度
3. assess_cancellation_link：废标公告关联
4. assess_explicit_rejection：明确投标否决
5. assess_info_conflict：信息冲突观察
6. assess_high_freq_cooccurrence：高频共现提示（选做）
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.processors.observation_types import (
    COOCCURRENCE_DISCLAIMER,
    FORBIDDEN_TERM_CONCENTRATION,
    SIGNAL_AWARD_ACTIVITY,
    SIGNAL_AWARD_CONCENTRATION,
    SIGNAL_CANCELLATION_LINK,
    SIGNAL_EXPLICIT_REJECTION,
    SIGNAL_HIGH_FREQ_COOCCURRENCE,
    SIGNAL_INFO_CONFLICT,
    STRICT_TERM_CONCENTRATION,
    ObservationSignal,
)


import re

# 金额数值提取正则：匹配整数/小数/科学计数法，忽略前后非数字字符
_AMOUNT_RE = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def _parse_amount(value) -> float:
    """从各种格式提取金额数值（float）。

    兼容 '209.7000000（万元）' / '500.0' / 500.0 / None / '' 等。
    """
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
    s = str(value).strip()
    if not s:
        return 0.0
    s = s.replace(",", "")
    m = _AMOUNT_RE.search(s)
    if not m:
        return 0.0
    try:
        return float(m.group(0))
    except (TypeError, ValueError):
        return 0.0


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
        _parse_amount(r.get("win_amount", 0)) for r in recent_records
    )

    # 金额趋势（按月统计）
    monthly_trend = {}
    for rec in recent_records:
        win_date_str = rec.get("win_date") or rec.get("award_date") or ""
        if not win_date_str:
            continue
        try:
            month_key = win_date_str[:7]  # YYYY-MM
            monthly_trend[month_key] = monthly_trend.get(month_key, 0) + _parse_amount(
                rec.get("win_amount", 0)
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
