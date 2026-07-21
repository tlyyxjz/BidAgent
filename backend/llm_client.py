"""BidAgent v4.1 OpenAI 兼容 HTTP LLM 客户端（W1-06 补丁）。

需求来源：
- W1-06 任务清单要求"记录模型标识、参数、Token、延迟和错误"
- 之前只有 StubLLMClient，没有真实调用实现
- 项目硬约束：async/await，不使用 callback

设计原则：
- **OpenAI Chat Completions 兼容**：支持 OpenAI / Azure / 智谱 GLM / DeepSeek 等所有
  兼容 OpenAI API 协议的服务
- **配置驱动**：通过环境变量 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL 注入
- **超时控制**：默认 60s，避免长尾请求阻塞批量任务
- **可注入**：实现 LLMClient Protocol，可直接替换 StubLLMClient
- **错误分类**：区分网络错误 / 限流 / 鉴权失败 / 服务端错误
- **测试隔离**：不内置真实 API key，单测用 httpx.MockTransport 注入

工程规范：
- async/await，遵循项目硬约束
- 结构化日志（request_id 上下文）
- 不在客户端层做重试（由 DirectLLMBaseline 统一处理）
- Token 统计优先用 API 返回的 usage，缺失时按字符数估算
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

from app.utils.logger import get_logger

from backend.extractors import LLMResponse

logger = get_logger("backend.llm_client")


# ============================================================
# 配置常量
# ============================================================


DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = "glm-5.2"
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_RETRIES = 0  # 重试由 DirectLLMBaseline 统一管理


# ============================================================
# 错误分类
# ============================================================


class LLMClientError(Exception):
    """LLM 客户端基础错误。"""


class LLMTimeoutError(LLMClientError):
    """请求超时。"""


class LLMRateLimitError(LLMClientError):
    """服务端限流（HTTP 429）。"""


class LLMAuthError(LLMClientError):
    """鉴权失败（HTTP 401/403）。"""


class LLMServerError(LLMClientError):
    """服务端错误（HTTP 5xx）。"""


class LLMResponseError(LLMClientError):
    """响应解析失败或返回错误结构。"""


def _classify_http_error(status_code: int, body: str) -> LLMClientError:
    """根据 HTTP 状态码分类错误。"""
    if status_code == 429:
        return LLMRateLimitError(f"HTTP 429 限流：{body[:200]}")
    if status_code in (401, 403):
        return LLMAuthError(f"HTTP {status_code} 鉴权失败：{body[:200]}")
    if 500 <= status_code < 600:
        return LLMServerError(f"HTTP {status_code} 服务端错误：{body[:200]}")
    return LLMResponseError(f"HTTP {status_code} 未预期响应：{body[:200]}")


# ============================================================
# OpenAI 兼容 HTTP 客户端
# ============================================================


class OpenAICompatibleClient:
    """OpenAI Chat Completions 兼容 HTTP 客户端。

    实现 backend.extractors.LLMClient Protocol。

    用法：
        # 通过环境变量配置
        client = OpenAICompatibleClient.from_env()

        # 或直接构造
        client = OpenAICompatibleClient(
            base_url="https://open.bigmodel.cn/api/paas/v4",
            api_key="your-key",
            model="glm-5.2",
        )

        baseline = DirectLLMBaseline(client=client, model_identifier="glm-5.2")
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url 不能为空")
        if not api_key:
            raise ValueError("api_key 不能为空")
        if not model:
            raise ValueError("model 不能为空")

        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._extra_headers = extra_headers or {}

        # transport 用于测试注入 httpx.MockTransport
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout_seconds,
            transport=transport,
        )

        logger.info(
            "OpenAICompatibleClient init base_url={} model={} timeout={}s",
            self._base_url,
            self._model,
            self._timeout,
        )

    @classmethod
    def from_env(cls) -> OpenAICompatibleClient:
        """从环境变量构造客户端。

        环境变量：
        - LLM_BASE_URL: API 基地址（默认智谱 GLM）
        - LLM_API_KEY: API 密钥（必须）
        - LLM_MODEL: 模型名（默认 glm-5.2）
        - LLM_TIMEOUT_SECONDS: 超时秒数（默认 60）
        """
        api_key = os.environ.get("LLM_API_KEY", "")
        if not api_key:
            raise ValueError(
                "环境变量 LLM_API_KEY 未设置，无法构造 OpenAICompatibleClient"
            )
        return cls(
            base_url=os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL),
            api_key=api_key,
            model=os.environ.get("LLM_MODEL", DEFAULT_MODEL),
            timeout_seconds=float(
                os.environ.get("LLM_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
            ),
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def base_url(self) -> str:
        return self._base_url

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """调用 Chat Completions API。

        Raises:
            LLMTimeoutError: 请求超时
            LLMRateLimitError: 限流
            LLMAuthError: 鉴权失败
            LLMServerError: 服务端错误
            LLMResponseError: 响应解析失败
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            **self._extra_headers,
        }

        started = time.monotonic()
        try:
            response = await self._client.post(
                "/chat/completions",
                json=payload,
                headers=headers,
            )
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(f"请求超时（{self._timeout}s）：{exc}") from exc
        except httpx.HTTPError as exc:
            raise LLMClientError(f"网络错误：{type(exc).__name__}: {exc}") from exc

        latency_ms = int((time.monotonic() - started) * 1000)

        if response.status_code != 200:
            raise _classify_http_error(
                response.status_code, response.text
            )

        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise LLMResponseError(
                f"响应非合法 JSON：{exc} body={response.text[:200]}"
            ) from exc

        # 解析 OpenAI 标准响应结构
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError(
                f"响应结构异常，缺少 choices[0].message.content：{exc} body={response.text[:200]}"
            ) from exc

        if not isinstance(content, str):
            raise LLMResponseError(
                f"content 字段类型异常：{type(content).__name__} body={response.text[:200]}"
            )

        # Token 统计：优先用 API 返回的 usage
        usage = data.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")

        # 缺失时按字符数估算（粗略，仅供 fallback）
        if prompt_tokens is None:
            prompt_tokens = (len(system_prompt) + len(user_prompt)) // 4
        if completion_tokens is None:
            completion_tokens = len(content) // 4
        if total_tokens is None:
            total_tokens = prompt_tokens + completion_tokens

        logger.info(
            "llm_complete model={} latency_ms={} tokens={}/{}/{} content_len={}",
            self._model,
            latency_ms,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            len(content),
        )

        return LLMResponse(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
        )

    async def close(self) -> None:
        """关闭底层 HTTP 客户端，释放连接池资源。"""
        await self._client.aclose()
        logger.info("OpenAICompatibleClient closed base_url={}", self._base_url)

    async def __aenter__(self) -> OpenAICompatibleClient:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT_SECONDS",
    "LLMAuthError",
    "LLMClientError",
    "LLMRateLimitError",
    "LLMResponseError",
    "LLMServerError",
    "LLMTimeoutError",
    "OpenAICompatibleClient",
]
