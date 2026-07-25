"""W2-01 LLM 字段抽取单元测试。

覆盖：
- prompt 构建：build_extraction_prompt / compute_prompt_hash
- 响应解析：parse_extraction_response
- Schema 校验：_validate_extraction
- 失败处理：LLM 不可用时返回带 error 的结果
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.llm.extractor import (
    EXTRACTION_SYSTEM_PROMPT,
    build_extraction_prompt,
    call_extraction_llm,
    compute_prompt_hash,
    parse_extraction_response,
)
from app.llm.extraction_schemas import (
    CORE_FIELD_NAMES,
    CandidateEvidence,
    ExtractionResult,
    FieldExtraction,
)


# ========== prompt 测试 ==========


class TestPrompt:
    def test_system_prompt_contains_six_fields(self):
        """Sol 要求：不修改六类字段定义。"""
        for field_name in CORE_FIELD_NAMES:
            assert field_name in EXTRACTION_SYSTEM_PROMPT

    def test_system_prompt_contains_evidence_role(self):
        """Sol 要求：证据角色标注 primary/context/qualifier。"""
        assert "primary" in EXTRACTION_SYSTEM_PROMPT
        assert "context" in EXTRACTION_SYSTEM_PROMPT
        assert "qualifier" in EXTRACTION_SYSTEM_PROMPT

    def test_system_prompt_contains_no_rewrite_constraint(self):
        """Sol 要求：候选证据文本必须是原文中的连续片段，不得改写。"""
        assert "不得改写" in EXTRACTION_SYSTEM_PROMPT
        assert "连续片段" in EXTRACTION_SYSTEM_PROMPT

    def test_build_extraction_prompt_includes_raw_text(self):
        raw = "招标公告原文"
        prompt = build_extraction_prompt(raw)
        assert raw in prompt
        assert "JSON" in prompt

    def test_compute_prompt_hash_stable(self):
        """prompt 哈希应稳定（同 prompt 同哈希）。"""
        hash1 = compute_prompt_hash()
        hash2 = compute_prompt_hash()
        assert hash1 == hash2
        assert len(hash1) == 16  # 前 16 字符


# ========== 响应解析测试 ==========


class TestParseResponse:
    def test_parse_valid_response(self):
        """解析合法响应。"""
        data = {
            "fields": [
                {
                    "field_name": "project_identifier",
                    "field_status": "present",
                    "raw_value": "ZFCG-2026-001",
                    "candidate_evidences": [
                        {"evidence_text": "项目编号：ZFCG-2026-001", "role": "primary"}
                    ],
                },
                {
                    "field_name": "winner_name",
                    "field_status": "absent",
                    "candidate_evidences": [],
                },
            ]
        }
        result = parse_extraction_response(data, "test-model", 100, 500)

        assert isinstance(result, ExtractionResult)
        assert len(result.fields) == 2
        assert result.fields[0].field_name == "project_identifier"
        assert result.fields[0].raw_value == "ZFCG-2026-001"
        assert len(result.fields[0].candidate_evidences) == 1
        assert result.fields[0].candidate_evidences[0].role == "primary"
        assert result.model_id == "test-model"
        assert result.latency_ms == 100
        assert result.total_tokens == 500
        assert result.prompt_hash != ""

    def test_parse_with_amount_type(self):
        """amount 字段的 amount_type 解析。"""
        data = {
            "fields": [
                {
                    "field_name": "amount",
                    "field_status": "present",
                    "raw_value": "100万元",
                    "amount_type": "budget",
                    "currency": "CNY",
                    "candidate_evidences": [
                        {"evidence_text": "预算金额：100万元", "role": "primary"}
                    ],
                }
            ]
        }
        result = parse_extraction_response(data, "test", 0)
        assert result.fields[0].amount_type == "budget"
        assert result.fields[0].currency == "CNY"

    def test_parse_multi_value_field(self):
        """多值字段（multi_value）。"""
        data = {
            "fields": [
                {
                    "field_name": "amount",
                    "field_status": "multi_value",
                    "raw_value": "100万元",
                    "amount_type": "award",
                    "lot_id": "lot1",
                    "candidate_evidences": [],
                }
            ]
        }
        result = parse_extraction_response(data, "test", 0)
        assert result.fields[0].field_status == "multi_value"
        assert result.fields[0].lot_id == "lot1"

    def test_parse_missing_fields_rejected(self):
        """缺少 fields 字段被拒。"""
        with pytest.raises(ValueError, match="缺少 fields"):
            parse_extraction_response({}, "test", 0)

    def test_parse_fields_not_list_rejected(self):
        """fields 不是列表被拒。"""
        with pytest.raises(ValueError, match="必须是列表"):
            parse_extraction_response({"fields": "not_a_list"}, "test", 0)

    def test_parse_field_missing_field_name_rejected(self):
        """字段缺少 field_name 被拒。"""
        with pytest.raises(ValueError, match="缺少 field_name"):
            parse_extraction_response({"fields": [{"field_status": "present"}]}, "test", 0)

    def test_parse_empty_fields_rejected(self):
        """fields 为空被拒。"""
        with pytest.raises(ValueError, match="不能为空"):
            parse_extraction_response({"fields": []}, "test", 0)

    def test_parse_invalid_field_name_rejected(self):
        """非法 field_name 被拒。"""
        data = {
            "fields": [
                {"field_name": "invalid_field", "field_status": "present"}
            ]
        }
        with pytest.raises(ValueError, match="非法 field_name"):
            parse_extraction_response(data, "test", 0)

    def test_parse_invalid_field_status_rejected(self):
        """非法 field_status 被拒。"""
        data = {
            "fields": [
                {"field_name": "project_identifier", "field_status": "invalid_status"}
            ]
        }
        with pytest.raises(ValueError, match="非法 field_status"):
            parse_extraction_response(data, "test", 0)

    def test_parse_invalid_amount_type_rejected(self):
        """非法 amount_type 被拒。"""
        data = {
            "fields": [
                {
                    "field_name": "amount",
                    "field_status": "present",
                    "amount_type": "invalid_type",
                }
            ]
        }
        with pytest.raises(ValueError, match="非法 amount_type"):
            parse_extraction_response(data, "test", 0)

    def test_parse_invalid_evidence_role_rejected(self):
        """非法证据角色被拒。"""
        data = {
            "fields": [
                {
                    "field_name": "project_identifier",
                    "field_status": "present",
                    "candidate_evidences": [
                        {"evidence_text": "证据", "role": "invalid_role"}
                    ],
                }
            ]
        }
        with pytest.raises(ValueError, match="非法 role"):
            parse_extraction_response(data, "test", 0)

    def test_parse_missing_evidence_text_rejected(self):
        """证据缺少 evidence_text 被拒。"""
        data = {
            "fields": [
                {
                    "field_name": "project_identifier",
                    "field_status": "present",
                    "candidate_evidences": [{"role": "primary"}],  # 缺 evidence_text
                }
            ]
        }
        with pytest.raises(ValueError, match="缺少 evidence_text"):
            parse_extraction_response(data, "test", 0)

    def test_parse_default_role(self):
        """role 缺省时默认 primary。"""
        data = {
            "fields": [
                {
                    "field_name": "project_identifier",
                    "field_status": "present",
                    "candidate_evidences": [{"evidence_text": "证据"}],  # 缺 role
                }
            ]
        }
        result = parse_extraction_response(data, "test", 0)
        assert result.fields[0].candidate_evidences[0].role == "primary"

    def test_parse_default_field_status(self):
        """field_status 缺省时默认 present。"""
        data = {
            "fields": [
                {"field_name": "project_identifier"}  # 缺 field_status
            ]
        }
        result = parse_extraction_response(data, "test", 0)
        assert result.fields[0].field_status == "present"


# ========== LLM 调用测试（mock） ==========


class TestCallExtractionLLM:
    @pytest.mark.asyncio
    async def test_call_no_api_key_raises(self):
        """无 API key 抛异常。"""
        with patch("app.llm.extractor.settings") as mock_settings:
            mock_settings.DEEPSEEK_API_KEY = ""
            with pytest.raises(RuntimeError, match="not configured"):
                await call_extraction_llm("测试文本")

    @pytest.mark.asyncio
    async def test_call_api_failure_returns_error(self):
        """Sol 要求：失败时记录错误，不静默丢弃。"""
        with patch("app.llm.extractor.settings") as mock_settings:
            mock_settings.DEEPSEEK_API_KEY = "test-key"
            mock_settings.LLM_MODEL = "test-model"
            mock_settings.LLM_TIMEOUT_SECONDS = 10
            mock_settings.DEEPSEEK_BASE_URL = "https://test"

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.__aenter__.return_value = mock_client
                mock_client.post.side_effect = Exception("API 错误")
                mock_client_cls.return_value = mock_client

                result = await call_extraction_llm("测试文本")

        assert isinstance(result, ExtractionResult)
        assert result.error is not None
        assert "API 错误" in result.error
        assert result.fields == []
        assert result.model_id == "test-model"
        assert result.prompt_hash != ""

    @pytest.mark.asyncio
    async def test_call_success(self):
        """成功调用返回解析后的结果。"""
        mock_response_data = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "fields": [
                                    {
                                        "field_name": "project_identifier",
                                        "field_status": "present",
                                        "raw_value": "ZFCG-2026-001",
                                        "candidate_evidences": [
                                            {
                                                "evidence_text": "项目编号：ZFCG-2026-001",
                                                "role": "primary",
                                            }
                                        ],
                                    }
                                ]
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ],
            "usage": {"total_tokens": 500},
        }

        with patch("app.llm.extractor.settings") as mock_settings:
            mock_settings.DEEPSEEK_API_KEY = "test-key"
            mock_settings.LLM_MODEL = "test-model"
            mock_settings.LLM_TIMEOUT_SECONDS = 10
            mock_settings.DEEPSEEK_BASE_URL = "https://test"

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.__aenter__.return_value = mock_client
                mock_resp = MagicMock()
                mock_resp.raise_for_status = MagicMock()
                mock_resp.json.return_value = mock_response_data
                mock_client.post.return_value = mock_resp
                mock_client_cls.return_value = mock_client

                result = await call_extraction_llm("招标公告")

        assert result.error is None
        assert len(result.fields) == 1
        assert result.fields[0].raw_value == "ZFCG-2026-001"
        assert result.model_id == "test-model"
        assert result.total_tokens == 500
        # mock 环境下 perf_counter 可能极快返回 0，只校验非负
        assert result.latency_ms >= 0
