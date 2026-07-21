"""BidAgent v4.1 Direct LLM Baseline 抽取器测试（W1-06）。

覆盖：
- 提示词构造与哈希稳定性
- StubLLMClient 桩行为
- DirectLLMBaseline.extract_one：成功/失败/重试
- DirectLLMBaseline.extract_batch：并发与顺序
- JSON 解析宽容性（markdown fence、单字段、字段数组）
- 失败记录不静默丢弃
- save/load JSONL 往返一致性

工程规范：
- 不调用真实 LLM API
- 使用 StubLLMClient 注入
- 验证 LLMExtractionRecord 的元数据完整性
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from backend.enums import CoreFieldName, SupportLevel
from backend.extractors import (
    PROMPT_VERSION,
    DirectLLMBaseline,
    LLMResponse,
    StubLLMClient,
    build_prompt,
    compute_prompt_hash,
    load_records_jsonl,
    save_records_jsonl,
)
from backend.schemas import LLMExtractionRecord


# ============================================================
# 测试套件 1：提示词构造与哈希
# ============================================================


class TestPromptConstruction:
    """提示词构造与哈希稳定性。"""

    def test_build_prompt_returns_system_user(self):
        sys_p, user_p = build_prompt("公告正文", "tender")
        assert isinstance(sys_p, str)
        assert isinstance(user_p, str)
        assert "公告正文" in user_p
        assert "tender" in user_p

    def test_build_prompt_handles_none_notice_type(self):
        sys_p, user_p = build_prompt("正文", None)
        assert "未知" in user_p

    def test_prompt_hash_stable(self):
        """相同输入必须产生相同 hash。"""
        sys_p, user_p = build_prompt("正文", "tender")
        h1 = compute_prompt_hash(sys_p, user_p)
        h2 = compute_prompt_hash(sys_p, user_p)
        assert h1 == h2
        assert len(h1) == 64  # SHA256 hex

    def test_prompt_hash_differs_on_different_input(self):
        sys1, user1 = build_prompt("正文 A", "tender")
        sys2, user2 = build_prompt("正文 B", "tender")
        assert compute_prompt_hash(sys1, user1) != compute_prompt_hash(sys2, user2)

    def test_prompt_version_constant(self):
        assert PROMPT_VERSION == "1.0"


# ============================================================
# 测试套件 2：StubLLMClient
# ============================================================


class TestStubLLMClient:
    """桩客户端行为。"""

    @pytest.mark.asyncio
    async def test_default_response_is_valid_json(self):
        client = StubLLMClient()
        sys_p, user_p = build_prompt("正文", "tender")
        resp = await client.complete(sys_p, user_p)
        assert isinstance(resp, LLMResponse)
        data = json.loads(resp.content)
        assert "fields" in data

    @pytest.mark.asyncio
    async def test_custom_responder(self):
        def custom(sys_p: str, user_p: str) -> str:
            return json.dumps({"fields": []})

        client = StubLLMClient(responder=custom)
        resp = await client.complete("sys", "user")
        assert json.loads(resp.content) == {"fields": []}

    @pytest.mark.asyncio
    async def test_latency_simulated(self):
        client = StubLLMClient(latency_ms=50)
        import time
        start = time.monotonic()
        await client.complete("sys", "user")
        elapsed = time.monotonic() - start
        assert elapsed >= 0.04  # 容许少量误差

    @pytest.mark.asyncio
    async def test_token_counts_populated(self):
        client = StubLLMClient()
        resp = await client.complete("system prompt", "user prompt")
        assert resp.prompt_tokens is not None
        assert resp.completion_tokens is not None
        assert resp.total_tokens is not None
        assert resp.total_tokens >= resp.prompt_tokens


# ============================================================
# 测试套件 3：DirectLLMBaseline.extract_one 成功路径
# ============================================================


class TestExtractOneSuccess:
    """单文档抽取 - 成功路径。"""

    @pytest.mark.asyncio
    async def test_successful_extraction(self):
        client = StubLLMClient()
        baseline = DirectLLMBaseline(client=client, model_identifier="stub-1.0")

        record = await baseline.extract_one(
            document_id="doc-001",
            notice_text="某采购项目招标公告\n项目编号：SH-2026-001\n采购人：某机关",
            notice_type="tender",
        )

        assert record.success is True
        assert record.document_id == "doc-001"
        assert record.model_identifier == "stub-1.0"
        assert record.prompt_version == "1.0"
        assert len(record.prompt_hash) == 64
        assert record.error_message is None
        assert record.output is not None
        assert len(record.output.fields) >= 1
        assert record.started_at is not None
        assert record.finished_at is not None
        assert record.latency_ms is not None and record.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_temperature_and_max_tokens_recorded(self):
        client = StubLLMClient()
        baseline = DirectLLMBaseline(
            client=client,
            model_identifier="stub",
            temperature=0.3,
            max_tokens=2048,
        )
        record = await baseline.extract_one("doc", "正文", "tender")
        assert record.temperature == 0.3
        assert record.max_tokens == 2048

    @pytest.mark.asyncio
    async def test_prompt_hash_depends_on_notice_text(self):
        """不同公告应产生不同 prompt_hash（消融实验可追溯）。"""
        client = StubLLMClient()
        baseline = DirectLLMBaseline(client=client, model_identifier="stub")

        r1 = await baseline.extract_one("d1", "正文 A", "tender")
        r2 = await baseline.extract_one("d2", "正文 B", "tender")
        assert r1.prompt_hash != r2.prompt_hash

    @pytest.mark.asyncio
    async def test_token_counts_from_response(self):
        client = StubLLMClient()
        baseline = DirectLLMBaseline(client=client, model_identifier="stub")
        record = await baseline.extract_one("d1", "正文", "tender")
        assert record.prompt_tokens is not None and record.prompt_tokens > 0
        assert record.completion_tokens is not None and record.completion_tokens > 0
        assert record.total_tokens is not None and record.total_tokens > 0


# ============================================================
# 测试套件 4：DirectLLMBaseline.extract_one 失败路径
# ============================================================


class TestExtractOneFailure:
    """单文档抽取 - 失败路径不静默丢弃。"""

    @pytest.mark.asyncio
    async def test_client_exception_recorded(self):
        class FailingClient:
            async def complete(self, system_prompt, user_prompt, temperature=0.0, max_tokens=None):
                raise RuntimeError("API 限流")

        baseline = DirectLLMBaseline(client=FailingClient(), model_identifier="fail")
        record = await baseline.extract_one("doc-fail", "正文", "tender")

        assert record.success is False
        assert record.output is None
        assert record.error_message is not None
        assert "API 限流" in record.error_message
        assert "RuntimeError" in record.error_message

    @pytest.mark.asyncio
    async def test_invalid_json_recorded(self):
        """LLM 返回非 JSON 时记录失败。"""
        client = StubLLMClient(responder=lambda s, u: "not a json at all")
        baseline = DirectLLMBaseline(client=client, model_identifier="stub")
        record = await baseline.extract_one("doc", "正文", "tender")

        assert record.success is False
        assert record.output is None
        assert "JSON" in record.error_message or "json" in record.error_message.lower()

    @pytest.mark.asyncio
    async def test_retry_then_succeed(self):
        """配置 max_retries 后，第一次失败第二次成功应被记录为成功。"""
        attempts = {"count": 0}

        class RetryClient:
            async def complete(self, system_prompt, user_prompt, temperature=0.0, max_tokens=None):
                attempts["count"] += 1
                if attempts["count"] == 1:
                    raise ConnectionError("网络抖动")
                return LLMResponse(content='{"fields": []}', prompt_tokens=10, completion_tokens=5, total_tokens=15)

        baseline = DirectLLMBaseline(
            client=RetryClient(), model_identifier="retry", max_retries=2
        )
        record = await baseline.extract_one("doc", "正文", "tender")
        assert record.success is True
        assert attempts["count"] == 2

    @pytest.mark.asyncio
    async def test_all_retries_exhausted(self):
        attempts = {"count": 0}

        class AlwaysFailingClient:
            async def complete(self, system_prompt, user_prompt, temperature=0.0, max_tokens=None):
                attempts["count"] += 1
                raise TimeoutError("超时")

        baseline = DirectLLMBaseline(
            client=AlwaysFailingClient(), model_identifier="retry",
            max_retries=2,
        )
        record = await baseline.extract_one("doc", "正文", "tender")
        assert record.success is False
        assert "attempt 3/3" in record.error_message
        assert attempts["count"] == 3

    @pytest.mark.asyncio
    async def test_empty_document_id_rejected(self):
        baseline = DirectLLMBaseline(client=StubLLMClient(), model_identifier="stub")
        with pytest.raises(ValueError, match="document_id"):
            await baseline.extract_one("", "正文", "tender")

    @pytest.mark.asyncio
    async def test_empty_notice_text_rejected(self):
        baseline = DirectLLMBaseline(client=StubLLMClient(), model_identifier="stub")
        with pytest.raises(ValueError, match="notice_text"):
            await baseline.extract_one("doc", "", "tender")
        with pytest.raises(ValueError, match="notice_text"):
            await baseline.extract_one("doc", "   ", "tender")


# ============================================================
# 测试套件 5：JSON 解析宽容性
# ============================================================


class TestJsonParsingTolerance:
    """LLM 输出格式宽容性。"""

    @pytest.mark.asyncio
    async def test_pure_json_parsed(self):
        client = StubLLMClient(responder=lambda s, u: '{"fields": []}')
        baseline = DirectLLMBaseline(client=client, model_identifier="stub")
        record = await baseline.extract_one("d", "正文", "tender")
        assert record.success is True
        assert record.output is not None
        assert record.output.fields == []

    @pytest.mark.asyncio
    async def test_markdown_fenced_json_parsed(self):
        def responder(s, u):
            return '```json\n{"fields": []}\n```'

        client = StubLLMClient(responder=responder)
        baseline = DirectLLMBaseline(client=client, model_identifier="stub")
        record = await baseline.extract_one("d", "正文", "tender")
        assert record.success is True
        assert record.output is not None

    @pytest.mark.asyncio
    async def test_bare_field_array_wrapped(self):
        """LLM 直接返回字段数组时自动包装。"""
        def responder(s, u):
            return json.dumps([
                {
                    "field_name": CoreFieldName.AMOUNT,
                    "support_level": "direct",
                    "values": [{"raw_value": "100"}],
                }
            ])

        client = StubLLMClient(responder=responder)
        baseline = DirectLLMBaseline(client=client, model_identifier="stub")
        record = await baseline.extract_one("d", "正文", "tender")
        assert record.success is True
        assert len(record.output.fields) == 1
        assert record.output.fields[0].field_name == CoreFieldName.AMOUNT

    @pytest.mark.asyncio
    async def test_json_with_surrounding_text_parsed(self):
        """LLM 在 JSON 前后加了解释文字时仍能解析。"""
        def responder(s, u):
            return '好的，以下是抽取结果：\n{"fields": []}\n以上是结果。'

        client = StubLLMClient(responder=responder)
        baseline = DirectLLMBaseline(client=client, model_identifier="stub")
        record = await baseline.extract_one("d", "正文", "tender")
        assert record.success is True


# ============================================================
# 测试套件 6：批量抽取
# ============================================================


class TestExtractBatch:
    """批量抽取与并发控制。"""

    @pytest.mark.asyncio
    async def test_batch_preserves_order(self):
        client = StubLLMClient()
        baseline = DirectLLMBaseline(client=client, model_identifier="stub")
        docs = [(f"doc-{i}", f"正文 {i}", "tender") for i in range(5)]
        records = await baseline.extract_batch(docs, concurrency=3)

        assert len(records) == 5
        for i, r in enumerate(records):
            assert r.document_id == f"doc-{i}"
            assert r.success is True

    @pytest.mark.asyncio
    async def test_batch_empty_input(self):
        client = StubLLMClient()
        baseline = DirectLLMBaseline(client=client, model_identifier="stub")
        records = await baseline.extract_batch([], concurrency=2)
        assert records == []

    @pytest.mark.asyncio
    async def test_batch_invalid_concurrency(self):
        client = StubLLMClient()
        baseline = DirectLLMBaseline(client=client, model_identifier="stub")
        with pytest.raises(ValueError):
            await baseline.extract_batch([("d", "正文", None)], concurrency=0)

    @pytest.mark.asyncio
    async def test_batch_failure_does_not_kill_others(self):
        """一个文档失败不影响其他文档。"""
        class MixedClient:
            def __init__(self):
                self.calls = 0

            async def complete(self, system_prompt, user_prompt, temperature=0.0, max_tokens=None):
                self.calls += 1
                if self.calls == 2:
                    raise RuntimeError("第二个失败")
                return LLMResponse(content='{"fields": []}', prompt_tokens=1, completion_tokens=1, total_tokens=2)

        baseline = DirectLLMBaseline(client=MixedClient(), model_identifier="mixed")
        docs = [(f"d{i}", f"正文{i}", None) for i in range(4)]
        records = await baseline.extract_batch(docs, concurrency=1)

        assert len(records) == 4
        success_count = sum(1 for r in records if r.success)
        failure_count = sum(1 for r in records if not r.success)
        assert success_count == 3
        assert failure_count == 1

    @pytest.mark.asyncio
    async def test_batch_concurrency_respected(self):
        """并发度上限被信号量控制。"""
        max_concurrent = {"current": 0, "peak": 0}

        class TrackingClient:
            async def complete(self, system_prompt, user_prompt, temperature=0.0, max_tokens=None):
                max_concurrent["current"] += 1
                max_concurrent["peak"] = max(max_concurrent["peak"], max_concurrent["current"])
                await asyncio.sleep(0.05)
                max_concurrent["current"] -= 1
                return LLMResponse(content='{"fields": []}', prompt_tokens=1, completion_tokens=1, total_tokens=2)

        baseline = DirectLLMBaseline(client=TrackingClient(), model_identifier="track")
        docs = [(f"d{i}", f"正文{i}", None) for i in range(10)]
        await baseline.extract_batch(docs, concurrency=3)

        assert max_concurrent["peak"] <= 3


# ============================================================
# 测试套件 7：JSONL 持久化
# ============================================================


class TestJsonlPersistence:
    """JSONL 保存与加载。"""

    @pytest.mark.asyncio
    async def test_save_and_load_round_trip(self, tmp_path: Path):
        client = StubLLMClient()
        baseline = DirectLLMBaseline(client=client, model_identifier="stub")
        docs = [(f"doc-{i}", f"正文 {i}", "tender") for i in range(3)]
        records = await baseline.extract_batch(docs, concurrency=2)

        path = tmp_path / "records.jsonl"
        count = save_records_jsonl(records, path)
        assert count == 3
        assert path.exists()

        loaded = load_records_jsonl(path)
        assert len(loaded) == 3
        for original, restored in zip(records, loaded):
            assert restored.document_id == original.document_id
            assert restored.success == original.success
            assert restored.model_identifier == original.model_identifier

    @pytest.mark.asyncio
    async def test_save_includes_failure_records(self, tmp_path: Path):
        """失败记录必须写入 JSONL，不丢弃。"""
        class FailingClient:
            async def complete(self, *args, **kwargs):
                raise RuntimeError("失败")

        baseline = DirectLLMBaseline(client=FailingClient(), model_identifier="fail")
        record = await baseline.extract_one("doc-fail", "正文", "tender")
        assert record.success is False

        path = tmp_path / "fails.jsonl"
        save_records_jsonl([record], path)

        loaded = load_records_jsonl(path)
        assert len(loaded) == 1
        assert loaded[0].success is False
        assert "失败" in loaded[0].error_message

    def test_load_nonexistent_file_returns_empty(self, tmp_path: Path):
        path = tmp_path / "missing.jsonl"
        assert load_records_jsonl(path) == []

    @pytest.mark.asyncio
    async def test_load_skips_corrupt_lines(self, tmp_path: Path):
        """损坏的行应被跳过，不抛异常。"""
        path = tmp_path / "mixed.jsonl"
        path.write_text(
            '{"document_id":"d1","model_identifier":"m","prompt_hash":"a","success":true}\n'
            'this is not json\n'
            '{"document_id":"d2","model_identifier":"m","prompt_hash":"b","success":true}\n'
            '\n',
            encoding="utf-8",
        )
        loaded = load_records_jsonl(path)
        assert len(loaded) == 2
        ids = {r.document_id for r in loaded}
        assert ids == {"d1", "d2"}

    @pytest.mark.asyncio
    async def test_save_creates_parent_directory(self, tmp_path: Path):
        path = tmp_path / "subdir" / "nested" / "records.jsonl"
        save_records_jsonl([], path)
        assert path.exists()
