"""config 密钥脱敏测试。"""
from app.config import _mask_secrets


def test_mask_sk_key():
    """sk- 开头的 API key 应被脱敏。"""
    assert "sk-abc" not in _mask_secrets("error: sk-abc123def456ghi789jkl")


def test_mask_github_token():
    """ghp_ 开头的 token 应被脱敏。"""
    assert "ghp_" not in _mask_secrets("error: ghp_abcdefghijklmnop123456")


def test_mask_aws_key():
    """AKIA 开头的 AWS key 应被脱敏。"""
    assert "AKIA" not in _mask_secrets("error: AKIAIOSFODNN7EXAMPLE")


def test_keep_normal_text():
    """普通文本不受影响。"""
    text = "配置加载失败: SECRET_KEY 必须是 64 字符 hex"
    assert _mask_secrets(text) == text
