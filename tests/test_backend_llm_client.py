"""BidAgent v4.1 OpenAI 兼容 HTTP LLM 客户端测试（W1-06 补丁）。

覆盖：
- OpenAICompatibleClient 构造与配置校验
- from_env 环境变量加载
- complete 成功路径（用 httpx.MockTransport 注入）
- HTTP 错误分类（429/401/500）
- 响应解析异常处理
- Token 统计优先用 API 返回 usage，缺失时 fallback
- async context manager 正常关闭

工程规范：
- 不调用真实 API
- 用 httpx.MockTransport 注入响应，避免网络
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from backend.extractors import DirectLLMBaseline, LLMResponse
from backend.llm_client import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    LLMAuthError,
    LLMClientError,
    LLMRateLimitError,
    LLMResponseError,
    LLMServerError,
    LLMTimeoutError,
    OpenAICompatibleClient,
)


# ============================================================
# 工具：构造 mock transport
# ============================================================


def _make_mock_transport(
    status_code: int = 200,
    json_body: dict[str, Any] | None = None,
    body_text: str | None = None,
) -> httpx.MockTransport:
    """构造 mock transport，返回固定响应。"""
    if json_body is not None:
        content = json.dumps(json_body).encode("utf-8")
        headers = {"content-type": "application/json"}
    else:
        content = (body_text or "").encode("utf-8")
        headers = {"content-type": "text/plain"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=status_code,
            content=content,
            headers=headers,
        )

    return httpx.MockTransport(handler)


def _make_openai_response(
    content: str = '{"fields": []}',
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
) -> dict[str, Any]:
    """构造标准 OpenAI Chat Completions 响应。"""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "glm-5.2",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


# ============================================================
# 测试套件 1：构造与配置
# ============================================================


class TestConstruction:
    def test_valid_construction(self):
        client = OpenAICompatibleClient(
            base_url="https://api.example.com/v1",
            api_key="sk-test",
            model="test-model",
        )
        assert client.model == "test-model"
        assert client.base_url == "https://api.example.com/v1"

    def test_strips_trailing_slash(self):
        client = OpenAICompatibleClient(
            base_url="https://api.example.com/v1/",
            api_key="sk-test",
            model="m",
        )
        assert client.base_url == "https://api.example.com/v1"

    def test_empty_base_url_rejected(self):
        with pytest.raises(ValueError, match="base_url"):
            OpenAICompatibleClient(base_url="", api_key="k", model="m")

    def test_empty_api_key_rejected(self):
        with pytest.raises(ValueError, match="api_key"):
            OpenAICompatibleClient(base_url="http://x", api_key="", model="m")

    def test_empty_model_rejected(self):
        with pytest.raises(ValueError, match="model"):
            OpenAICompatibleClient(base_url="http://x", api_key="k", model="")


class TestFromEnv:
    def test_from_env_uses_defaults(self, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "sk-env-test")
        monkeypatch.delenv("LLM_BASE_URL", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)
        monkeypatch.delenv("LLM_TIMEOUT_SECONDS", raising=False)

        client = OpenAICompatibleClient.from_env()
        assert client.base_url == DEFAULT_BASE_URL
        assert client.model == DEFAULT_MODEL

    def test_from_env_reads_custom_values(self, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "sk-custom")
        monkeypatch.setenv("LLM_BASE_URL", "https://custom.example.com")
        monkeypatch.setenv("LLM_MODEL", "custom-model")
        monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "30")

        client = OpenAICompatibleClient.from_env()
        assert client.base_url == "https://custom.example.com"
        assert client.model == "custom-model"

    def test_from_env_missing_key_raises(self, monkeypatch):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        with pytest.raises(ValueError, match="LLM_API_KEY"):
            OpenAICompatibleClient.from_env()


# ============================================================
# 测试套件 2：complete 成功路径
# ============================================================


class TestCompleteSuccess:
    @pytest.mark.asyncio
    async def test_successful_completion(self):
        body = _make_openai_response(
            content='{"fields": [{"field_name": "amount", "values": [{"raw_value": "100"}]}]}',
            prompt_tokens=120,
            completion_tokens=30,
        )
        transport = _make_mock_transport(status_code=200, json_body=body)
        client = OpenAICompatibleClient(
            base_url="https://api.example.com/v1",
            api_key="sk-test",
            model="glm-5.2",
            transport=transport,
        )

        resp = await client.complete("system", "user")
        assert isinstance(resp, LLMResponse)
        assert "fields" in resp.content
        assert resp.prompt_tokens == 120
        assert resp.completion_tokens == 30
        assert resp.total_tokens == 150
        assert resp.latency_ms is not None and resp.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_token_usage_fallback_when_missing(self):
        """API 不返回 usage 时按字符数估算。"""
        body = {
            "choices": [
                {"message": {"content": "hello"}, "finish_reason": "stop"}
            ],
            # 没有 usage 字段
        }
        transport = _make_mock_transport(status_code=200, json_body=body)
        client = OpenAICompatibleClient(
            base_url="https://api.example.com/v1",
            api_key="sk-test",
            model="glm-5.2",
            transport=transport,
        )
        resp = await client.complete("system prompt", "user prompt")
        assert resp.prompt_tokens > 0
        assert resp.completion_tokens > 0
        assert resp.total_tokens == resp.prompt_tokens + resp.completion_tokens

    @pytest.mark.asyncio
    async def test_payload_includes_model_and_messages(self):
        """验证请求 payload 结构正确。"""
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            captured["headers"] = dict(request.headers)
            return httpx.Response(
                200,
                content=json.dumps(_make_openai_response()).encode(),
                headers={"content-type": "application/json"},
            )

        transport = httpx.MockTransport(handler)
        client = OpenAICompatibleClient(
            base_url="https://api.example.com/v1",
            api_key="sk-test",
            model="glm-5.2",
            transport=transport,
        )
        await client.complete("sys", "user", temperature=0.5, max_tokens=100)

        assert captured["body"]["model"] == "glm-5.2"
        assert captured["body"]["temperature"] == 0.5
        assert captured["body"]["max_tokens"] == 100
        assert len(captured["body"]["messages"]) == 2
        assert captured["body"]["messages"][0]["role"] == "system"
        assert captured["body"]["messages"][1]["role"] == "user"
        # Authorization 头
        assert captured["headers"]["authorization"] == "Bearer sk-test"

    @pytest.mark.asyncio
    async def test_integration_with_direct_baseline(self):
        """端到端：OpenAICompatibleClient 注入 DirectLLMBaseline。"""
        body = _make_openai_response(content='{"fields": []}')
        transport = _make_mock_transport(status_code=200, json_body=body)
        client = OpenAICompatibleClient(
            base_url="https://api.example.com/v1",
            api_key="sk-test",
            model="glm-5.2",
            transport=transport,
        )
        baseline = DirectLLMBaseline(client=client, model_identifier="glm-5.2")
        record = await baseline.extract_one("doc-1", "公告正文", "tender")

        assert record.success is True
        assert record.model_identifier == "glm-5.2"
        assert record.output is not None
        await client.close()


# ============================================================
# 测试套件 3：HTTP 错误分类
# ============================================================


class TestHttpErrorClassification:
    @pytest.mark.asyncio
    async def test_rate_limit_429(self):
        transport = _make_mock_transport(status_code=429, body_text="rate limited")
        client = OpenAICompatibleClient(
            base_url="http://x", api_key="k", model="m", transport=transport
        )
        with pytest.raises(LLMRateLimitError) as exc:
            await client.complete("sys", "user")
        assert "429" in str(exc.value)

    @pytest.mark.asyncio
    async def test_auth_error_401(self):
        transport = _make_mock_transport(status_code=401, body_text="unauthorized")
        client = OpenAICompatibleClient(
            base_url="http://x", api_key="k", model="m", transport=transport
        )
        with pytest.raises(LLMAuthError):
            await client.complete("sys", "user")

    @pytest.mark.asyncio
    async def test_auth_error_403(self):
        transport = _make_mock_transport(status_code=403, body_text="forbidden")
        client = OpenAICompatibleClient(
            base_url="http://x", api_key="k", model="m", transport=transport
        )
        with pytest.raises(LLMAuthError):
            await client.complete("sys", "user")

    @pytest.mark.asyncio
    async def test_server_error_500(self):
        transport = _make_mock_transport(status_code=500, body_text="internal error")
        client = OpenAICompatibleClient(
            base_url="http://x", api_key="k", model="m", transport=transport
        )
        with pytest.raises(LLMServerError):
            await client.complete("sys", "user")

    @pytest.mark.asyncio
    async def test_server_error_503(self):
        transport = _make_mock_transport(status_code=503, body_text="unavailable")
        client = OpenAICompatibleClient(
            base_url="http://x", api_key="k", model="m", transport=transport
        )
        with pytest.raises(LLMServerError):
            await client.complete("sys", "user")


# ============================================================
# 测试套件 4：超时与网络错误
# ============================================================


class TestTimeoutAndNetwork:
    @pytest.mark.asyncio
    async def test_timeout_raises_specific_error(self):
        # 用一个会一直 hang 的 transport
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("simulated timeout")

        transport = httpx.MockTransport(handler)
        client = OpenAICompatibleClient(
            base_url="http://x", api_key="k", model="m",
            timeout_seconds=0.1, transport=transport,
        )
        with pytest.raises(LLMTimeoutError) as exc:
            await client.complete("sys", "user")
        assert "超时" in str(exc.value)

    @pytest.mark.asyncio
    async def test_generic_http_error_wrapped(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        transport = httpx.MockTransport(handler)
        client = OpenAICompatibleClient(
            base_url="http://x", api_key="k", model="m", transport=transport
        )
        with pytest.raises(LLMClientError) as exc:
            await client.complete("sys", "user")
        assert "网络错误" in str(exc.value)


# ============================================================
# 测试套件 5：响应解析异常
# ============================================================


class TestResponseParsingErrors:
    @pytest.mark.asyncio
    async def test_non_json_body_raises(self):
        transport = _make_mock_transport(
            status_code=200, body_text="not a json at all"
        )
        client = OpenAICompatibleClient(
            base_url="http://x", api_key="k", model="m", transport=transport
        )
        with pytest.raises(LLMResponseError) as exc:
            await client.complete("sys", "user")
        assert "非合法 JSON" in str(exc.value) or "JSON" in str(exc.value)

    @pytest.mark.asyncio
    async def test_missing_choices_raises(self):
        body = {"id": "test"}  # 缺 choices
        transport = _make_mock_transport(status_code=200, json_body=body)
        client = OpenAICompatibleClient(
            base_url="http://x", api_key="k", model="m", transport=transport
        )
        with pytest.raises(LLMResponseError) as exc:
            await client.complete("sys", "user")
        assert "choices" in str(exc.value) or "结构异常" in str(exc.value)

    @pytest.mark.asyncio
    async def test_missing_message_content_raises(self):
        body = {
            "choices": [{"message": {}}]  # 缺 content
        }
        transport = _make_mock_transport(status_code=200, json_body=body)
        client = OpenAICompatibleClient(
            base_url="http://x", api_key="k", model="m", transport=transport
        )
        with pytest.raises(LLMResponseError):
            await client.complete("sys", "user")

    @pytest.mark.asyncio
    async def test_empty_choices_raises(self):
        body = {"choices": []}
        transport = _make_mock_transport(status_code=200, json_body=body)
        client = OpenAICompatibleClient(
            base_url="http://x", api_key="k", model="m", transport=transport
        )
        with pytest.raises(LLMResponseError):
            await client.complete("sys", "user")


# ============================================================
# 测试套件 6：资源管理
# ============================================================


class TestResourceManagement:
    @pytest.mark.asyncio
    async def test_async_context_manager_closes(self):
        body = _make_openai_response()
        transport = _make_mock_transport(status_code=200, json_body=body)
        async with OpenAICompatibleClient(
            base_url="http://x", api_key="k", model="m", transport=transport
        ) as client:
            resp = await client.complete("sys", "user")
            assert resp.content is not None
        # 关闭后再次调用应失败
        with pytest.raises(Exception):
            await client.complete("sys", "user")

    @pytest.mark.asyncio
    async def test_explicit_close(self):
        body = _make_openai_response()
        transport = _make_mock_transport(status_code=200, json_body=body)
        client = OpenAICompatibleClient(
            base_url="http://x", api_key="k", model="m", transport=transport
        )
        await client.complete("sys", "user")
        await client.close()
        # 不抛异常即可
