"""W3-06 Bootstrap 置信区间计算。

对应总规划 v4.1 第十章 10.10 节：为关键评测指标计算 95% 置信区间。

Bootstrap 以采购项目为最小重采样单元（不按单个字段独立采样），
同一项目的公告、版本和字段整体参与采样。

按 project_id 分组（v4.1 10.10：同一项目的公告、版本和字段整体参与采样）。
若 doc_metrics 中无 project_id 字段或值为空，归入 "__ungrouped__" 组。

实现：纯 Python 无 numpy 依赖（如需 numpy 加速可在 docstring 中注明）。
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Any


def _percentile(values: list[float], pct: float) -> float:
    """线性插值法计算分位数（与 numpy.percentile 默认 method='linear' 一致）。

    Args:
        values: 数值列表（无需预先排序）
        pct: 百分位，0~100

    Returns:
        分位数值
    """
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    rank = (pct / 100.0) * (n - 1)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return sorted_vals[lo]
    frac = rank - lo
    return sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo])


def _aggregate_metric(
    docs: list[dict], metric_key: str
) -> float:
    """按「先求和再相除」口径计算指标。

    与 OverallMetric 保持一致：
    - recall = sum(fields_found) / sum(fields_present)
    - precision = sum(evidences_matched) / sum(evidences_pred)
    - iou_avg = sum(iou_list_matched) / sum(evidences_pred)

    Args:
        docs: doc_metrics 子集
        metric_key: 指标名

    Returns:
        指标值（分母为 0 时返回 0.0）
    """
    if metric_key == "recall":
        num = sum(float(d.get("fields_found", 0)) for d in docs)
        den = sum(float(d.get("fields_present", 0)) for d in docs)
        return num / den if den > 0 else 0.0
    if metric_key == "precision":
        num = sum(float(d.get("evidences_matched", 0)) for d in docs)
        den = sum(float(d.get("evidences_pred", 0)) for d in docs)
        return num / den if den > 0 else 0.0
    if metric_key == "iou_avg":
        # iou_list_matched 可能是 list(逐条 IoU)或标量,需兼容
        num = 0.0
        for d in docs:
            v = d.get("iou_list_matched", 0)
            if isinstance(v, list):
                num += sum(float(x) for x in v)
            else:
                num += float(v)
        den = sum(float(d.get("evidences_pred", 0)) for d in docs)
        return num / den if den > 0 else 0.0
    # 通用 fallback：逐篇 sum / n
    vals = [float(d.get(metric_key, 0)) for d in docs]
    return sum(vals) / len(vals) if vals else 0.0


def bootstrap_ci(
    doc_metrics: list[dict],
    metric_keys: list[str],
    n_bootstrap: int = 1000,
    confidence: float = 0.95,
    random_seed: int = 42,
    group_key: str = "project_id",
) -> dict[str, Any]:
    """Bootstrap 置信区间计算。

    算法步骤（v4.1 10.10）：
    1. 分组：按 group_key 将 doc_metrics 分组（组是最小重采样单元）
    2. 点估计：全量计算每个指标
    3. Bootstrap 循环：有放回采样 n_groups 个组 → 拼接组内 doc → 重算指标
    4. 置信区间：对每个指标采样值排序取 (1-confidence)/2 和 1-(1-confidence)/2 分位数

    Args:
        doc_metrics: 逐篇指标列表（W3-03 报告 doc_metrics 结构）
        metric_keys: 需要计算 CI 的指标名，如 ["recall", "precision", "iou_avg"]
        n_bootstrap: 采样次数（默认 1000）
        confidence: 置信水平（默认 0.95，即 95%）
        random_seed: 随机种子（保证可复现，必须记录在 meta 中）
        group_key: 分组字段（默认 project_id，符合 v4.1 10.10 以采购项目为最小重采样单元）

    Returns:
        {
            "<metric_name>": {
                "point_estimate": float,   # 点估计
                "ci_lower": float,         # 置信下界
                "ci_upper": float,         # 置信上界
                "bootstrap_samples": [float, ...],  # 全部采样值（长度 n_bootstrap）
            },
            "meta": {
                "n_bootstrap": int,
                "confidence": float,
                "random_seed": int,
                "group_key": str,
                "n_groups": int,
                "n_docs": int,
            }
        }
    """
    rng = random.Random(random_seed)
    n_docs = len(doc_metrics)

    # Step 1: 按 group_key 分组（缺失 key 归入 "__ungrouped__"）
    groups: dict[str, list[dict]] = defaultdict(list)
    for d in doc_metrics:
        g = d.get(group_key, "__ungrouped__")
        groups[str(g)].append(d)
    group_names = list(groups.keys())
    n_groups = len(group_names)

    # Step 2: 点估计（全量 docs）
    result: dict[str, Any] = {}
    for mk in metric_keys:
        point = _aggregate_metric(doc_metrics, mk)
        result[mk] = {
            "point_estimate": point,
            "ci_lower": point,
            "ci_upper": point,
            "bootstrap_samples": [point] * n_bootstrap,
        }

    if n_groups <= 1 or n_docs == 0 or n_bootstrap <= 0:
        # 边界：单组/无数据/无采样 → CI 退化为点估计
        result["meta"] = {
            "n_bootstrap": max(n_bootstrap, 0),
            "confidence": confidence,
            "random_seed": random_seed,
            "group_key": group_key,
            "n_groups": n_groups,
            "n_docs": n_docs,
        }
        return result

    # Step 3: Bootstrap 循环
    alpha = 1.0 - confidence
    low_pct = (alpha / 2.0) * 100.0
    high_pct = (1.0 - alpha / 2.0) * 100.0

    sample_buckets: dict[str, list[float]] = {mk: [] for mk in metric_keys}
    for _ in range(n_bootstrap):
        # 有放回采样 n_groups 个组
        resampled_docs: list[dict] = []
        for _i in range(n_groups):
            gname = group_names[rng.randrange(n_groups)]
            resampled_docs.extend(groups[gname])
        # 重新计算各指标
        for mk in metric_keys:
            sample_buckets[mk].append(_aggregate_metric(resampled_docs, mk))

    # Step 4: 置信区间
    for mk in metric_keys:
        samples = sample_buckets[mk]
        point = result[mk]["point_estimate"]
        ci_lo = _percentile(samples, low_pct)
        ci_hi = _percentile(samples, high_pct)
        # 合理性保证（理论下界/上界 ≤ 或 ≥ 点估计，但分位数法不保证，夹逼修正）
        result[mk]["ci_lower"] = min(ci_lo, point)
        result[mk]["ci_upper"] = max(ci_hi, point)
        result[mk]["bootstrap_samples"] = samples

    result["meta"] = {
        "n_bootstrap": n_bootstrap,
        "confidence": confidence,
        "random_seed": random_seed,
        "group_key": group_key,
        "n_groups": n_groups,
        "n_docs": n_docs,
    }
    return result
