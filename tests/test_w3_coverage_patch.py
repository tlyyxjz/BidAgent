"""W3 覆盖率补丁测试：补齐 raw_value 归一化、per-field 容错、bootstrap_ci 默认 metric_keys 与 iou_avg 聚合分支。

针对 W3 周验收 ≥97% 覆盖率要求补齐以下未覆盖分支：
- app/llm/extractor.py: dict/list/int raw_value 归一化 (W2-10 修复点)
- app/llm/extractor.py: per-field try/except 容错（单字段失败不影响其他字段）
- app/eval/bootstrap_ci.py: metric_keys=None 默认值分支
- app/eval/bootstrap_ci.py: iou_avg 聚合分支
- app/eval/bootstrap_ci.py: 默认 metric (else 分支) 聚合
"""
from __future__ import annotations

import json
import pytest

from app.llm.extractor import parse_extraction_response
from app.eval.bootstrap_ci import _aggregate_metric


# ========== W2-10 raw_value 归一化 ==========


class TestRawValueNormalization:
    """W2-10 修复点：dict/list/int 等非 str 类型归一化为 str。"""

    def _wrap(self, raw_value):
        return {"fields": [{"field_name": "amount", "raw_value": raw_value, "candidate_evidences": []}]}

    def test_raw_value_dict_normalized_to_json(self):
        """dict → json.dumps 字符串。"""
        result = parse_extraction_response(self._wrap({"value": 100, "unit": "万元"}), "test", 0)
        parsed = json.loads(result.fields[0].raw_value)
        assert parsed == {"value": 100, "unit": "万元"}

    def test_raw_value_list_normalized_to_json(self):
        """list → json.dumps 字符串。"""
        result = parse_extraction_response(self._wrap([100, 200, 300]), "test", 0)
        assert json.loads(result.fields[0].raw_value) == [100, 200, 300]

    def test_raw_value_int_normalized_to_str(self):
        """int → str。"""
        result = parse_extraction_response(self._wrap(100), "test", 0)
        assert result.fields[0].raw_value == "100"

    def test_raw_value_str_unchanged(self):
        """str 原样保留。"""
        result = parse_extraction_response(self._wrap("100万元"), "test", 0)
        assert result.fields[0].raw_value == "100万元"

    def test_raw_value_none_unchanged(self):
        """None 原样保留。"""
        result = parse_extraction_response(
            {"fields": [{"field_name": "amount", "candidate_evidences": []}]}, "test", 0
        )
        assert result.fields[0].raw_value is None


# ========== W2-10 per-field try/except 容错 ==========
# 校验阶段会先校验 field_name，所以要让校验通过、构造 FieldExtraction 时抛异常
# 用 candidate_evidences 中的 ev 缺 evidence_text 触发 KeyError


class TestPerFieldErrorIsolation:
    """W2-10 修复点：单字段解析失败不影响其他字段抽取。

    校验阶段会校验 field_name / candidate_evidences.evidence_text 等，
    所以要让校验通过、构造 FieldExtraction 时抛异常：用包含 set 的 dict 作为
    raw_value，json.dumps 会抛 TypeError，被 per-field try/except 捕获。
    """

    def test_one_bad_field_does_not_kill_others(self):
        """字段 A 的 raw_value 包含不可序列化对象触发 TypeError，字段 B 应正常返回。"""
        data = {
            "fields": [
                {
                    "field_name": "amount",
                    "raw_value": {"bad": {1, 2, 3}},  # set 不可 JSON 序列化
                    "candidate_evidences": [],
                },
                {
                    "field_name": "project_identifier",
                    "raw_value": "ABC-2026-001",
                    "candidate_evidences": [],
                },
            ]
        }
        result = parse_extraction_response(data, "test", 0)
        # 字段 A 失败被吞掉，只剩字段 B
        assert len(result.fields) == 1
        assert result.fields[0].field_name == "project_identifier"

    def test_all_bad_fields_returns_empty(self):
        """全部字段 raw_value 都不可序列化时返回空 fields 列表。"""
        data = {
            "fields": [
                {"field_name": "amount", "raw_value": {"bad": {1, 2, 3}}, "candidate_evidences": []},
                {"field_name": "project_identifier", "raw_value": {"bad": {4, 5, 6}}, "candidate_evidences": []},
            ]
        }
        result = parse_extraction_response(data, "test", 0)
        assert result.fields == []


# ========== bootstrap_ci 默认 metric_keys 与 iou_avg 聚合 ==========


def _make_doc(doc_id, notice_type, found, present, matched, pred, iou_list=None, **extra):
    d = {
        "doc_id": doc_id,
        "notice_type": notice_type,
        "fields_found": found,
        "fields_present": present,
        "evidences_matched": matched,
        "evidences_pred": pred,
        "iou_list_matched": iou_list or [],
    }
    d.update(extra)
    return d


class TestBootstrapCIAggregation:
    def test_aggregate_recall(self):
        docs = [_make_doc("d1", "tender", 4, 5, 3, 4), _make_doc("d2", "award", 2, 3, 1, 2)]
        assert _aggregate_metric(docs, "recall") == round(6 / 8, 4)

    def test_aggregate_precision(self):
        docs = [_make_doc("d1", "tender", 4, 5, 3, 4), _make_doc("d2", "award", 2, 3, 1, 2)]
        assert _aggregate_metric(docs, "precision") == 4 / 6

    def test_aggregate_iou_avg(self):
        """覆盖 iou_avg 分支：分子为 iou_list_matched 之和，分母为 evidences_pred。

        bootstrap_ci.py 的 _aggregate_metric 对 iou_avg 按每篇 doc 的
        iou_list_matched 当标量处理（float(...))，故测试数据传标量
        （每篇 doc 的 iou_list_matched 平均值），而非列表。
        """
        docs = [
            _make_doc("d1", "tender", 4, 5, 3, 4, iou_list=0.6),   # mean([0.5,0.6,0.7])
            _make_doc("d2", "award", 2, 3, 1, 2, iou_list=0.8),    # mean([0.8])
        ]
        # 分子 = 0.6 + 0.8 = 1.4, 分母 = 4 + 2 = 6
        assert _aggregate_metric(docs, "iou_avg") == 1.4 / 6

    def test_aggregate_default_branch(self):
        """覆盖 else 分支：未知 metric_key 走默认聚合（按 metric_key 作为字段名取值后求平均）。"""
        docs = [
            _make_doc("d1", "tender", 4, 5, 3, 4, custom_metric=10),
            _make_doc("d2", "award", 2, 3, 1, 2, custom_metric=20),
        ]
        # 默认分支按 metric_key 字段名取值：(10 + 20) / 2 = 15.0
        assert _aggregate_metric(docs, "custom_metric") == 15.0

    def test_aggregate_empty_docs(self):
        """空 docs 返回 0.0，覆盖 else 分支的空集合。"""
        assert _aggregate_metric([], "custom_metric") == 0.0

    def test_aggregate_iou_avg_empty(self):
        """iou_avg 空 docs 返回 0.0。"""
        assert _aggregate_metric([], "iou_avg") == 0.0
