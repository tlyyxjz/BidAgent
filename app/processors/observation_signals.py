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

本模块按功能职责拆分为子模块，此处通过 re-export 保持原公开接口兼容：
- observation_types：常量与数据结构
- observation_assessors：六个 MVP 信号计算函数

主入口 analyze_observation_signals 保留在本模块，以便 build_supplier_profile
的 monkeypatch 通过 app.processors.observation_signals.build_supplier_profile 生效。
"""
from __future__ import annotations

from datetime import datetime

from app.models.organization import SupplierProfile, build_supplier_profile
from app.utils.logger import get_logger

# re-export 子模块的全部公开与私有接口（保持向后兼容）
from app.processors.observation_types import (  # noqa: F401
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
)
from app.processors.observation_assessors import (  # noqa: F401
    _get_recent_records,
    assess_award_activity,
    assess_award_concentration,
    assess_cancellation_link,
    assess_explicit_rejection,
    assess_high_freq_cooccurrence,
    assess_info_conflict,
)

logger = get_logger("observation_signals")


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
