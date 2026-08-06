"""多模型 Provider 解析层（多 LLM 可切换）。

v4.1 工程规范：所有 LLM 调用走 OpenAI 兼容协议（/v1/chat/completions）。
支持通过 LLM_PROVIDER 环境变量切换供应商：

- deepseek（默认）：DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL
- dashscope（通义千问）：DASHSCOPE_API_KEY / DASHSCOPE_BASE_URL
- zhipu（智谱 GLM）：ZHIPU_API_KEY / ZHIPU_BASE_URL
- openai：OPENAI_API_KEY / OPENAI_BASE_URL

覆盖优先级：LLM_BASE_URL + LLM_API_KEY 显式配置 > provider 专属配置。
抽取任务可用 LLM_EXTRACTION_MODEL 单独指定模型（默认回落 LLM_MODEL）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.config import settings


@dataclass(frozen=True)
class ProviderInfo:
    """已解析的 LLM 供应商配置。"""

    name: str
    api_key: str
    base_url: str
    model: str
    supports_json_mode: bool


# 各 provider 默认模型与 json_object response_format 支持情况
_PROVIDER_DEFAULTS: dict[str, tuple[str, bool]] = {
    "deepseek": ("deepseek-chat", True),
    "dashscope": ("qwen-plus", True),
    "zhipu": ("glm-4-flash", False),  # GLM 旧版不稳支持 json_object，默认关
    "openai": ("gpt-4o-mini", True),
}

_BASE_URL_DEFAULTS: dict[str, str] = {
    "deepseek": "https://api.deepseek.com",
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "openai": "https://api.openai.com/v1",
}


def resolve_provider(purpose: str = "extraction") -> ProviderInfo:
    """解析当前生效的 LLM 供应商配置。

    Args:
        purpose: "extraction"（字段抽取）或 "intent"（意图解析）。
            extraction 优先使用 LLM_EXTRACTION_MODEL 覆盖模型。

    Returns:
        ProviderInfo

    Raises:
        RuntimeError: provider 未配置 API Key
    """
    name = (settings.LLM_PROVIDER or "deepseek").strip().lower()
    default_model, default_json = _PROVIDER_DEFAULTS.get(
        name, ("deepseek-chat", True)
    )

    # provider 专属 key/url
    api_key = getattr(settings, f"{name.upper()}_API_KEY", "") or ""
    base_url = getattr(settings, f"{name.upper()}_BASE_URL", "") or _BASE_URL_DEFAULTS.get(
        name, _BASE_URL_DEFAULTS["deepseek"]
    )

    # 显式覆盖优先
    override_key = getattr(settings, "LLM_API_KEY", "")
    override_url = getattr(settings, "LLM_BASE_URL", "")
    if override_key:
        api_key = override_key
    if override_url:
        base_url = override_url

    if not api_key:
        raise RuntimeError(
            f"LLM provider '{name}' API key not configured "
            f"(set {name.upper()}_API_KEY in .env)"
        )

    # 模型选择：purpose 覆盖 > LLM_MODEL > provider 默认
    model = settings.LLM_MODEL or default_model
    if purpose == "extraction":
        model = getattr(settings, "LLM_EXTRACTION_MODEL", "") or model

    # json mode：LLM_JSON_MODE 显式配置 > provider 默认
    json_mode_env = getattr(settings, "LLM_JSON_MODE", "")
    if json_mode_env.strip().lower() in ("1", "true", "yes"):
        supports_json = True
    elif json_mode_env.strip().lower() in ("0", "false", "no"):
        supports_json = False
    else:
        supports_json = default_json

    return ProviderInfo(
        name=name,
        api_key=api_key,
        base_url=base_url.rstrip("/"),
        model=model,
        supports_json_mode=supports_json,
    )


def build_chat_payload(
    provider: ProviderInfo,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.1,
    max_tokens: int = 8000,
) -> dict:
    """构造 OpenAI 兼容 chat/completions 请求体。"""
    payload = {
        "model": provider.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if provider.supports_json_mode:
        payload["response_format"] = {"type": "json_object"}
    return payload


def chat_endpoint(provider: ProviderInfo) -> str:
    """返回 chat/completions 完整 URL（base_url 已含 /v1 时不重复拼接）。"""
    base = provider.base_url
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def extract_content_and_usage(data: dict) -> tuple[str, int]:
    """从响应中提取正文与 total_tokens。"""
    content = data["choices"][0]["message"]["content"]
    total_tokens = data.get("usage", {}).get("total_tokens", 0)
    return content, total_tokens


# ========== JSON 宽松解析（抗偶发格式破损） ==========

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


def parse_json_lenient(content: str) -> dict:
    """宽松解析 LLM 返回的 JSON 文本。

    依次尝试：
    1. 直接 json.loads
    2. 去除 markdown 围栏后解析
    3. 截取第一个 { 到最后一个 } 的子串解析
    4. 修复常见尾逗号后解析

    Raises:
        ValueError: 全部尝试失败
    """
    if not content or not content.strip():
        raise ValueError("empty LLM response")

    text = content.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 去围栏
    unfenced = _FENCE_RE.sub("", text).strip()
    if unfenced != text:
        try:
            return json.loads(unfenced)
        except json.JSONDecodeError:
            text = unfenced

    # 截取最外层花括号
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidate = text[start : end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            # 尾逗号修复：,} / ,]
            fixed = re.sub(r",\s*([}\]])", r"\1", candidate)
            try:
                return json.loads(fixed)
            except json.JSONDecodeError as exc:
                raise ValueError(f"unparseable LLM JSON: {exc}") from exc

    raise ValueError(f"no JSON object found in LLM response: {text[:100]}")
