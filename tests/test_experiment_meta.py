"""v4.1 §10.12 实验复现信息收集器测试。

覆盖：
- ExperimentMeta 16 项字段完整性
- compute_response_hash 稳定性
- compute_dataset_hash 文件哈希
- get_git_commit 返回值
- collect_experiment_meta 集成
- to_dict / from_dict 序列化
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.experiment_meta import (
    DISPLAY_RULE_VERSION,
    EVIDENCE_RULE_VERSION,
    NORMALIZER_VERSION,
    ExperimentMeta,
    collect_experiment_meta,
    compute_dataset_hash,
    compute_response_hash,
    get_git_commit,
    now_iso8601,
)


# ========== ExperimentMeta 字段完整性测试 ==========


class TestExperimentMetaFields:
    """ExperimentMeta 16 项字段完整性。"""

    def test_has_sixteen_fields(self):
        """ExperimentMeta 必须有 16 个字段。"""
        fields = ExperimentMeta.__dataclass_fields__
        assert len(fields) == 16

    def test_model_info_fields(self):
        """模型信息 4 项字段存在。"""
        meta = ExperimentMeta()
        assert meta.model_role == "primary"
        assert meta.provider == "deepseek"
        assert meta.model_id == "unknown"
        assert meta.model_snapshot is None

    def test_request_params_fields(self):
        """请求参数 5 项字段存在。"""
        meta = ExperimentMeta()
        assert meta.request_time == ""
        assert meta.temperature == 0.0
        assert meta.top_p == 1.0
        assert meta.seed is None
        assert meta.request_id is None

    def test_hash_fields(self):
        """哈希信息 2 项字段存在。"""
        meta = ExperimentMeta()
        assert meta.prompt_hash == ""
        assert meta.response_hash is None

    def test_rule_version_fields(self):
        """规则版本 3 项字段存在且非 unknown。"""
        meta = ExperimentMeta()
        # 版本号应该从对应模块导入，不是 "unknown"
        assert meta.normalizer_version == NORMALIZER_VERSION
        assert meta.evidence_rule_version == EVIDENCE_RULE_VERSION
        assert meta.display_rule_version == DISPLAY_RULE_VERSION

    def test_data_code_version_fields(self):
        """数据与代码版本 2 项字段存在。"""
        meta = ExperimentMeta()
        assert meta.dataset_version is None
        assert meta.code_commit is None


# ========== compute_response_hash 测试 ==========


class TestComputeResponseHash:
    """响应内容哈希计算。"""

    def test_stable_hash(self):
        """相同内容哈希一致。"""
        content = '{"fields": [{"field_name": "amount", "raw_value": "100万元"}]}'
        h1 = compute_response_hash(content)
        h2 = compute_response_hash(content)
        assert h1 == h2

    def test_hash_length(self):
        """哈希长度 16 字符。"""
        h = compute_response_hash("test content")
        assert len(h) == 16

    def test_different_content_different_hash(self):
        """不同内容哈希不同。"""
        h1 = compute_response_hash("content1")
        h2 = compute_response_hash("content2")
        assert h1 != h2

    def test_matches_sha256_prefix(self):
        """哈希是 SHA256 前 16 字符。"""
        content = "test"
        expected = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        assert compute_response_hash(content) == expected


# ========== compute_dataset_hash 测试 ==========


class TestComputeDatasetHash:
    """数据集文件哈希计算。"""

    def test_nonexistent_file_returns_unknown(self, tmp_path):
        """不存在的文件返回 'unknown'。"""
        result = compute_dataset_hash(tmp_path / "nonexistent.json")
        assert result == "unknown"

    def test_existing_file_returns_hash(self, tmp_path):
        """存在的文件返回 16 字符哈希。"""
        f = tmp_path / "dataset.json"
        f.write_text('{"test": "data"}', encoding="utf-8")
        result = compute_dataset_hash(f)
        assert len(result) == 16
        assert result != "unknown"

    def test_same_content_same_hash(self, tmp_path):
        """相同内容文件哈希一致。"""
        f1 = tmp_path / "d1.json"
        f2 = tmp_path / "d2.json"
        f1.write_text("same content", encoding="utf-8")
        f2.write_text("same content", encoding="utf-8")
        assert compute_dataset_hash(f1) == compute_dataset_hash(f2)


# ========== get_git_commit 测试 ==========


class TestGetGitCommit:
    """git commit hash 获取。"""

    def test_returns_string(self):
        """返回字符串。"""
        result = get_git_commit()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_returns_unknown_on_failure(self):
        """git 命令失败时返回 'unknown'。"""
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert get_git_commit() == "unknown"


# ========== collect_experiment_meta 集成测试 ==========


class TestCollectExperimentMeta:
    """collect_experiment_meta 集成测试。"""

    def test_collect_all_sixteen_fields(self):
        """收集 16 项字段全部填充。"""
        meta = collect_experiment_meta(
            model_id="deepseek-chat",
            prompt_hash="abc123def456",
            response_content='{"fields": []}',
            temperature=0.7,
            top_p=0.9,
            seed=42,
            request_id="req-001",
            model_role="primary",
            provider="deepseek",
            model_snapshot="2026-08-01",
        )
        # 16 项全部非默认空值
        assert meta.model_role == "primary"
        assert meta.provider == "deepseek"
        assert meta.model_id == "deepseek-chat"
        assert meta.model_snapshot == "2026-08-01"
        assert meta.request_time  # 非空
        assert meta.temperature == 0.7
        assert meta.top_p == 0.9
        assert meta.seed == 42
        assert meta.request_id == "req-001"
        assert meta.prompt_hash == "abc123def456"
        assert meta.response_hash  # 非空
        assert meta.normalizer_version == NORMALIZER_VERSION
        assert meta.evidence_rule_version == EVIDENCE_RULE_VERSION
        assert meta.display_rule_version == DISPLAY_RULE_VERSION
        assert meta.code_commit  # 非空（可能是 unknown 或实际 hash）

    def test_collect_without_optional_params(self):
        """不传可选参数时使用默认值。"""
        meta = collect_experiment_meta(model_id="test-model")
        assert meta.model_id == "test-model"
        assert meta.prompt_hash == ""
        assert meta.response_hash is None
        assert meta.dataset_version is None
        assert meta.temperature == 0.0

    def test_collect_with_dataset_path(self, tmp_path):
        """传入数据集路径时计算 dataset_version。"""
        f = tmp_path / "gold.json"
        f.write_text('{"gold": true}', encoding="utf-8")
        meta = collect_experiment_meta(dataset_path=f)
        assert meta.dataset_version is not None
        assert len(meta.dataset_version) == 16


# ========== 序列化测试 ==========


class TestSerialization:
    """to_dict / from_dict 序列化。"""

    def test_to_dict_contains_all_fields(self):
        """to_dict 包含 16 个字段。"""
        meta = ExperimentMeta(model_id="test")
        d = meta.to_dict()
        assert len(d) == 16
        assert "model_id" in d
        assert "normalizer_version" in d

    def test_from_dict_roundtrip(self):
        """from_dict 反序列化正确。"""
        meta1 = ExperimentMeta(
            model_id="test-model",
            prompt_hash="abc123",
            temperature=0.5,
            seed=42,
        )
        d = meta1.to_dict()
        meta2 = ExperimentMeta.from_dict(d)
        assert meta2.model_id == "test-model"
        assert meta2.prompt_hash == "abc123"
        assert meta2.temperature == 0.5
        assert meta2.seed == 42

    def test_from_dict_ignores_unknown_fields(self):
        """from_dict 忽略未知字段。"""
        d = {"model_id": "test", "unknown_field": "value"}
        meta = ExperimentMeta.from_dict(d)
        assert meta.model_id == "test"

    def test_json_serializable(self):
        """to_dict 结果可 JSON 序列化。"""
        meta = collect_experiment_meta(model_id="test")
        d = meta.to_dict()
        # 不应抛出 JSON 序列化异常
        json_str = json.dumps(d, ensure_ascii=False)
        assert "model_id" in json.loads(json_str)
