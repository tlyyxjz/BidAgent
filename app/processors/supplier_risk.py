"""W3-03 供应商风险分析引擎。

对应总规划 v4.1 第八章「基础组织实体活动画像」+ 第九章「供应商画像」。

核心职责：
1. 基于历史中标记录生成供应商风险评分
2. 风险维度：集中度风险 / 金额异常 / 频率异常 / 地域集中 / 采购人集中
3. 风险等级：low / medium / high
4. 已接入 finance_agent.py 的 _run_supplier_analysis（W3-03 完成）

W3 周验收要求：基础组织实体活动画像

工程约束：
- 纯函数 + 数据类，不绑定数据库类型
- 风险评分基于历史数据统计（不依赖 LLM）
- 评分阈值可配置
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.models.organization import SupplierProfile, build_supplier_profile
from app.utils.logger import get_logger

logger = get_logger("supplier_risk")


# ========== 风险等级枚举 ==========

RISK_LEVEL_LOW = "low"
RISK_LEVEL_MEDIUM = "medium"
RISK_LEVEL_HIGH = "high"

VALID_RISK_LEVELS = (RISK_LEVEL_LOW, RISK_LEVEL_MEDIUM, RISK_LEVEL_HIGH)

# 风险评分阈值（总分 0-100）
RISK_SCORE_LOW_MAX = 30      # ≤30 → low
RISK_SCORE_MEDIUM_MAX = 60   # 31-60 → medium
# >60 → high


# ========== 风险维度 ==========

RISK_DIMENSION_CONCENTRATION = "concentration"      # 集中度风险
RISK_DIMENSION_AMOUNT_ANOMALY = "amount_anomaly"    # 金额异常
RISK_DIMENSION_FREQUENCY_ANOMALY = "frequency"      # 频率异常
RISK_DIMENSION_REGION_CONCENTRATION = "region"      # 地域集中
RISK_DIMENSION_PURCHASER_CONCENTRATION = "purchaser"  # 采购人集中


@dataclass
class RiskDimension:
    """风险维度评分。"""
    name: str
    score: float  # 0-100
    level: str    # low/medium/high
    reason: str
    details: dict = field(default_factory=dict)


@dataclass
class SupplierRiskResult:
    """供应商风险分析结果。"""
    organization_id: str
    normalized_name: str
    # 总分（0-100，越高风险越大）
    total_score: float
    # 总风险等级
    risk_level: str
    # 各维度评分
    dimensions: list = field(default_factory=list)
    # 供应商画像
    profile: Optional[SupplierProfile] = None
    # 分析时间
    analyzed_at: str = ""
    # 风险摘要
    summary: str = ""


# ========== 风险维度计算 ==========

def _score_to_level(score: float) -> str:
    """评分转风险等级。"""
    if score <= RISK_SCORE_LOW_MAX:
        return RISK_LEVEL_LOW
    if score <= RISK_SCORE_MEDIUM_MAX:
        return RISK_LEVEL_MEDIUM
    return RISK_LEVEL_HIGH


def assess_concentration_risk(
    win_count: int,
    total_amount: float,
    avg_amount_threshold: float = 5000000,  # 平均中标金额阈值（500 万）
) -> RiskDimension:
    """集中度风险：中标次数少 + 单笔金额大 → 高风险。

    场景：新供应商突然中大单，可能是围标/串标。

    Args:
        win_count: 中标总次数
        total_amount: 累计中标金额
        avg_amount_threshold: 平均金额阈值（超过则风险加分）

    Returns:
        RiskDimension
    """
    if win_count == 0:
        return RiskDimension(
            name=RISK_DIMENSION_CONCENTRATION,
            score=0, level=RISK_LEVEL_LOW,
            reason="无中标记录", details={"win_count": 0}
        )

    avg_amount = total_amount / win_count
    score = 0.0
    reason_parts = []

    # 中标次数少 + 平均金额大 → 高风险
    if win_count <= 2 and avg_amount > avg_amount_threshold:
        score = 70
        reason_parts.append(f"中标次数少({win_count})且平均金额大({avg_amount:.0f})")
    elif win_count <= 5 and avg_amount > avg_amount_threshold * 2:
        score = 60
        reason_parts.append(f"中标次数少({win_count})且平均金额极大({avg_amount:.0f})")
    elif win_count >= 10:
        score = 10
        reason_parts.append(f"中标次数多({win_count})，风险低")
    else:
        score = 30
        reason_parts.append(f"中标次数适中({win_count})")

    return RiskDimension(
        name=RISK_DIMENSION_CONCENTRATION,
        score=min(score, 100),
        level=_score_to_level(score),
        reason="; ".join(reason_parts),
        details={"win_count": win_count, "avg_amount": avg_amount},
    )


def assess_amount_anomaly(
    win_records: list,
    outlier_factor: float = 3.0,
) -> RiskDimension:
    """金额异常风险：单笔金额显著高于历史均值 → 高风险。

    Args:
        win_records: 中标记录列表（含 win_amount 字段）
        outlier_factor: 离群因子（金额超过均值 N 倍视为异常）

    Returns:
        RiskDimension
    """
    amounts = []
    for rec in win_records:
        try:
            amt = float(rec.get("win_amount", 0) or 0)
            if amt > 0:
                amounts.append(amt)
        except (ValueError, TypeError):
            pass

    if not amounts:
        return RiskDimension(
            name=RISK_DIMENSION_AMOUNT_ANOMALY,
            score=0, level=RISK_LEVEL_LOW,
            reason="无有效金额数据", details={}
        )

    avg = sum(amounts) / len(amounts)
    max_amt = max(amounts)

    score = 0.0
    reason_parts = [f"平均金额 {avg:.0f}，最大金额 {max_amt:.0f}"]

    if avg > 0 and max_amt > avg * outlier_factor:
        # 最大金额显著高于均值
        ratio = max_amt / avg
        score = min(80, 40 + (ratio - outlier_factor) * 10)
        reason_parts.append(f"最大金额是均值的 {ratio:.1f} 倍，异常")
    elif len(amounts) >= 3:
        # 检查金额方差
        variance = sum((a - avg) ** 2 for a in amounts) / len(amounts)
        std_dev = variance ** 0.5
        if avg > 0 and std_dev / avg > 0.5:
            score = 40
            reason_parts.append(f"金额波动大(标准差 {std_dev:.0f})")
        else:
            score = 15
            reason_parts.append("金额稳定")
    else:
        score = 25
        reason_parts.append("样本不足，无法判断")

    return RiskDimension(
        name=RISK_DIMENSION_AMOUNT_ANOMALY,
        score=score,
        level=_score_to_level(score),
        reason="; ".join(reason_parts),
        details={"avg": avg, "max": max_amt, "count": len(amounts)},
    )


def assess_frequency_anomaly(
    win_records: list,
    high_frequency_threshold: int = 10,  # 同年中标 ≥10 次视为高频
) -> RiskDimension:
    """频率异常风险：同年中标次数过多 → 可能围标/陪标。

    Args:
        win_records: 中标记录列表（含 win_date 字段）
        high_frequency_threshold: 高频阈值

    Returns:
        RiskDimension
    """
    from collections import Counter

    year_counts = Counter()
    for rec in win_records:
        date_str = rec.get("win_date", "")
        if date_str and len(date_str) >= 4:
            year = date_str[:4]
            year_counts[year] += 1

    if not year_counts:
        return RiskDimension(
            name=RISK_DIMENSION_FREQUENCY_ANOMALY,
            score=0, level=RISK_LEVEL_LOW,
            reason="无日期数据", details={}
        )

    max_year_count = max(year_counts.values())
    score = 0.0
    reason_parts = [f"最高同年中标次数 {max_year_count}"]

    if max_year_count >= high_frequency_threshold:
        score = 70
        reason_parts.append(f"超过高频阈值({high_frequency_threshold})，疑似围标")
    elif max_year_count >= high_frequency_threshold // 2:
        score = 40
        reason_parts.append("中标频率较高")
    else:
        score = 15
        reason_parts.append("中标频率正常")

    return RiskDimension(
        name=RISK_DIMENSION_FREQUENCY_ANOMALY,
        score=score,
        level=_score_to_level(score),
        reason="; ".join(reason_parts),
        details={"year_counts": dict(year_counts), "max_year_count": max_year_count},
    )


def assess_region_concentration(
    win_records: list,
    concentration_threshold: float = 0.8,  # 单一地区占比 ≥80% 视为集中
) -> RiskDimension:
    """地域集中风险：中标项目集中在单一地区 → 可能地方保护。

    Args:
        win_records: 中标记录列表（含 region 字段）
        concentration_threshold: 集中度阈值

    Returns:
        RiskDimension
    """
    from collections import Counter

    regions = [rec.get("region", "") for rec in win_records if rec.get("region")]
    if not regions:
        return RiskDimension(
            name=RISK_DIMENSION_REGION_CONCENTRATION,
            score=0, level=RISK_LEVEL_LOW,
            reason="无地区数据", details={}
        )

    region_counts = Counter(regions)
    total = len(regions)
    top_region, top_count = region_counts.most_common(1)[0]
    concentration = top_count / total

    score = 0.0
    reason_parts = [f"主要地区 {top_region} 占比 {concentration:.0%}"]

    if concentration >= concentration_threshold and total >= 5:
        score = 55
        reason_parts.append("地域高度集中，疑似地方保护")
    elif concentration >= concentration_threshold:
        score = 35
        reason_parts.append("地域集中但样本少")
    elif len(region_counts) >= 3:
        score = 10
        reason_parts.append(f"地域分散({len(region_counts)}个地区)")
    else:
        score = 25

    return RiskDimension(
        name=RISK_DIMENSION_REGION_CONCENTRATION,
        score=score,
        level=_score_to_level(score),
        reason="; ".join(reason_parts),
        details={"region_counts": dict(region_counts), "top_region": top_region},
    )


def assess_purchaser_concentration(
    win_records: list,
    concentration_threshold: float = 0.8,
) -> RiskDimension:
    """采购人集中风险：中标项目集中在单一采购人 → 可能利益输送。

    Args:
        win_records: 中标记录列表（含 purchaser_name 字段）
        concentration_threshold: 集中度阈值

    Returns:
        RiskDimension
    """
    from collections import Counter

    purchasers = [rec.get("purchaser_name", "") for rec in win_records if rec.get("purchaser_name")]
    if not purchasers:
        return RiskDimension(
            name=RISK_DIMENSION_PURCHASER_CONCENTRATION,
            score=0, level=RISK_LEVEL_LOW,
            reason="无采购人数据", details={}
        )

    purchaser_counts = Counter(purchasers)
    total = len(purchasers)
    top_purchaser, top_count = purchaser_counts.most_common(1)[0]
    concentration = top_count / total

    score = 0.0
    reason_parts = [f"主要采购人 {top_purchaser} 占比 {concentration:.0%}"]

    if concentration >= concentration_threshold and total >= 5:
        score = 65
        reason_parts.append("采购人高度集中，疑似利益输送")
    elif concentration >= concentration_threshold:
        score = 40
    elif len(purchaser_counts) >= 3:
        score = 12
        reason_parts.append(f"采购人分散({len(purchaser_counts)}个)")
    else:
        score = 28

    return RiskDimension(
        name=RISK_DIMENSION_PURCHASER_CONCENTRATION,
        score=score,
        level=_score_to_level(score),
        reason="; ".join(reason_parts),
        details={"purchaser_counts": dict(purchaser_counts), "top_purchaser": top_purchaser},
    )


# ========== 主分析函数 ==========

def analyze_supplier(
    organization_id: str,
    normalized_name: str,
    win_records: list,
) -> SupplierRiskResult:
    """供应商风险分析主函数（已接入 finance_agent.py，W3-03 完成）。

    流程：
    1. 生成供应商画像（build_supplier_profile）
    2. 评估各风险维度
    3. 汇总总分 + 总风险等级

    Args:
        organization_id: 组织 ID
        normalized_name: 规范化名称
        win_records: 中标记录列表

    Returns:
        SupplierRiskResult
    """
    # 1. 生成画像
    profile = build_supplier_profile(organization_id, normalized_name, win_records)

    # 2. 评估各维度
    dimensions = [
        assess_concentration_risk(profile.win_count, profile.total_win_amount),
        assess_amount_anomaly(win_records),
        assess_frequency_anomaly(win_records),
        assess_region_concentration(win_records),
        assess_purchaser_concentration(win_records),
    ]

    # 3. 汇总（加权平均，集中度和采购人集中权重高）
    weights = {
        RISK_DIMENSION_CONCENTRATION: 0.25,
        RISK_DIMENSION_AMOUNT_ANOMALY: 0.20,
        RISK_DIMENSION_FREQUENCY_ANOMALY: 0.20,
        RISK_DIMENSION_REGION_CONCENTRATION: 0.15,
        RISK_DIMENSION_PURCHASER_CONCENTRATION: 0.20,
    }

    total_score = 0.0
    for dim in dimensions:
        total_score += dim.score * weights.get(dim.name, 0.2)
    total_score = min(total_score, 100)
    risk_level = _score_to_level(total_score)

    # 摘要
    high_risk_dims = [d for d in dimensions if d.level == RISK_LEVEL_HIGH]
    summary_parts = [f"总分 {total_score:.1f} ({risk_level})"]
    if high_risk_dims:
        summary_parts.append(f"高风险维度: {', '.join(d.name for d in high_risk_dims)}")
    else:
        summary_parts.append("无高风险维度")

    return SupplierRiskResult(
        organization_id=organization_id,
        normalized_name=normalized_name,
        total_score=total_score,
        risk_level=risk_level,
        dimensions=dimensions,
        profile=profile,
        analyzed_at=datetime.utcnow().isoformat(),
        summary="; ".join(summary_parts),
    )
