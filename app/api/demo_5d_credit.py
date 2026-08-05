"""W3 Demo 5 维度公开活动观察度（v4.1 第九章）。

对齐 app/processors/observation_signals.py 口径：
1. 集中度（concentration）25%
2. 金额异常（amount_anomaly）20%
3. 频率异常（frequency）20%
4. 地域集中（region）15%
5. 采购人集中（purchaser）20%

注：本接口为公开活动观察度（0-100），不输出信用评分（v4.1 §9.1）。
"""

from __future__ import annotations

# 按名称索引的组织库（name -> org_id + 元数据），支持大小写/模糊匹配前缀命中
_ORG_INDEX: dict[str, dict] = {
    "北京大学第三医院": {
        "org_id": "org_001",
        "org_type": "医疗机构",
        "region": "北京市海淀区",
        "total_projects": 347,
        "total_amount_yuan": 6_248_900_000,
        "award_win_rate": 0.386,
        "active_days_30d": 18,
        "amount_consistency_score": 92.4,
        "type_coverage_count": 7,
    },
    "北第三医院": {
        "org_id": "org_001",
        "org_type": "医疗机构",
        "region": "北京市海淀区",
        "total_projects": 347,
        "total_amount_yuan": 6_248_900_000,
        "award_win_rate": 0.386,
        "active_days_30d": 18,
        "amount_consistency_score": 92.4,
        "type_coverage_count": 7,
    },
    "中国科学院计算技术研究所": {
        "org_id": "org_002",
        "org_type": "科研机构",
        "region": "北京市海淀区中关村",
        "total_projects": 512,
        "total_amount_yuan": 9_124_500_000,
        "award_win_rate": 0.312,
        "active_days_30d": 22,
        "amount_consistency_score": 95.7,
        "type_coverage_count": 9,
    },
    "北京市教育委员会": {
        "org_id": "org_003",
        "org_type": "政府机关",
        "region": "北京市西城区",
        "total_projects": 1_248,
        "total_amount_yuan": 28_740_000_000,
        "award_win_rate": 0.245,
        "active_days_30d": 27,
        "amount_consistency_score": 89.1,
        "type_coverage_count": 12,
    },
    "北京协和医院": {
        "org_id": "org_004",
        "org_type": "医疗机构",
        "region": "北京市东城区",
        "total_projects": 420,
        "total_amount_yuan": 7_890_000_000,
        "award_win_rate": 0.402,
        "active_days_30d": 20,
        "amount_consistency_score": 93.8,
        "type_coverage_count": 8,
    },
    "上海市教育委员会": {
        "org_id": "org_005",
        "org_type": "政府机关",
        "region": "上海市黄浦区",
        "total_projects": 1_096,
        "total_amount_yuan": 24_580_000_000,
        "award_win_rate": 0.261,
        "active_days_30d": 26,
        "amount_consistency_score": 88.3,
        "type_coverage_count": 11,
    },
    "深圳卫健委": {
        "org_id": "org_006",
        "org_type": "政府机关",
        "region": "深圳市福田区",
        "total_projects": 288,
        "total_amount_yuan": 4_120_000_000,
        "award_win_rate": 0.334,
        "active_days_30d": 16,
        "amount_consistency_score": 90.5,
        "type_coverage_count": 6,
    },
}


def _build_5d_credit(meta: dict) -> list[dict]:
    """5 维度公开活动观察度（对齐 observation_signals.py 口径，v4.1 第九章）。

    维度名 / 权重对齐 app/processors/observation_signals.py：
    1. 集中度（concentration）25%
    2. 金额异常（amount_anomaly）20%
    3. 频率异常（frequency）20%
    4. 地域集中（region）15%
    5. 采购人集中（purchaser）20%

    注：本接口为公开活动观察度（0-100），不输出信用评分（v4.1 §9.1）。
    meta 字段有限，部分维度以代理指标估算。
    """
    # v4.1 §9.1: 不输出信用评分，score/grade 设为 None，仅保留观察值
    def _grade(s: float) -> str:
        if s >= 85:
            return "high"
        if s >= 70:
            return "medium"
        return "low"

    # 集中度：中标次数多 → 集中度风险低 → 公开活动观察度高（代理：total_projects）
    conc = min(100.0, 55 + (meta["total_projects"] / 1_200.0) * 45.0)
    # 金额异常：金额一致性高 → 异常低 → 公开活动观察度高
    amt = meta["amount_consistency_score"]
    # 频率异常：稳定活跃 → 频率正常 → 公开活动观察度高（代理：active_days_30d）
    freq = 50 + (meta["active_days_30d"] / 30.0) * 50.0
    # 地域集中：类型覆盖广 → 地域分散 → 公开活动观察度高（代理：type_coverage_count）
    reg = 50 + (meta["type_coverage_count"] / 12.0) * 50.0
    # 采购人集中：健康中标率 → 采购人分散 → 公开活动观察度高（代理：award_win_rate）
    pur = min(100.0, 50 + meta["award_win_rate"] / 0.45 * 50)
    dims = [
        {
            "key": "concentration",
            "name": "集中度",
            "icon": "ph-graph",
            "score": None,
            "grade": None,
            "display": f"{meta['total_projects']:,} 个",
            "description": "中标次数分散度，次数越多集中度风险越低（对齐 observation_signals.py）",
        },
        {
            "key": "amount_anomaly",
            "name": "金额异常",
            "icon": "ph-shield-check",
            "score": None,
            "grade": None,
            "display": f"{amt:.1f} / 100",
            "description": "金额一致性，越高异常越低（对齐 observation_signals.py）",
        },
        {
            "key": "frequency",
            "name": "频率异常",
            "icon": "ph-clock",
            "score": None,
            "grade": None,
            "display": f"{meta['active_days_30d']} / 30 天",
            "description": "中标频率稳定性，活跃越稳定频率异常越低（对齐 observation_signals.py）",
        },
        {
            "key": "region",
            "name": "地域集中",
            "icon": "ph-bezier-curve",
            "score": None,
            "grade": None,
            "display": f"{meta['type_coverage_count']} / 12 类",
            "description": "地域分散度（以公告类型覆盖度代理），越分散越低（对齐 observation_signals.py）",
        },
        {
            "key": "purchaser",
            "name": "采购人集中",
            "icon": "ph-trophy",
            "score": None,
            "grade": None,
            "display": f"{meta['award_win_rate'] * 100:.1f}%",
            "description": "采购人分散度（以中标率代理），越分散越低（对齐 observation_signals.py）",
        },
    ]
    return dims


def _build_5d_credit_no_data() -> list[dict]:
    """真实数据未命中时返回的 5 维度占位（score 全部为 None，不伪造数字）。

    5 维度对齐 observation_signals.py 口径（v4.1 第九章）。
    """
    _reason = "暂无真实数据，以下为演示样例"
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


def _find_org_meta(name: str) -> dict | None:
    """按名称匹配：精确 > 包含 > 前缀 命中。"""
    if not name:
        return None
    if name in _ORG_INDEX:
        return _ORG_INDEX[name]
    lower = name.strip()
    # 包含匹配
    for k, v in _ORG_INDEX.items():
        if lower in k or k in lower:
            return v
    # 前缀
    prefix_max = 0
    best: dict | None = None
    for k, v in _ORG_INDEX.items():
        lcp = 0
        for a, b in zip(lower, k):
            if a == b:
                lcp += 1
            else:
                break
        if lcp > prefix_max and lcp >= 2:
            prefix_max = lcp
            best = v
    return best
