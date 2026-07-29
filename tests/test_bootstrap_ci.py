"""Bootstrap 置信区间单元测试。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.eval.bootstrap_ci import bootstrap_ci, run_from_report, _group_docs, _aggregate_metric


def _make_doc(doc_id: str, notice_type: str, found: int, present: int, matched: int, pred: int) -> dict:
    """构造测试用 doc_metric。"""
    return {
        "doc_id": doc_id,
        "notice_type": notice_type,
        "fields_found": found,
        "fields_present": present,
        "evidences_matched": matched,
        "evidences_pred": pred,
        "iou_list_matched": [1.0] * matched,
        "recall": round(found / max(present, 1), 4),
        "precision": round(matched / max(pred, 1), 4),
        "iou_avg": round(matched / max(pred, 1), 4),
    }


# ============ _group_docs 测试 ============

def test_group_docs_basic():
    """基础分组测试。"""
    docs = [
        _make_doc("d1", "tender", 4, 5, 3, 4),
        _make_doc("d2", "tender", 3, 5, 2, 3),
        _make_doc("d3", "award", 2, 3, 1, 2),
    ]
    groups = _group_docs(docs, "notice_type")
    assert set(groups.keys()) == {"tender", "award"}
    assert len(groups["tender"]) == 2
    assert len(groups["award"]) == 1


def test_group_docs_empty():
    """空数据测试。"""
    groups = _group_docs([], "notice_type")
    assert groups == {}


def test_group_docs_missing_key():
    """缺 group_key 字段归到 unknown。"""
    docs = [{"doc_id": "d1", "fields_found": 1}]
    groups = _group_docs(docs, "notice_type")
    assert "unknown" in groups
    assert len(groups["unknown"]) == 1


# ============ _aggregate_metric 测试 ============

def test_aggregate_recall():
    """recall 聚合：先求和再相除。"""
    docs = [
        _make_doc("d1", "tender", 4, 5, 3, 4),
        _make_doc("d2", "tender", 3, 5, 2, 3),
    ]
    # recall = (4+3) / (5+5) = 0.7
    assert _aggregate_metric(docs, "recall") == 0.7


def test_aggregate_precision():
    """precision 聚合。"""
    docs = [
        _make_doc("d1", "tender", 4, 5, 3, 4),
        _make_doc("d2", "tender", 3, 5, 2, 3),
    ]
    # precision = (3+2) / (4+3) = 0.7143
    assert _aggregate_metric(docs, "precision") == 0.7143


def test_aggregate_zero_denominator():
    """分母为 0 返回 0.0。"""
    docs = [_make_doc("d1", "tender", 0, 0, 0, 0)]
    assert _aggregate_metric(docs, "recall") == 0.0
    assert _aggregate_metric(docs, "precision") == 0.0


# ============ bootstrap_ci 测试 ============

def test_bootstrap_ci_basic():
    """基础 Bootstrap CI 测试。"""
    docs = [
        _make_doc("d1", "tender", 4, 5, 3, 4),
        _make_doc("d2", "tender", 3, 5, 2, 3),
        _make_doc("d3", "award", 2, 3, 1, 2),
        _make_doc("d4", "award", 3, 3, 2, 3),
        _make_doc("d5", "correction", 1, 2, 1, 2),
        _make_doc("d6", "correction", 2, 2, 1, 1),
    ]
    result = bootstrap_ci(
        docs,
        metric_keys=["recall", "precision"],
        n_bootstrap=100,
        random_seed=42,
    )
    assert "meta" in result
    assert "metrics" in result
    assert result["meta"]["n_bootstrap"] == 100
    assert result["meta"]["random_seed"] == 42
    assert result["meta"]["n_groups"] == 3
    assert result["meta"]["n_docs"] == 6
    assert "recall" in result["metrics"]
    assert "precision" in result["metrics"]


def test_bootstrap_ci_ci_bounds():
    """CI 上下界合理性：ci_lower <= point_estimate <= ci_upper。"""
    docs = [
        _make_doc(f"d{i}", "tender", 4 + i % 2, 5, 3, 4)
        for i in range(10)
    ] + [
        _make_doc(f"a{i}", "award", 2 + i % 2, 3, 1, 2)
        for i in range(10)
    ]
    result = bootstrap_ci(
        docs,
        metric_keys=["recall"],
        n_bootstrap=500,
        random_seed=42,
    )
    pe = result["metrics"]["recall"]["point_estimate"]
    lo = result["metrics"]["recall"]["ci_lower"]
    hi = result["metrics"]["recall"]["ci_upper"]
    assert lo <= pe <= hi, f"CI 不合理: lo={lo} pe={pe} hi={hi}"
    assert 0.0 <= lo <= 1.0
    assert 0.0 <= hi <= 1.0


def test_bootstrap_ci_reproducible():
    """相同 random_seed 两次运行结果一致。"""
    docs = [
        _make_doc("d1", "tender", 4, 5, 3, 4),
        _make_doc("d2", "award", 2, 3, 1, 2),
        _make_doc("d3", "correction", 1, 2, 1, 2),
    ]
    r1 = bootstrap_ci(docs, ["recall"], n_bootstrap=100, random_seed=42)
    r2 = bootstrap_ci(docs, ["recall"], n_bootstrap=100, random_seed=42)
    assert r1["metrics"]["recall"]["bootstrap_samples"] == r2["metrics"]["recall"]["bootstrap_samples"]
    assert r1["metrics"]["recall"]["ci_lower"] == r2["metrics"]["recall"]["ci_lower"]
    assert r1["metrics"]["recall"]["ci_upper"] == r2["metrics"]["recall"]["ci_upper"]


def test_bootstrap_ci_single_group():
    """单组数据：Bootstrap 退化为点估计附近抖动。"""
    docs = [_make_doc("d1", "tender", 4, 5, 3, 4)]
    result = bootstrap_ci(docs, ["recall"], n_bootstrap=100, random_seed=42)
    pe = result["metrics"]["recall"]["point_estimate"]
    # 单组采样永远是自己，所以所有样本都是点估计
    for s in result["metrics"]["recall"]["bootstrap_samples"]:
        assert s == pe


def test_bootstrap_ci_empty():
    """空数据返回错误标记。"""
    result = bootstrap_ci([], ["recall"], n_bootstrap=100)
    assert "error" in result["meta"]
    assert result["metrics"] == {}


def test_bootstrap_ci_samples_count():
    """bootstrap_samples 长度等于 n_bootstrap。"""
    docs = [
        _make_doc("d1", "tender", 4, 5, 3, 4),
        _make_doc("d2", "award", 2, 3, 1, 2),
    ]
    result = bootstrap_ci(docs, ["recall", "precision"], n_bootstrap=200, random_seed=42)
    assert len(result["metrics"]["recall"]["bootstrap_samples"]) == 200
    assert len(result["metrics"]["precision"]["bootstrap_samples"]) == 200


# ============ run_from_report 测试 ============

def test_run_from_report(tmp_path):
    """从报告文件读取并计算 CI。"""
    report = {
        "task": "W3-03",
        "doc_metrics": [
            _make_doc("d1", "tender", 4, 5, 3, 4),
            _make_doc("d2", "award", 2, 3, 1, 2),
            _make_doc("d3", "correction", 1, 2, 1, 2),
        ],
    }
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

    output_path = tmp_path / "ci.json"
    result = run_from_report(
        str(report_path),
        str(output_path),
        metric_keys=["recall"],
        n_bootstrap=100,
        random_seed=42,
    )
    assert "metrics" in result
    assert "recall" in result["metrics"]
    assert output_path.exists()
    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert saved == result


def test_run_from_report_empty(tmp_path):
    """报告 doc_metrics 为空时报错。"""
    report_path = tmp_path / "empty.json"
    report_path.write_text(json.dumps({"doc_metrics": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="doc_metrics 为空"):
        run_from_report(str(report_path), metric_keys=["recall"])
