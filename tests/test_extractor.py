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
    EXTRACTION_FEWSHOT_EXAMPLES_NO_EVIDENCE,
    EXTRACTION_SYSTEM_PROMPT,
    EXTRACTION_SYSTEM_PROMPT_NO_EVIDENCE,
    _strip_markdown_fence,
    build_extraction_prompt,
    build_extraction_prompt_no_evidence,
    call_extraction_llm,
    call_extraction_llm_no_evidence,
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

    def test_parse_dict_raw_value_normalized(self):
        """K3-W3-03 方案A：raw_value 为 dict 时归一化为 JSON 字符串。

        联合体场景下 LLM v2 prompt R4 输出 {"main":..., "partners":[...]}，
        FieldExtraction.raw_value 是 Optional[str]，不收 dict。
        原实现整篇炸掉（6 字段全丢），修复后应序列化为 JSON 字符串。
        """
        data = {
            "fields": [
                {
                    "field_name": "winner_name",
                    "field_status": "present",
                    "raw_value": {"main": "公司A", "partners": ["公司B", "公司C"]},
                    "candidate_evidences": [],
                }
            ]
        }
        result = parse_extraction_response(data, "test", 0)
        assert len(result.fields) == 1
        rv = result.fields[0].raw_value
        assert isinstance(rv, str)
        parsed = json.loads(rv)
        assert parsed["main"] == "公司A"
        assert parsed["partners"] == ["公司B", "公司C"]

    def test_parse_list_raw_value_normalized(self):
        """K3-W3-03 方案A：raw_value 为 list 时归一化为 JSON 字符串。

        多中标人场景下 LLM 可能输出 ["公司A", "公司B"]，
        修复后应序列化为 JSON 字符串。
        """
        data = {
            "fields": [
                {
                    "field_name": "winner_name",
                    "field_status": "multi_value",
                    "raw_value": ["公司A", "公司B"],
                    "candidate_evidences": [],
                }
            ]
        }
        result = parse_extraction_response(data, "test", 0)
        assert len(result.fields) == 1
        rv = result.fields[0].raw_value
        assert isinstance(rv, str)
        parsed = json.loads(rv)
        assert parsed == ["公司A", "公司B"]

    def test_parse_int_raw_value_coerced(self):
        """K3-W3-03 方案A：raw_value 为非 str 标量时强转 str。

        LLM 偶尔输出 int/float，修复后应强转为 str。
        """
        data = {
            "fields": [
                {
                    "field_name": "amount",
                    "field_status": "present",
                    "raw_value": 1000000,
                    "amount_type": "budget",
                    "candidate_evidences": [],
                }
            ]
        }
        result = parse_extraction_response(data, "test", 0)
        assert len(result.fields) == 1
        assert result.fields[0].raw_value == "1000000"
        assert isinstance(result.fields[0].raw_value, str)

    def test_parse_field_validation_error_skipped(self):
        """K3-W3-03 方案D：单字段构造失败时跳过，保留其他字段。

        原实现单字段失败导致整篇 6 字段全丢，
        修复后应跳过失败字段，保留可解析字段。

        构造触发场景：candidate_evidences[].evidence_text 为 dict，
        _validate_extraction 只检查 key 存在不检查类型，
        但 pydantic CandidateEvidence 构造时 dict 不能转 str，抛 ValidationError。
        """
        data = {
            "fields": [
                {
                    "field_name": "project_identifier",
                    "field_status": "present",
                    "raw_value": "ZFCG-2026-001",
                    "candidate_evidences": [],
                },
                {
                    "field_name": "amount",
                    "field_status": "present",
                    "raw_value": "100万元",
                    "amount_type": "budget",
                    # evidence_text 传 dict，绕过 _validate_extraction（只查 key 存在），
                    # 在 CandidateEvidence 构造时触发 pydantic ValidationError
                    "candidate_evidences": [
                        {"evidence_text": {"nested": "value"}, "role": "primary"}
                    ],
                },
                {
                    "field_name": "purchaser_name",
                    "field_status": "present",
                    "raw_value": "某机关单位",
                    "candidate_evidences": [],
                },
            ]
        }
        result = parse_extraction_response(data, "test", 0)
        # 失败字段被跳过，保留 2 个可解析字段
        field_names = [f.field_name for f in result.fields]
        assert "project_identifier" in field_names
        assert "purchaser_name" in field_names
        assert "amount" not in field_names
        assert len(result.fields) == 2


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


# ========== W2-08 A 组无证据 prompt 测试 ==========


class TestNoEvidencePrompt:
    """无证据 prompt 常量完整性测试 (P1 修复)。"""

    def test_no_evidence_prompt_exists(self):
        """无证据 prompt 常量存在。"""
        assert EXTRACTION_SYSTEM_PROMPT_NO_EVIDENCE is not None
        assert isinstance(EXTRACTION_SYSTEM_PROMPT_NO_EVIDENCE, str)
        assert len(EXTRACTION_SYSTEM_PROMPT_NO_EVIDENCE) > 100

    def test_no_evidence_prompt_contains_six_fields(self):
        """无证据 prompt 仍包含六类核心字段定义。"""
        for field_name in CORE_FIELD_NAMES:
            assert field_name in EXTRACTION_SYSTEM_PROMPT_NO_EVIDENCE

    def test_no_evidence_prompt_no_candidate_evidences_requirement(self):
        """Sol 要求 (P1)：A 组 prompt 不应要求 candidate_evidences。"""
        # 无证据 prompt 不应包含 "candidate_evidences" 输出要求
        # (原 prompt 有 "每个字段必须提供候选证据" 的要求)
        assert "必须提供候选证据" not in EXTRACTION_SYSTEM_PROMPT_NO_EVIDENCE
        assert "candidate_evidences" not in EXTRACTION_SYSTEM_PROMPT_NO_EVIDENCE

    def test_no_evidence_prompt_no_evidence_role(self):
        """无证据 prompt 不应要求证据角色标注。"""
        # P1-16 修复：重写断言，确保 no_evidence prompt 不包含证据角色关键词
        assert "primary" not in EXTRACTION_SYSTEM_PROMPT_NO_EVIDENCE
        assert "context" not in EXTRACTION_SYSTEM_PROMPT_NO_EVIDENCE
        assert "qualifier" not in EXTRACTION_SYSTEM_PROMPT_NO_EVIDENCE

    def test_no_evidence_fewshot_exists(self):
        """无证据 few-shot 示例存在。"""
        assert EXTRACTION_FEWSHOT_EXAMPLES_NO_EVIDENCE is not None
        assert len(EXTRACTION_FEWSHOT_EXAMPLES_NO_EVIDENCE) > 0
        ex = EXTRACTION_FEWSHOT_EXAMPLES_NO_EVIDENCE[0]
        assert "raw_text" in ex
        assert "result" in ex
        assert "fields" in ex["result"]

    def test_no_evidence_fewshot_no_candidate_evidences(self):
        """无证据 few-shot 的 fields 不含 candidate_evidences 字段。"""
        for ex in EXTRACTION_FEWSHOT_EXAMPLES_NO_EVIDENCE:
            for f in ex["result"]["fields"]:
                # 无证据 few-shot 不输出 candidate_evidences
                assert "candidate_evidences" not in f or f["candidate_evidences"] == []

    def test_no_evidence_fewshot_has_six_fields(self):
        """无证据 few-shot 仍包含六类核心字段。"""
        ex = EXTRACTION_FEWSHOT_EXAMPLES_NO_EVIDENCE[0]
        field_names = [f["field_name"] for f in ex["result"]["fields"]]
        for name in CORE_FIELD_NAMES:
            assert name in field_names


class TestComputePromptHashNoEvidence:
    """compute_prompt_hash 带参数测试 (P1 修复)。"""

    def test_default_hash_stable(self):
        """默认 hash (无参数) 与原行为一致。"""
        h1 = compute_prompt_hash()
        h2 = compute_prompt_hash()
        assert h1 == h2
        assert len(h1) == 16

    def test_no_evidence_hash_stable(self):
        """无证据 hash 稳定。"""
        h1 = compute_prompt_hash(
            EXTRACTION_SYSTEM_PROMPT_NO_EVIDENCE,
            EXTRACTION_FEWSHOT_EXAMPLES_NO_EVIDENCE,
        )
        h2 = compute_prompt_hash(
            EXTRACTION_SYSTEM_PROMPT_NO_EVIDENCE,
            EXTRACTION_FEWSHOT_EXAMPLES_NO_EVIDENCE,
        )
        assert h1 == h2
        assert len(h1) == 16

    def test_no_evidence_hash_differs_from_default(self):
        """Sol 要求 (P1)：A 组 prompt_hash 必须与 B/C 组不同。"""
        h_default = compute_prompt_hash()
        h_no_evidence = compute_prompt_hash(
            EXTRACTION_SYSTEM_PROMPT_NO_EVIDENCE,
            EXTRACTION_FEWSHOT_EXAMPLES_NO_EVIDENCE,
        )
        assert h_default != h_no_evidence

    def test_different_prompts_different_hash(self):
        """不同 prompt 产生不同 hash。"""
        h1 = compute_prompt_hash("prompt_a", [])
        h2 = compute_prompt_hash("prompt_b", [])
        assert h1 != h2


class TestBuildExtractionPromptNoEvidence:
    """build_extraction_prompt_no_evidence 测试 (P1 修复)。"""

    def test_build_includes_raw_text(self):
        """构建的 prompt 包含原文。"""
        raw = "测试公告原文内容"
        prompt = build_extraction_prompt_no_evidence(raw)
        assert raw in prompt

    def test_build_includes_fewshot(self):
        """构建的 prompt 包含 few-shot 示例。"""
        prompt = build_extraction_prompt_no_evidence("测试")
        # few-shot 示例的原文应出现在 prompt 中
        ex_raw = EXTRACTION_FEWSHOT_EXAMPLES_NO_EVIDENCE[0]["raw_text"]
        assert ex_raw in prompt

    def test_build_no_candidate_evidences_in_fewshot(self):
        """构建的 prompt 的 few-shot 不含 candidate_evidences。"""
        prompt = build_extraction_prompt_no_evidence("测试")
        # few-shot 不应输出 candidate_evidences 字段
        # (原 build_extraction_prompt 的 few-shot 含 candidate_evidences)
        assert "candidate_evidences" not in prompt


class TestCallExtractionLLMNoEvidence:
    """call_extraction_llm_no_evidence 测试 (P1 修复)。"""

    @pytest.mark.asyncio
    async def test_call_no_evidence_no_api_key_raises(self):
        """无 API key 抛异常。"""
        with patch("app.llm.extractor.settings") as mock_settings:
            mock_settings.DEEPSEEK_API_KEY = ""
            with pytest.raises(RuntimeError, match="not configured"):
                await call_extraction_llm_no_evidence("测试文本")

    @pytest.mark.asyncio
    async def test_call_no_evidence_api_failure_returns_error(self):
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

                result = await call_extraction_llm_no_evidence("测试文本")

        assert isinstance(result, ExtractionResult)
        assert result.error is not None
        assert "API 错误" in result.error
        assert result.fields == []
        assert result.model_id == "test-model"
        assert result.prompt_hash != ""
        assert result.total_tokens == 0

    @pytest.mark.asyncio
    async def test_call_no_evidence_success(self):
        """成功调用返回解析后的结果 (无证据版本)。"""
        # 无证据 LLM 返回的 JSON 不含 candidate_evidences
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
                                        # 无证据版本不输出 candidate_evidences
                                    }
                                ]
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ],
            "usage": {"total_tokens": 400},
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

                result = await call_extraction_llm_no_evidence("招标公告")

        assert result.error is None
        assert len(result.fields) == 1
        assert result.fields[0].raw_value == "ZFCG-2026-001"
        assert result.fields[0].candidate_evidences == []  # 无证据版本
        assert result.model_id == "test-model"
        assert result.total_tokens == 400
        assert result.latency_ms >= 0
        # prompt_hash 应为无证据版本 (与默认不同)
        default_hash = compute_prompt_hash()
        assert result.prompt_hash != default_hash
        no_ev_hash = compute_prompt_hash(
            EXTRACTION_SYSTEM_PROMPT_NO_EVIDENCE,
            EXTRACTION_FEWSHOT_EXAMPLES_NO_EVIDENCE,
        )
        assert result.prompt_hash == no_ev_hash

# ========== markdown fence 剥离测试 (#42 修复) ==========


class TestStripMarkdownFence:
    """_strip_markdown_fence 测试 (#42 修复)。"""

    def test_plain_json(self):
        """无 fence 包裹，原样返回（仅 strip 前后空白）。"""
        raw = '{"field_name": "project_identifier"}'
        assert _strip_markdown_fence(raw) == raw

    def test_json_with_fence(self):
        """```json...``` 包裹，剥离后可解析。"""
        raw = '```json\n{"field_name": "project_identifier"}\n```'
        stripped = _strip_markdown_fence(raw)
        assert stripped == '{"field_name": "project_identifier"}'
        assert json.loads(stripped)["field_name"] == "project_identifier"

    def test_json_with_plain_fence(self):
        """```...``` 包裹，剥离后可解析。"""
        raw = '```\n{"field_name": "winner_name"}\n```'
        stripped = _strip_markdown_fence(raw)
        assert stripped == '{"field_name": "winner_name"}'
        assert json.loads(stripped)["field_name"] == "winner_name"

    def test_json_with_whitespace(self):
        """带前后空白，剥离后可解析。"""
        raw = '\n  ```json\n{"a": 1}\n```\n  '
        stripped = _strip_markdown_fence(raw)
        assert stripped == '{"a": 1}'
        assert json.loads(stripped) == {"a": 1}

    def test_already_valid_json(self):
        """已是有效 JSON，不变。"""
        raw = '{"a": 1, "b": 2}'
        assert _strip_markdown_fence(raw) == raw


# ========== K3-W3-03 raw_value 归一化 + per-field 容错测试 ==========


class TestRawValueNormalization:
    """K3-W3-03 方案 A：dict/list raw_value 归一化为 JSON 字符串。"""

    def test_dict_raw_value_normalized_to_json_string(self):
        """联合体 dict raw_value 不再炸 parse，转为可逆 JSON 字符串。"""
        data = {
            "fields": [
                {
                    "field_name": "winner_name",
                    "field_status": "present",
                    "raw_value": {"main": "甲建设集团有限公司", "partners": ["乙信息技术有限公司"]},
                    "candidate_evidences": [
                        {"evidence_text": "中标人：甲建设集团有限公司（联合体牵头人）", "role": "primary"}
                    ],
                }
            ]
        }
        result = parse_extraction_response(data, "test", 0)
        assert len(result.fields) == 1
        rv = result.fields[0].raw_value
        assert isinstance(rv, str)
        parsed_back = json.loads(rv)
        assert parsed_back["main"] == "甲建设集团有限公司"
        assert parsed_back["partners"] == ["乙信息技术有限公司"]

    def test_list_raw_value_normalized(self):
        data = {
            "fields": [
                {"field_name": "winner_name", "field_status": "present",
                 "raw_value": ["甲公司", "乙公司"], "candidate_evidences": []}
            ]
        }
        result = parse_extraction_response(data, "test", 0)
        assert isinstance(result.fields[0].raw_value, str)
        assert json.loads(result.fields[0].raw_value) == ["甲公司", "乙公司"]

    def test_scalar_raw_value_coerced_to_str(self):
        """int raw_value（LLM 把金额输出成数字）强转 str，不炸。"""
        data = {
            "fields": [
                {"field_name": "amount", "field_status": "present",
                 "raw_value": 2580, "amount_type": "award", "candidate_evidences": []}
            ]
        }
        result = parse_extraction_response(data, "test", 0)
        assert result.fields[0].raw_value == "2580"

    def test_str_raw_value_unchanged(self):
        """正常 str 不受影响（防回归）。"""
        data = {
            "fields": [
                {"field_name": "winner_name", "field_status": "present",
                 "raw_value": "广东美的制冷设备有限公司", "candidate_evidences": []}
            ]
        }
        result = parse_extraction_response(data, "test", 0)
        assert result.fields[0].raw_value == "广东美的制冷设备有限公司"

    def test_none_raw_value_unchanged(self):
        data = {
            "fields": [
                {"field_name": "winner_name", "field_status": "absent",
                 "raw_value": None, "candidate_evidences": []}
            ]
        }
        result = parse_extraction_response(data, "test", 0)
        assert result.fields[0].raw_value is None


class TestPerFieldFaultTolerance:
    """K3-W3-03 方案 D：单字段构造失败不拖垮整篇公告。"""

    def test_broken_field_skipped_others_survive(self):
        """evidence_text 非 str 的字段被跳过，其余字段正常解析。"""
        data = {
            "fields": [
                {"field_name": "project_identifier", "field_status": "present",
                 "raw_value": "ZFCG-2026-001",
                 "candidate_evidences": [{"evidence_text": "项目编号：ZFCG-2026-001", "role": "primary"}]},
                {"field_name": "winner_name", "field_status": "present",
                 "raw_value": "某公司",
                 "candidate_evidences": [{"evidence_text": {"bad": "type"}, "role": "primary"}]},
                {"field_name": "amount", "field_status": "present",
                 "raw_value": "100万元", "amount_type": "budget", "candidate_evidences": []},
            ]
        }
        result = parse_extraction_response(data, "test", 0)
        names = [f.field_name for f in result.fields]
        assert "project_identifier" in names
        assert "amount" in names
        assert "winner_name" not in names
        assert len(result.fields) == 2
