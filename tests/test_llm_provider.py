"""多模型 provider 层单元测试（2026-08-06 多模型支持）。"""
import pytest

from app.config import settings
from app.llm.provider import (
    build_chat_payload,
    chat_endpoint,
    parse_json_lenient,
    resolve_provider,
)


# ========== resolve_provider ==========

def test_resolve_deepseek_default(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "")
    monkeypatch.setattr(settings, "LLM_EXTRACTION_MODEL", "")
    monkeypatch.setattr(settings, "LLM_JSON_MODE", "")
    p = resolve_provider("extraction")
    assert p.name == "deepseek"
    assert p.api_key == "sk-test"
    assert p.base_url == "https://api.deepseek.com"
    assert p.supports_json_mode is True


def test_resolve_zhipu(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "zhipu")
    monkeypatch.setattr(settings, "ZHIPU_API_KEY", "zhipu-test")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "")
    monkeypatch.setattr(settings, "LLM_EXTRACTION_MODEL", "")
    monkeypatch.setattr(settings, "LLM_JSON_MODE", "")
    p = resolve_provider("extraction")
    assert p.name == "zhipu"
    assert "bigmodel.cn" in p.base_url
    # GLM 默认关 json_object（旧版不稳支持），payload 不应含 response_format
    assert p.supports_json_mode is False


def test_resolve_missing_key_raises(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "zhipu")
    monkeypatch.setattr(settings, "ZHIPU_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    with pytest.raises(RuntimeError):
        resolve_provider("extraction")


def test_explicit_override(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-override")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://my-proxy.local/v1")
    monkeypatch.setattr(settings, "LLM_EXTRACTION_MODEL", "")
    p = resolve_provider("extraction")
    assert p.api_key == "sk-override"
    assert p.base_url == "https://my-proxy.local/v1"


def test_extraction_model_override(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "")
    monkeypatch.setattr(settings, "LLM_EXTRACTION_MODEL", "deepseek-reasoner")
    p = resolve_provider("extraction")
    assert p.model == "deepseek-reasoner"


def test_json_mode_env_override(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "zhipu")
    monkeypatch.setattr(settings, "ZHIPU_API_KEY", "zhipu-test")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "")
    monkeypatch.setattr(settings, "LLM_JSON_MODE", "true")
    p = resolve_provider("extraction")
    assert p.supports_json_mode is True


# ========== build_chat_payload / chat_endpoint ==========

def test_payload_json_mode():
    p = resolve_provider.__wrapped__ if False else None  # noqa: F841
    from app.llm.provider import ProviderInfo

    info = ProviderInfo("x", "k", "https://api.x.com", "m", True)
    payload = build_chat_payload(info, "sys", "user")
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["messages"][0]["role"] == "system"

    info_no = ProviderInfo("x", "k", "https://api.x.com", "m", False)
    payload2 = build_chat_payload(info_no, "sys", "user")
    assert "response_format" not in payload2


def test_chat_endpoint_v1_suffix():
    from app.llm.provider import ProviderInfo

    info = ProviderInfo("x", "k", "https://api.x.com", "m", True)
    assert chat_endpoint(info) == "https://api.x.com/v1/chat/completions"
    info2 = ProviderInfo("x", "k", "https://api.x.com/v1", "m", True)
    assert chat_endpoint(info2) == "https://api.x.com/v1/chat/completions"


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
