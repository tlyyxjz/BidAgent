"""多模型 provider 层单元测试（2026-08-06 多模型支持）。"""
import pytest

from app.config import settings
from app.llm.provider import (
    build_chat_payload,
    chat_endpoint,
    parse_json_lenient,
    resolve_provider,
)


def _clear_env(monkeypatch):
    """把全部 provider key/覆盖项清空，避免测试间串扰。"""
    for attr in (
        "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "ZHIPU_API_KEY", "OPENAI_API_KEY",
        "LLM_API_KEY", "LLM_BASE_URL", "LLM_EXTRACTION_MODEL", "LLM_JSON_MODE",
        "OLLAMA_BASE_URL", "VLLM_BASE_URL",
    ):
        if hasattr(settings, attr):
            monkeypatch.setattr(settings, attr, "")


# ========== resolve_provider ==========

def test_resolve_deepseek_default(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "sk-test")
    p = resolve_provider("extraction")
    assert p.name == "deepseek"
    assert p.api_key == "sk-test"
    assert p.base_url == "https://api.deepseek.com"
    assert p.supports_json_mode is True


def test_resolve_zhipu(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "zhipu")
    monkeypatch.setattr(settings, "ZHIPU_API_KEY", "zhipu-test")
    p = resolve_provider("extraction")
    assert p.name == "zhipu"
    assert "bigmodel.cn" in p.base_url
    # GLM 默认关 json_object（旧版不稳支持），payload 不应含 response_format
    assert p.supports_json_mode is False


def test_resolve_missing_key_raises(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "zhipu")
    with pytest.raises(RuntimeError):
        resolve_provider("extraction")


def test_explicit_override(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-override")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://my-proxy.local/v1")
    p = resolve_provider("extraction")
    assert p.api_key == "sk-override"
    assert p.base_url == "https://my-proxy.local/v1"


def test_extraction_model_override(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "LLM_EXTRACTION_MODEL", "deepseek-reasoner")
    p = resolve_provider("extraction")
    assert p.model == "deepseek-reasoner"


def test_json_mode_env_override(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "zhipu")
    monkeypatch.setattr(settings, "ZHIPU_API_KEY", "zhipu-test")
    monkeypatch.setattr(settings, "LLM_JSON_MODE", "true")
    p = resolve_provider("extraction")
    assert p.supports_json_mode is True


# ========== 本地自部署 provider（私有化交付形态，免 API Key） ==========

def test_resolve_ollama_no_key_ok(monkeypatch):
    """Ollama 本地端点：无需任何 API key 即可解析（私有化部署核心契约）。"""
    _clear_env(monkeypatch)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(settings, "LLM_MODEL", "")
    p = resolve_provider("extraction")
    assert p.name == "ollama"
    assert p.api_key == ""
    assert p.base_url == "http://localhost:11434/v1"
    assert p.model == "qwen2.5:7b"  # provider 默认模型


def test_resolve_vllm_no_key_ok(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "vllm")
    monkeypatch.setattr(settings, "LLM_MODEL", "")
    p = resolve_provider("extraction")
    assert p.name == "vllm"
    assert p.base_url == "http://localhost:8080/v1"
    assert p.supports_json_mode is True


def test_local_model_and_url_override(monkeypatch):
    """客户换模型（千问 32B）+ 换端口：只改配置即可生效。"""
    _clear_env(monkeypatch)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(settings, "LLM_MODEL", "qwen2.5:32b")
    monkeypatch.setattr(settings, "OLLAMA_BASE_URL", "http://gpu-node:11434/v1")
    p = resolve_provider("extraction")
    assert p.model == "qwen2.5:32b"
    assert p.base_url == "http://gpu-node:11434/v1"


def test_local_provider_json_mode_can_disable(monkeypatch):
    """量化小模型不稳支持 json_object 时，可用 LLM_JSON_MODE=false 强制关。"""
    _clear_env(monkeypatch)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(settings, "LLM_JSON_MODE", "false")
    p = resolve_provider("extraction")
    assert p.supports_json_mode is False


# ========== build_chat_payload / chat_endpoint ==========

def test_payload_json_mode():
    from app.llm.provider import ProviderInfo

    info = ProviderInfo("x", "k", "https://api.x.com", "m", True)
    payload = build_chat_payload(info, "sys", "user")
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["messages"][0]["role"] == "system"

    info_no = ProviderInfo("x", "k", "https://api.x.com", "m", False)
    payload2 = build_chat_payload(info_no, "sys", "user")
    assert "response_format" not in payload2


def test_payload_local_no_key_header():
    """本地 provider（空 key）payload 正常构造，不因空 key 报错。"""
    from app.llm.provider import ProviderInfo

    info = ProviderInfo("ollama", "", "http://localhost:11434/v1", "qwen2.5:7b", True)
    payload = build_chat_payload(info, "sys", "user", max_tokens=500)
    assert payload["model"] == "qwen2.5:7b"
    assert payload["max_tokens"] == 500


def test_chat_endpoint_v1_suffix():
    from app.llm.provider import ProviderInfo

    info = ProviderInfo("x", "k", "https://api.x.com", "m", True)
    assert chat_endpoint(info) == "https://api.x.com/v1/chat/completions"
    info2 = ProviderInfo("x", "k", "https://api.x.com/v1", "m", True)
    assert chat_endpoint(info2) == "https://api.x.com/v1/chat/completions"


def test_chat_endpoint_ollama_local():
    from app.llm.provider import ProviderInfo

    info = ProviderInfo("ollama", "", "http://localhost:11434/v1", "qwen2.5:7b", True)
    assert chat_endpoint(info) == "http://localhost:11434/v1/chat/completions"


# ========== parse_json_lenient ==========

def test_parse_plain_json():
    assert parse_json_lenient('{"a": 1}') == {"a": 1}


def test_parse_markdown_fenced():
    assert parse_json_lenient('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_with_surrounding_text():
    text = '好的，以下是结果：\n{"fields": []}\n以上。'
    assert parse_json_lenient(text) == {"fields": []}


def test_parse_trailing_comma():
    assert parse_json_lenient('{"a": 1, "b": [2, 3,],}') == {"a": 1, "b": [2, 3]}


def test_parse_invalid_raises():
    with pytest.raises(ValueError):
        parse_json_lenient("完全不是 JSON")
    with pytest.raises(ValueError):
        parse_json_lenient("")
