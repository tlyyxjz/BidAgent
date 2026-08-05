"""Cookie / 会话令牌 AES-GCM-256 认证加密（从 credentials 拆分）。

- `aes_gcm_encrypt` 使用 AES-256-GCM 对明文进行认证加密，返回 base64 编码的
  `nonce || ciphertext || tag`（nonce 每次随机生成 12 字节，保证唯一性）。
- `aes_gcm_decrypt` 解密并验证完整性，任何篡改都会抛出 `InvalidTag`。
- 密钥来自 SECRET_KEY（32 字节 hex 解码），与 HMAC 共用服务端密钥。
- `generate_session_token` / `verify_session_token` 是语义化别名。

设计要点：
- 所有函数都不记录明文令牌、密钥字节串到日志（遵循 §13.1
  "日志不得记录凭证" 要求）。
- AES-GCM nonce 长度固定 12 字节（96 bit），是 NIST SP 800-38D 推荐值，
  随机生成且不重用，无需额外计数器。
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


# ========== AES-GCM 参数 ==========
_AES_KEY_LEN = 32  # bytes（AES-256）
_AES_NONCE_LEN = 12  # bytes（NIST SP 800-38D 推荐 96 bit）


# ========== Cookie / 会话令牌（AES-GCM-256）==========


def _get_aes_key() -> bytes:
    """从 SECRET_KEY 派生 32 字节 AES-256 密钥。

    SECRET_KEY 必须是 64 字符 hex 字符串（编码 32 字节），由 `config.py` 的
    `validate_secret_key` 强制校验。本函数将其解码为原始 32 字节作为 AES-256
    密钥。

    Returns:
        bytes: 32 字节 AES-256 密钥。

    Raises:
        RuntimeError: SECRET_KEY 未设置或长度非法。
    """
    secret = os.environ.get("SECRET_KEY", "")
    if not secret:
        raise RuntimeError(
            "SECRET_KEY 环境变量未设置；无法派生 AES-256 密钥。"
            ' 请用 python -c "import secrets; print(secrets.token_hex(32))" 生成'
            " 并配置到环境变量或 .env 文件。"
        )
    try:
        key_bytes = bytes.fromhex(secret)
    except ValueError as exc:
        raise RuntimeError(
            f"SECRET_KEY 不是合法的 hex 字符串: {exc}"
        ) from exc
    if len(key_bytes) != _AES_KEY_LEN:
        raise RuntimeError(
            f"SECRET_KEY 必须编码 32 字节（当前 {len(key_bytes)} 字节），"
            "AES-256 要求 32 字节密钥。"
        )
    return key_bytes


def aes_gcm_encrypt(plaintext: str, *, aad: bytes | None = None) -> str:
    """使用 AES-256-GCM 对明文进行认证加密。

    流程：
    1. 随机生成 12 字节 nonce（每次调用都不同，保证 nonce 唯一性）。
    2. 用 SECRET_KEY 派生的 AES-256 密钥加密明文，得到 `ciphertext || tag`。
    3. 拼接 `nonce || ciphertext || tag`，base64 编码后返回。

    返回的字符串可直接作为 Cookie 值或会话令牌。AAD（Additional Authenticated
    Data）可选，用于绑定上下文（如用户 ID、IP 段），解密时必须传入相同 AAD。

    Args:
        plaintext: 待加密的明文字符串（UTF-8 编码）。
        aad: 可选的附加认证数据，会纳入完整性校验但不被加密。

    Returns:
        str: base64 编码的 `nonce(12B) || ciphertext || tag(16B)`。

    Raises:
        ValueError: plaintext 为空或非字符串。
        RuntimeError: SECRET_KEY 未配置。
    """
    if not isinstance(plaintext, str):
        raise ValueError("plaintext 必须是字符串")
    if not plaintext:
        raise ValueError("plaintext 不能为空")
    key = _get_aes_key()
    aesgcm = AESGCM(key)
    # 每次加密生成随机 nonce，保证唯一性（§13.1 要求）
    nonce = os.urandom(_AES_NONCE_LEN)
    # AESGCM.encrypt 返回 ciphertext || tag（tag 在末尾 16 字节）
    ct_and_tag = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), aad)
    # 拼接 nonce + ciphertext + tag，base64 编码
    blob = nonce + ct_and_tag
    return base64.urlsafe_b64encode(blob).decode("ascii")


def aes_gcm_decrypt(token: str, *, aad: bytes | None = None) -> str:
    """解密由 `aes_gcm_encrypt` 生成的 base64 令牌。

    流程：
    1. base64 解码得到 `nonce || ciphertext || tag`。
    2. 拆出前 12 字节 nonce，剩余部分为 `ciphertext || tag`。
    3. 用相同密钥与 AAD 解密并验证 tag，任何篡改都会抛出 `InvalidTag`。

    Args:
        token: `aes_gcm_encrypt` 返回的 base64 字符串。
        aad: 加密时使用的相同 AAD，不匹配会解密失败。

    Returns:
        str: 解密后的明文字符串。

    Raises:
        ValueError: token 格式非法（空、非字符串、base64 解码失败、长度不足）。
        RuntimeError: SECRET_KEY 未配置。
        cryptography.exceptions.InvalidTag: 令牌被篡改或 AAD 不匹配。
    """
    if not isinstance(token, str):
        raise ValueError("token 必须是字符串")
    if not token:
        raise ValueError("token 不能为空")
    key = _get_aes_key()
    try:
        blob = base64.urlsafe_b64decode(token.encode("ascii"))
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError(f"token 不是合法的 base64 字符串: {exc}") from exc
    if len(blob) < _AES_NONCE_LEN + 16:  # 至少 nonce(12) + tag(16)
        raise ValueError(
            f"token 长度不足：需要至少 {_AES_NONCE_LEN + 16} 字节，"
            f"实际 {len(blob)} 字节"
        )
    nonce = blob[:_AES_NONCE_LEN]
    ct_and_tag = blob[_AES_NONCE_LEN:]
    aesgcm = AESGCM(key)
    plaintext_bytes = aesgcm.decrypt(nonce, ct_and_tag, aad)
    return plaintext_bytes.decode("utf-8")


def generate_session_token(payload: str, *, aad: bytes | None = None) -> str:
    """生成会话令牌（语义化别名，内部调用 `aes_gcm_encrypt`）。

    语义上用于"签发会话 Cookie"，与 `aes_gcm_encrypt` 完全等价，便于调用方
    表达意图。例如：

        token = generate_session_token(user_id)
        # 写入 Set-Cookie: session=<token>

    Args:
        payload: 会话载荷明文（如 user_id 或 JSON 序列化的会话数据）。
        aad: 可选的附加认证数据。

    Returns:
        str: base64 编码的加密令牌。
    """
    return aes_gcm_encrypt(payload, aad=aad)


def verify_session_token(token: str, *, aad: bytes | None = None) -> str:
    """验证并解密会话令牌（语义化别名，内部调用 `aes_gcm_decrypt`）。

    Args:
        token: `generate_session_token` 返回的 base64 令牌。
        aad: 签发时使用的相同 AAD。

    Returns:
        str: 解密后的会话载荷明文。
    """
    return aes_gcm_decrypt(token, aad=aad)
