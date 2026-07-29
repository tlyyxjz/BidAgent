"""Bootstrap 置信区间计算。

对应 v4.1 第十章 10.10 节：以采购项目为最小重采样单元，报告 95% 置信区间。

W3 阶段限制：
- W3 数据无 project_id 字段，暂用 notice_type 分组（tender/award/correction 三组）
- 待数据库接入 project_id 后切换为按项目分组

算法：
1. 按 group_key 将 doc_metrics 分组
2. 点估计：全量聚合计算每个指标
3. Bootstrap 循环 n_bootstrap 次：有放回采样 n_groups 个组，重新计算指标
4. 置信区间：采样值排序，取 2.5% 和 97.5% 分位数
"""
from __future__ import annotations

import random
from collections import defaultdict
from typing import Any


def _group_docs(
    doc_metrics: list[dict],
    group_key: str,
) -> dict[str, list[dict]]:
    """按 group_key 分组。"""
    groups: dict[str, list[dict]] = defaultdict(list)
    for d in doc_metrics:
        groups[str(d.get(group_key, "unknown"))].append(d)
    return dict(groups)


def _aggregate_metric(docs: list[dict], metric_key: str) -> float:
    """全量聚合计算指标值（先求和再相除，与 OverallMetric 口径一致）。

    recall = sum(fields_found) / sum(fields_present)
    precision = sum(evidences_matched) / sum(evidences_pred)
    iou_avg = sum(iou_list_matched) / sum(evidences_pred)
    """
    if metric_key == "recall":
        num = sum(d.get("fields_found", 0) for d in docs)
        den = sum(d.get("fields_present", 0) for d in docs)
    elif metric_key == "precision":
        num = sum(d.get("evidences_matched", 0) for d in docs)
        den = sum(d.get("evidences_pred", 0) for d in docs)
    elif metric_key == "iou_avg":
        # iou_avg 的分母是 evidences_pred，分子是所有匹配IoU之和
        num = sum(sum(d.get("iou_list_matched", [])) for d in docs)
        den = sum(d.get("evidences_pred", 0) for d in docs)
    else:
        # 通用：取该字段值的均值
        vals = [d.get(metric_key, 0) for d in docs]
        return sum(vals) / len(vals) if vals else 0.0
    return round(num / den, 4) if den > 0 else 0.0


def bootstrap_ci(
    doc_metrics: list[dict],
    metric_keys: list[str],
    n_bootstrap: int = 1000,
    confidence: float = 0.95,
    random_seed: int = 42,
    group_key: str = "notice_type",
) -> dict:
    """Bootstrap 置信区间计算。

    Args:
        doc_metrics: 逐篇指标列表（来自 W3-03 报告的 doc_metrics）
        metric_keys: 需要计算 CI 的指标名
        n_bootstrap: 采样次数（默认 1000）
        confidence: 置信水平（默认 0.95）
        random_seed: 随机种子（必须记录，保证可复现）
        group_key: 分组字段（W3 无 project_id，暂用 notice_type）

    Returns:
        {
            metric_name: {
                "point_estimate": float,
                "ci_lower": float,
                "ci_upper": float,
                "bootstrap_samples": list[float],
            },
            "meta": {...}
        }
    """
    if not doc_metrics:
        return {"meta": {"error": "empty doc_metrics"}, "metrics": {}}

    rng = random.Random(random_seed)
    groups = _group_docs(doc_metrics, group_key)
    group_names = list(groups.keys())
    n_groups = len(group_names)

    # 点估计
    point_estimates: dict[str, float] = {}
    for mk in metric_keys:
        point_estimates[mk] = _aggregate_metric(doc_metrics, mk)

    # Bootstrap 循环
    bootstrap_samples: dict[str, list[float]] = {mk: [] for mk in metric_keys}
    for _ in range(n_bootstrap):
        # 有放回采样 n_groups 个组
        sampled_group_names = [rng.choice(group_names) for _ in range(n_groups)]
        sampled_docs: list[dict] = []
        for gn in sampled_group_names:
            sampled_docs.extend(groups[gn])
        for mk in metric_keys:
            val = _aggregate_metric(sampled_docs, mk)
            bootstrap_samples[mk].append(val)

    # 置信区间
    alpha = 1.0 - confidence
    lower_pct = alpha / 2 * 100
    upper_pct = (1 - alpha / 2) * 100

    result: dict[str, Any] = {"meta": {
        "n_bootstrap": n_bootstrap,
        "confidence": confidence,
        "random_seed": random_seed,
        "group_key": group_key,
        "n_groups": n_groups,
        "n_docs": len(doc_metrics),
        "groups": group_names,
    }, "metrics": {}}

    for mk in metric_keys:
        samples = sorted(bootstrap_samples[mk])
        ci_lower = samples[int(len(samples) * lower_pct / 100)]
        ci_upper = samples[int(len(samples) * upper_pct / 100) - 1]
        result["metrics"][mk] = {
            "point_estimate": point_estimates[mk],
            "ci_lower": round(ci_lower, 4),
            "ci_upper": round(ci_upper, 4),
            "bootstrap_samples": [round(x, 4) for x in bootstrap_samples[mk]],
        }

    return result


def run_from_report(
    report_path: str,
    output_path: str | None = None,
    metric_keys: list[str] | None = None,
    n_bootstrap: int = 1000,
    random_seed: int = 42,
) -> dict:
    """从 W3-03 评测报告读取数据并计算 CI。

    Args:
        report_path: W3-03 报告 JSON 路径
        output_path: 输出 JSON 路径（None 则不写文件）
        metric_keys: 指标列表，默认 recall/precision/iou_avg
        n_bootstrap: 采样次数
        random_seed: 随机种子

    Returns:
        CI 计算结果 dict
    """
    import json
    from pathlib import Path

    if metric_keys is None:
        metric_keys = ["recall", "precision", "iou_avg"]

    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)

    doc_metrics = report.get("doc_metrics", [])
    if not doc_metrics:
        raise ValueError(f"report.doc_metrics 为空: {report_path}")

    result = bootstrap_ci(
        doc_metrics=doc_metrics,
        metric_keys=metric_keys,
        n_bootstrap=n_bootstrap,
        random_seed=random_seed,
    )

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    return result
