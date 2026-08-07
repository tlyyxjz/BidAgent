"""W3 Demo 5 维度公开活动观察度（v4.1 第九章）。

对齐 app/processors/observation_signals.py 口径：
1. 集中度（concentration）25%
2. 金额异常（amount_anomaly）20%
3. 频率异常（frequency）20%
4. 地域集中（region）15%
5. 采购人集中（purchaser）20%

注：本接口为公开活动观察度（0-100），不输出信用评分（v4.1 §9.1）。

数据诚实性：本模块不再维护预置组织样本库（_ORG_INDEX 已移除）。
5 维度展示值全部由真实 DB 聚合的 meta 驱动；meta 缺值时展示 "--"，不伪造数字。
"""

from __future__ import annotations


def _build_5d_credit(meta: dict) -> list[dict]:
    """5 维度公开活动观察度（对齐 observation_signals.py 口径，v4.1 第九章）。

    维度名 / 权重对齐 app/processors/observation_signals.py：
    1. 集中度（concentration）25%
    2. 金额异常（amount_anomaly）20%
    3. 频率异常（frequency）20%
    4. 地域集中（region）15%
    5. 采购人集中（purchaser）20%

    注：本接口为公开活动观察度（0-100），不输出信用评分（v4.1 §9.1）。
    meta 字段有限，部分维度以代理指标估算；缺值展示 "--"（不伪造）。
    """
    # v4.1 §9.1: 不输出信用评分，score/grade 设为 None，仅保留观察值
    total_projects = meta.get("total_projects") or 0
    amt_yuan = meta.get("total_amount_yuan")
    active_days = meta.get("active_days_30d") or 0
    type_coverage = meta.get("type_coverage_count") or 0
    win_rate = meta.get("award_win_rate")

    # 金额维度：展示可观察的事实量（累计中标金额），无真实金额时展示 "--"
    amt_display = f"{amt_yuan / 1e8:.2f} 亿元" if amt_yuan else "--"
    # 采购人集中：中标率缺失时展示 "--"（不伪造代理值）
    pur_display = f"{win_rate * 100:.1f}%" if win_rate is not None else "--"

    dims = [
        {
            "key": "concentration",
            "name": "集中度",
            "icon": "ph-graph",
            "score": None,
            "grade": None,
            "display": f"{total_projects:,} 个",
            "description": "中标次数分散度，次数越多集中度风险越低（对齐 observation_signals.py）",
        },
        {
            "key": "amount_anomaly",
            "name": "金额异常",
            "icon": "ph-shield-check",
            "score": None,
            "grade": None,
            "display": amt_display,
            "description": "累计中标金额（仅统计该组织作为中标人的公告实测值）",
        },
        {
            "key": "frequency",
            "name": "频率异常",
            "icon": "ph-clock",
            "score": None,
            "grade": None,
            "display": f"{active_days} / 30 天",
            "description": "中标频率稳定性，活跃越稳定频率异常越低（对齐 observation_signals.py）",
        },
        {
            "key": "region",
            "name": "地域集中",
            "icon": "ph-bezier-curve",
            "score": None,
            "grade": None,
            "display": f"{type_coverage} / 12 类",
            "description": "地域分散度（以公告类型覆盖度代理），越分散越低（对齐 observation_signals.py）",
        },
        {
            "key": "purchaser",
            "name": "采购人集中",
            "icon": "ph-trophy",
            "score": None,
            "grade": None,
            "display": pur_display,
            "description": "采购人分散度（以中标率代理），越分散越低（对齐 observation_signals.py）",
        },
    ]
    return dims


def _build_5d_credit_no_data() -> list[dict]:
    """真实数据未命中时返回的 5 维度占位（score 全部为 None，不伪造数字）。

    5 维度对齐 observation_signals.py 口径（v4.1 第九章）。
    """
    _reason = "当前采集库中无该组织的公开记录"
    return [
        {"key": "concentration", "name": "集中度", "icon": "ph-graph",
         "score": None, "grade": None, "display": "--", "reason": _reason,
         "description": "中标次数少 + 单笔金额大 → 高风险"},
        {"key": "amount_anomaly", "name": "金额异常", "icon": "ph-shield-check",
         "score": None, "grade": None, "display": "--", "reason": _reason,
         "description": "单笔金额显著高于历史均值 → 高风险"},
        {"key": "frequency", "name": "频率异常", "icon": "ph-clock",
         "score": None, "grade": None, "display": "--", "reason": _reason,
         "description": "同年中标次数过多 → 频率异常观察信号"},
        {"key": "region", "name": "地域集中", "icon": "ph-bezier-curve",
         "score": None, "grade": None, "display": "--", "reason": _reason,
         "description": "中标项目集中在单一地区 → 地域集中度观察信号"},
        {"key": "purchaser", "name": "采购人集中", "icon": "ph-trophy",
         "score": None, "grade": None, "display": "--", "reason": _reason,
         "description": "中标项目集中在单一采购人 → 可能利益输送"},
    ]
