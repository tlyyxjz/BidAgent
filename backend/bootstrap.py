"""BidAgent v4.1 Bootstrap 置信区间计算（W1-07 补丁）。

需求来源：
- v4.1 §10.10 要求"报告关键指标的 95% 置信区间"
- 之前 evaluate_dataset 只返回点估计，无置信区间
- 小样本场景下点估计不稳定，需 Bootstrap 给出区间估计

设计原则：
- **非侵入式**：作为独立模块，不修改 FieldMetrics / EvaluationSummary 结构
- **重抽样基于文档级**：以 document_id 为重抽样单元（非字段级），符合统计独立性假设
- **可复现**：固定 random_seed，结果可复现
- **百分位法**：取 2.5% 和 97.5% 分位数作为 95% CI

工程规范：
- 异步友好但本身 CPU 密集，需 run_in_executor（遵循项目硬约束）
- 结构化日志
- 不依赖 scipy/numpy，纯标准库实现
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field

from app.utils.logger import get_logger

from backend.evaluation import evaluate_dataset
from backend.schemas import (
    AnnotationDocument,
    EvaluationSummary,
    LLMExtractionRecord,
)

logger = get_logger("backend.bootstrap")


# ============================================================
# 数据结构
# ============================================================


@dataclass
class ConfidenceInterval:
    """置信区间 - 单指标的下界/上界/中位数。"""
    point_estimate: float
    ci_lower: float
    ci_upper: float
    bootstrap_samples: int
    confidence_level: float = 0.95

    def to_dict(self) -> dict[str, float | int]:
        return {
            "point_estimate": round(self.point_estimate, 4),
            "ci_lower": round(self.ci_lower, 4),
            "ci_upper": round(self.ci_upper, 4),
            "bootstrap_samples": self.bootstrap_samples,
            "confidence_level": self.confidence_level,
        }


@dataclass
class BootstrapResult:
    """完整 Bootstrap 结果 - 含字段级和宏平均 CI。"""
    field_precision: dict[str, ConfidenceInterval] = field(default_factory=dict)
    field_recall: dict[str, ConfidenceInterval] = field(default_factory=dict)
    field_f1: dict[str, ConfidenceInterval] = field(default_factory=dict)
    macro_precision: ConfidenceInterval | None = None
    macro_recall: ConfidenceInterval | None = None
    macro_f1: ConfidenceInterval | None = None
    bootstrap_samples: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "field_precision": {k: v.to_dict() for k, v in self.field_precision.items()},
            "field_recall": {k: v.to_dict() for k, v in self.field_recall.items()},
            "field_f1": {k: v.to_dict() for k, v in self.field_f1.items()},
            "macro_precision": self.macro_precision.to_dict() if self.macro_precision else None,
            "macro_recall": self.macro_recall.to_dict() if self.macro_recall else None,
            "macro_f1": self.macro_f1.to_dict() if self.macro_f1 else None,
            "bootstrap_samples": self.bootstrap_samples,
        }


# ============================================================
# 核心实现
# ============================================================


def _percentile(values: list[float], p: float) -> float:
    """计算百分位数（线性插值法）。

    Args:
        values: 已排序或未排序的数值列表
        p: 百分位（0-100），如 2.5 表示 2.5%
    """
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    # 线性插值
    rank = (p / 100.0) * (n - 1)
    lower_idx = int(rank)
    upper_idx = min(lower_idx + 1, n - 1)
    frac = rank - lower_idx
    return sorted_vals[lower_idx] * (1 - frac) + sorted_vals[upper_idx] * frac


def _extract_metrics(summary: EvaluationSummary) -> dict[str, dict[str, float]]:
    """从 EvaluationSummary 提取每字段的 P/R/F1，便于重抽样统计。"""
    per_field: dict[str, dict[str, float]] = {}
    for m in summary.field_metrics:
        per_field[m.field_name] = {
            "precision": m.precision,
            "recall": m.recall,
            "f1": m.f1,
        }
    return per_field


def _resample_pairs(
    pairs: list[tuple[AnnotationDocument, LLMExtractionRecord | None]],
    rng: random.Random,
) -> list[tuple[AnnotationDocument, LLMExtractionRecord | None]]:
    """修复：配对重抽样 (gold, system) 一起重抽，保持对应关系。

    标准配对 bootstrap：把 (gold_doc, system_record) 作为整体单元重抽样，
    避免之前只重抽样 gold_docs 导致 document_id 失配的问题。
    """
    if not pairs:
        return []
    n = len(pairs)
    return [rng.choice(pairs) for _ in range(n)]


def _summarize_resample(
    gold_docs: list[AnnotationDocument],
    system_records: list[LLMExtractionRecord],
    system_identifier: str,
    dataset_split: str,
    run_id: str,
) -> dict[str, dict[str, float]]:
    """对一次重抽样运行 evaluate_dataset，返回字段级 P/R/F1。"""
    summary = evaluate_dataset(
        gold_docs=gold_docs,
        system_records=system_records,
        system_identifier=system_identifier,
        dataset_split=dataset_split,
        run_id=run_id,
    )
    result = _extract_metrics(summary)
    # 加宏平均
    result["__macro__"] = {
        "precision": summary.macro_precision,
        "recall": summary.macro_recall,
        "f1": summary.macro_f1,
    }
    return result


def bootstrap_evaluate(
    gold_docs: list[AnnotationDocument],
    system_records: list[LLMExtractionRecord],
    system_identifier: str,
    dataset_split: str = "test",
    n_bootstrap: int = 1000,
    confidence_level: float = 0.95,
    random_seed: int = 42,
) -> BootstrapResult:
    """对数据集做 Bootstrap 重抽样，返回 95% 置信区间。

    Args:
        gold_docs: 金标文档列表
        system_records: 系统输出记录
        system_identifier: 系统标识
        dataset_split: 数据集划分
        n_bootstrap: 重抽样次数（默认 1000）
        confidence_level: 置信水平（默认 0.95）
        random_seed: 随机种子，保证可复现

    Returns:
        BootstrapResult 含每字段和宏平均的 CI

    Raises:
        ValueError: 数据为空或 n_bootstrap < 2
    """
    if not gold_docs:
        raise ValueError("gold_docs 不能为空")
    if n_bootstrap < 2:
        raise ValueError("n_bootstrap 必须 >= 2")
    if not 0 < confidence_level < 1:
        raise ValueError("confidence_level 必须在 (0, 1) 区间")

    rng = random.Random(random_seed)
    alpha = 1 - confidence_level
    lower_p = (alpha / 2) * 100
    upper_p = (1 - alpha / 2) * 100

    logger.info(
        "bootstrap start docs={} n_bootstrap={} confidence={} seed={}",
        len(gold_docs),
        n_bootstrap,
        confidence_level,
        random_seed,
    )

    # 修复：构造配对单元 (gold, system)，确保重抽样时保持对应关系
    sys_map: dict[str, LLMExtractionRecord] = {}
    for r in system_records:
        sys_map[r.document_id] = r
    pairs: list[tuple[AnnotationDocument, LLMExtractionRecord | None]] = [
        (g, sys_map.get(g.document_id)) for g in gold_docs
    ]

    # 先跑一次原始数据集得到点估计
    original_metrics = _summarize_resample(
        gold_docs, system_records, system_identifier, dataset_split, "bootstrap-original"
    )

    # 收集每次重抽样的指标
    # 结构：{field_name: {"precision": [v1, v2, ...], "recall": [...], "f1": [...]}}
    field_names = list(original_metrics.keys())
    metric_names = ("precision", "recall", "f1")
    samples: dict[str, dict[str, list[float]]] = {
        fn: {mn: [] for mn in metric_names} for fn in field_names
    }

    for i in range(n_bootstrap):
        # 修复：配对重抽样，保持 (gold, system) 对应关系
        resampled_pairs = _resample_pairs(pairs, rng)
        resampled_gold = [p[0] for p in resampled_pairs]
        # 收集非 None 的 system 记录（去重，因为重抽样可能产生重复）
        seen_sys_ids: set[str] = set()
        resampled_sys: list[LLMExtractionRecord] = []
        for _, sys_rec in resampled_pairs:
            if sys_rec is not None and sys_rec.document_id not in seen_sys_ids:
                resampled_sys.append(sys_rec)
                seen_sys_ids.add(sys_rec.document_id)
        try:
            metrics = _summarize_resample(
                resampled_gold,
                resampled_sys,
                system_identifier,
                dataset_split,
                f"bootstrap-{i}",
            )
        except Exception as exc:
            logger.warning(
                "bootstrap iter {} failed, skip: {}", i, exc
            )
            continue
        for fn in field_names:
            if fn not in metrics:
                continue
            for mn in metric_names:
                samples[fn][mn].append(metrics[fn][mn])

    # 构造置信区间
    result = BootstrapResult(bootstrap_samples=n_bootstrap)

    for fn in field_names:
        if fn == "__macro__":
            continue
        for mn in metric_names:
            vals = samples[fn][mn]
            if not vals:
                continue
            ci = ConfidenceInterval(
                point_estimate=original_metrics[fn][mn],
                ci_lower=_percentile(vals, lower_p),
                ci_upper=_percentile(vals, upper_p),
                bootstrap_samples=len(vals),
                confidence_level=confidence_level,
            )
            if mn == "precision":
                result.field_precision[fn] = ci
            elif mn == "recall":
                result.field_recall[fn] = ci
            elif mn == "f1":
                result.field_f1[fn] = ci

    # 宏平均 CI
    macro_samples = samples.get("__macro__", {})
    if macro_samples:
        for mn in metric_names:
            vals = macro_samples[mn]
            if not vals:
                continue
            ci = ConfidenceInterval(
                point_estimate=original_metrics["__macro__"][mn],
                ci_lower=_percentile(vals, lower_p),
                ci_upper=_percentile(vals, upper_p),
                bootstrap_samples=len(vals),
                confidence_level=confidence_level,
            )
            if mn == "precision":
                result.macro_precision = ci
            elif mn == "recall":
                result.macro_recall = ci
            elif mn == "f1":
                result.macro_f1 = ci

    logger.info(
        "bootstrap done samples={} macro_f1={:.4f} ci=[{:.4f}, {:.4f}]",
        n_bootstrap,
        result.macro_f1.point_estimate if result.macro_f1 else 0.0,
        result.macro_f1.ci_lower if result.macro_f1 else 0.0,
        result.macro_f1.ci_upper if result.macro_f1 else 0.0,
    )
    return result


async def bootstrap_evaluate_async(
    gold_docs: list[AnnotationDocument],
    system_records: list[LLMExtractionRecord],
    system_identifier: str,
    dataset_split: str = "test",
    n_bootstrap: int = 1000,
    confidence_level: float = 0.95,
    random_seed: int = 42,
) -> BootstrapResult:
    """异步版 Bootstrap - 将 CPU 密集的重抽样 offload 到线程池。

    遵循项目硬约束：异步函数中 CPU 密集任务用 run_in_executor。
    """
    return await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: bootstrap_evaluate(
            gold_docs=gold_docs,
            system_records=system_records,
            system_identifier=system_identifier,
            dataset_split=dataset_split,
            n_bootstrap=n_bootstrap,
            confidence_level=confidence_level,
            random_seed=random_seed,
        ),
    )


__all__ = [
    "BootstrapResult",
    "ConfidenceInterval",
    "bootstrap_evaluate",
    "bootstrap_evaluate_async",
]
