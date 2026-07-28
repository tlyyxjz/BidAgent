"""项目级 Bootstrap 置信区间计算 (v4.1 第 10.10 节).

核心约束 (project_memory):
- 以采购项目为最小重采样单元
- 同一项目的所有公告、版本和字段整体参与采样
- 不按单个字段独立采样
- 重复采样的项目保留独立采样 ID 和权重
- 使用 ulid-py 生成采样 ID（禁止截断 uuid4.hex）
- 95% CI: 2.5 和 97.5 分位
- 可复现：使用 numpy.random.Generator + seed

支持指标:
- field_precision: 字段精确率 (correct / evaluable)
- field_recall: 字段召回率 (correct / gold_present)
- evidence_precision: 证据精确率 (verified / has_evidence)
- unjustified_rate: 无依据输出率 (unjustified / with_value)
- iou_avg: IoU 平均值 (mean of iou values)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
import ulid


RULE_VERSION = "bootstrap_v1.0"


@dataclass(frozen=True)
class ProjectFieldPair:
    """项目-字段级评测对（Bootstrap 最小聚合单元内的字段记录）."""

    project_id: str
    doc_id: str
    field_name: str
    field_correct: bool
    field_present: bool
    has_value: bool
    has_evidence: bool
    evidence_verified: bool
    unjustified: bool
    evaluable: bool = True
    iou: Optional[float] = None


@dataclass
class BootstrapSample:
    """单次 Bootstrap 采样记录.

    重复采样的项目保留独立 sample_instance_id 和权重.
    """

    sample_instance_id: str
    project_id: str
    weight: int


@dataclass
class BootstrapResult:
    """Bootstrap 置信区间结果."""

    metric_name: str
    point_estimate: float
    ci_lower: float
    ci_upper: float
    n_resamples: int
    n_projects: int
    n_pairs: int
    seed: int
    rule_version: str
    samples: list = field(default_factory=list)
    sample_size_per_draw: int = 0


def _metric_field_precision(pairs):
    evaluable = [p for p in pairs if p.evaluable]
    if not evaluable:
        return 0.0
    correct = sum(1 for p in evaluable if p.field_correct)
    return correct / len(evaluable)


def _metric_field_recall(pairs):
    gold_present = [p for p in pairs if p.field_present]
    if not gold_present:
        return 0.0
    correct = sum(1 for p in gold_present if p.field_correct)
    return correct / len(gold_present)


def _metric_evidence_precision(pairs):
    with_ev = [p for p in pairs if p.has_evidence]
    if not with_ev:
        return 0.0
    verified = sum(1 for p in with_ev if p.evidence_verified)
    return verified / len(with_ev)


def _metric_unjustified_rate(pairs):
    with_value = [p for p in pairs if p.has_value]
    if not with_value:
        return 0.0
    unjustified = sum(1 for p in with_value if p.unjustified)
    return unjustified / len(with_value)


def _metric_iou_avg(pairs):
    ious = [p.iou for p in pairs if p.iou is not None]
    if not ious:
        return 0.0
    return float(np.mean(ious))


METRIC_FUNCS = {
    "field_precision": _metric_field_precision,
    "field_recall": _metric_field_recall,
    "evidence_precision": _metric_evidence_precision,
    "unjustified_rate": _metric_unjustified_rate,
    "iou_avg": _metric_iou_avg,
}


class BootstrapSampler:
    """项目级 Bootstrap 采样器.

    以采购项目为最小重采样单元:
    - 同一项目的所有公告、版本和字段整体参与采样
    - 不按单个字段独立采样
    - 重复采样的项目保留独立采样 ID 和权重
    - 使用 numpy.random.Generator 保证可复现
    """

    def __init__(self, pairs, n_resamples=1000, seed=42):
        if not pairs:
            raise ValueError("pairs 不能为空")
        if n_resamples < 1:
            raise ValueError("n_resamples 必须 >= 1")
        self.pairs = list(pairs)
        self.n_resamples = int(n_resamples)
        self.seed = int(seed)

        self._by_project = {}
        for p in self.pairs:
            self._by_project.setdefault(p.project_id, []).append(p)
        self._project_ids = list(self._by_project.keys())
        self._n_projects = len(self._project_ids)

        if self._n_projects < 2:
            raise ValueError(
                f"Bootstrap 需要至少 2 个项目, 当前 {self._n_projects} 个"
            )

        self._rng = np.random.default_rng(self.seed)

    def _resample(self):
        indices = self._rng.integers(
            0, self._n_projects, size=self._n_projects
        )
        resampled = []
        samples = []
        for idx in indices:
            pid = self._project_ids[idx]
            sample_instance_id = str(ulid.new())
            project_pairs = self._by_project[pid]
            samples.append(
                BootstrapSample(
                    sample_instance_id=sample_instance_id,
                    project_id=pid,
                    weight=1,
                )
            )
            resampled.extend(project_pairs)
        return resampled, samples

    def compute_ci(self, metric_name):
        func = METRIC_FUNCS.get(metric_name)
        if func is None:
            raise ValueError(
                f"未知指标: {metric_name}, 支持: {list(METRIC_FUNCS.keys())}"
            )

        point_estimate = func(self.pairs)

        estimates = []
        all_samples = []
        for _ in range(self.n_resamples):
            resampled, samples = self._resample()
            estimates.append(func(resampled))
            all_samples.extend(samples)

        estimates.sort()
        n = len(estimates)
        lower_idx = int(n * 0.025)
        upper_idx = int(n * 0.975) - 1
        ci_lower = estimates[max(lower_idx, 0)]
        ci_upper = estimates[min(upper_idx, n - 1)]

        return BootstrapResult(
            metric_name=metric_name,
            point_estimate=round(point_estimate, 4),
            ci_lower=round(ci_lower, 4),
            ci_upper=round(ci_upper, 4),
            n_resamples=n,
            n_projects=self._n_projects,
            n_pairs=len(self.pairs),
            seed=self.seed,
            rule_version=RULE_VERSION,
            samples=all_samples,
            sample_size_per_draw=self._n_projects,
        )


def bootstrap_ci(pairs, metric_name, n_resamples=1000, seed=42):
    """便捷封装：直接计算单指标 CI."""
    sampler = BootstrapSampler(pairs, n_resamples=n_resamples, seed=seed)
    return sampler.compute_ci(metric_name)
