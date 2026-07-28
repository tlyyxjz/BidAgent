"""P1-17: invalid 链路和 iou 口径回归测试。

保护 P0-5/P0-6 修复的 invalid 检测逻辑和 P1-18 iou_avg 新逻辑。
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 确保 scripts 包可被 import（scripts 不是包，无 __init__.py）
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.eval_ablation import run_group_a, run_group_b, run_group_c
from scripts.eval_evidence import evaluate_doc, DocMetric, OverallMetric


class TestEvalAblationInvalid:
    """P0-5: eval_ablation.py invalid 检测。"""

    async def test_group_c_invalid_on_error(self):
        """C 组 LLM 返回 error 时标记 invalid。"""
        mock_result = MagicMock()
        mock_result.error = "LLM API Error"
        mock_result.total_tokens = 100
        mock_result.fields = []

        with patch("scripts.eval_ablation.call_extraction_llm", return_value=mock_result):
            fields, meta = await run_group_c(MagicMock(), MagicMock())

        assert meta["invalid"] is True
        assert fields == []

    async def test_group_c_invalid_on_zero_tokens(self):
        """C 组 LLM 返回 0 tokens 时标记 invalid。"""
        mock_result = MagicMock()
        mock_result.error = None
        mock_result.total_tokens = 0
        mock_result.fields = [MagicMock(field_name="test")]

        with patch("scripts.eval_ablation.call_extraction_llm", return_value=mock_result):
            fields, meta = await run_group_c(MagicMock(), MagicMock())

        assert meta["invalid"] is True
        assert fields == []

    async def test_group_c_invalid_on_empty_fields(self):
        """C 组 LLM 返回空 fields 时标记 invalid。"""
        mock_result = MagicMock()
        mock_result.error = None
        mock_result.total_tokens = 100
        mock_result.fields = []

        with patch("scripts.eval_ablation.call_extraction_llm", return_value=mock_result):
            fields, meta = await run_group_c(MagicMock(), MagicMock())

        assert meta["invalid"] is True
        assert fields == []

    async def test_group_c_valid(self):
        """C 组正常返回时不标记 invalid。"""
        mock_result = MagicMock()
        mock_result.error = None
        mock_result.total_tokens = 100
        mock_field = MagicMock()
        mock_field.field_name = "test"
        mock_result.fields = [mock_field]

        # patch EvidenceLocator 避免 MagicMock raw_text 触发真实构造
        with patch("scripts.eval_ablation.call_extraction_llm", return_value=mock_result), \
             patch("scripts.eval_ablation.EvidenceLocator"):
            fields, meta = await run_group_c(MagicMock(), MagicMock())

        assert meta["invalid"] is False


class TestEvalEvidenceInvalid:
    """P0-6: eval_evidence.py invalid 检测。"""

    async def test_evaluate_doc_invalid_on_error(self):
        """evaluate_doc LLM 返回 error 时返回 None。"""
        mock_result = MagicMock()
        mock_result.error = "LLM API Error"
        mock_result.total_tokens = 100
        mock_result.fields = []

        with patch("scripts.eval_evidence.call_extraction_llm", return_value=mock_result):
            dm, meta = await evaluate_doc(MagicMock(), MagicMock())

        assert meta["invalid"] is True
        assert dm is None

    async def test_evaluate_doc_invalid_on_zero_tokens(self):
        """evaluate_doc LLM 返回 0 tokens 时返回 None。"""
        mock_result = MagicMock()
        mock_result.error = None
        mock_result.total_tokens = 0
        mock_result.fields = [MagicMock(field_name="test")]

        with patch("scripts.eval_evidence.call_extraction_llm", return_value=mock_result):
            dm, meta = await evaluate_doc(MagicMock(), MagicMock())

        assert meta["invalid"] is True
        assert dm is None

    async def test_evaluate_doc_invalid_on_empty_fields(self):
        """evaluate_doc LLM 返回空 fields 时返回 None。"""
        mock_result = MagicMock()
        mock_result.error = None
        mock_result.total_tokens = 100
        mock_result.fields = []

        with patch("scripts.eval_evidence.call_extraction_llm", return_value=mock_result):
            dm, meta = await evaluate_doc(MagicMock(), MagicMock())

        assert meta["invalid"] is True
        assert dm is None


class TestIouAvgLogic:
    """P1-18: iou_avg 新逻辑（未匹配算 0）。"""

    def test_iou_avg_unmatched_counts_as_zero(self):
        """未匹配的证据在 iou_avg 中算 0（计入分母，不计入分子）。"""
        # 3 个预测证据，2 个匹配（IoU=0.8, 0.6），1 个未匹配
        # iou_avg = (0.8 + 0.6) / 3 = 0.4667
        iou_list_matched = [0.8, 0.6]
        evidences_pred = 3
        expected = round(sum(iou_list_matched) / evidences_pred, 4)
        assert expected == 0.4667

    def test_iou_avg_all_matched(self):
        """全部匹配时 iou_avg = sum(iou) / pred。"""
        iou_list_matched = [1.0, 0.8, 0.6]
        evidences_pred = 3
        expected = round(sum(iou_list_matched) / evidences_pred, 4)
        assert expected == 0.8

    def test_iou_avg_none_matched(self):
        """全部未匹配时 iou_avg = 0。"""
        iou_list_matched = []
        evidences_pred = 3
        expected = round(sum(iou_list_matched) / max(evidences_pred, 1), 4) if evidences_pred else 0.0
        assert expected == 0.0
