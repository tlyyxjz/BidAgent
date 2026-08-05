"""API Key HMAC-SHA256 摘要（从 credentials 拆分）。

- `generate_api_key` 使用 `secrets.token_urlsafe(32)` 生成 256 位熵的 URL 安全
  随机字符串。
- `hash_api_key` 以 SECRET_KEY 环境变量为 HMAC 密钥，对 API Key 计算 SHA256
  摘要，返回 64 字符 hex 字符串（与原 SHA256 hexdigest 长度一致，DB schema 无需变更）。
- `verify_api_key` 重新计算传入 key 的 HMAC 摘要，并与存储摘要进行常量时间比对。
- HMAC 引入服务端密钥，即使数据库泄露，攻击者也无法离线爆破出原始 API Key。

设计要点：
- 所有函数都不记录明文 API Key、密钥字节串到日志（遵循 §13.1
  "日志不得记录凭证" 要求）。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets


# ========== API Key（HMAC-SHA256）==========


def generate_api_key() -> str:
    """生成高熵随机 API Key。

    使用 `secrets.token_urlsafe(32)` 生成 43 字符的 URL 安全 base64 字符串，
    对应 256 位熵，满足 v4.1 §13.1 凭证安全要求。

    Returns:
        str: URL 安全的随机 API Key 字符串（43 字符，字符集为 A-Z a-z 0-9 - _）。
    """
    return secrets.token_urlsafe(32)


def _get_server_secret() -> bytes:
    """获取服务端 HMAC 密钥（来自 SECRET_KEY 环境变量）。

    Returns:
        bytes: SECRET_KEY 的 UTF-8 字节串。

    Raises:
        RuntimeError: SECRET_KEY 未设置时抛出，提示生成方法。
    """
    secret = os.environ.get("SECRET_KEY", "")
    if not secret:
        raise RuntimeError(
            "SECRET_KEY 环境变量未设置；无法计算 HMAC 摘要。"
            ' 请用 python -c "import secrets; print(secrets.token_hex(32))" 生成'
            " 并配置到环境变量或 .env 文件。"
        )
    return secret.encode("utf-8")


def hash_api_key(key: str) -> str:
    """返回基于服务端密钥的 HMAC-SHA256 摘要。

    使用 SECRET_KEY 环境变量作为 HMAC 密钥，对 API Key 计算 SHA256 摘要。
    相比纯 SHA256，HMAC 引入服务端密钥使得即使数据库泄露，攻击者也无法离线
    爆破出原始 API Key（需要同时获取 SECRET_KEY 才能构造有效摘要）。

    Args:
        key: 待哈希的 API Key 明文。

    Returns:
        str: 64 字符的 hex 摘要字符串（与 SHA256 hexdigest 长度一致，
            DB schema 无需变更）。
    """
    return hmac.new(
        _get_server_secret(),
        key.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_api_key(key: str, stored_hash: str) -> bool:
    """使用 `hmac.compare_digest` 常量时间比对 API Key 与存储摘要。

    重新计算传入 key 的 HMAC 摘要，并与 stored_hash 进行常量时间比对，
    防止通过响应时间差异反推明文 key（时序攻击）。

    Args:
        key: 待验证的 API Key 明文。
        stored_hash: 数据库中存储的 HMAC 摘要（由 `hash_api_key` 生成）。

    Returns:
        bool: 匹配返回 True，否则 False。
    """
    expected = hash_api_key(key)
    return hmac.compare_digest(expected, stored_hash)
