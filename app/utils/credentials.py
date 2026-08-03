"""API Key 与凭证安全工具（v4.1 §13.1 升级）。

三层凭证安全实现：

1. API Key（HMAC-SHA256 摘要）
   - `generate_api_key` 使用 `secrets.token_urlsafe(32)` 生成 256 位熵的 URL 安全
     随机字符串。
   - `hash_api_key` 以 SECRET_KEY 环境变量为 HMAC 密钥，对 API Key 计算 SHA256
     摘要，返回 64 字符 hex 字符串（与原 SHA256 hexdigest 长度一致，DB schema 无需变更）。
   - `verify_api_key` 重新计算传入 key 的 HMAC 摘要，并与存储摘要进行常量时间比对。
   - HMAC 引入服务端密钥，即使数据库泄露，攻击者也无法离线爆破出原始 API Key。

2. 用户密码（Argon2id）
   - `hash_password` 使用 Argon2id（OWASP 推荐参数）对密码进行加盐哈希，返回
     PHC 格式字符串（含盐、参数、哈希值，可直接存数据库）。
   - `verify_password` 使用 `argon2.PasswordVerifier` 常量时间比对。
   - Argon2id 是抗 GPU/ASIC 攻击的现代密码哈希算法，2015 年 Password Hashing
     Competition 冠军，OWASP 首选推荐。

3. Cookie / 会话令牌（AES-GCM-256）
   - `aes_gcm_encrypt` 使用 AES-256-GCM 对明文进行认证加密，返回 base64 编码的
     `nonce || ciphertext || tag`（nonce 每次随机生成 12 字节，保证唯一性）。
   - `aes_gcm_decrypt` 解密并验证完整性，任何篡改都会抛出 `InvalidTag`。
   - 密钥来自 SECRET_KEY（32 字节 hex 解码），与 HMAC 共用服务端密钥。

设计要点：
- 所有函数都不记录明文密码、明文 API Key、密钥字节串到日志（遵循 §13.1
  "日志不得记录凭证" 要求）。
- Argon2id 参数采用 OWASP 2023 推荐值：memory_cost=19456 KiB、time_cost=2、
  parallelism=1，对应 approximately 47 MiB memory、2 iterations。
- AES-GCM nonce 长度固定 12 字节（96 bit），是 NIST SP 800-38D 推荐值，
  随机生成且不重用，无需额外计数器。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import (
    Argon2Error,
    InvalidHash,
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


# ========== Argon2id 参数（OWASP 2023 推荐）==========
# memory_cost=19456 KiB（19 MiB），time_cost=2，parallelism=1
# 对应 OWASP "Argon2id" 推荐基线：t=2, m=19456, p=1
_ARGON2_TIME_COST = 2
_ARGON2_MEMORY_COST = 19456  # KiB
_ARGON2_PARALLELISM = 1
_ARGON2_HASH_LEN = 32  # bytes
_ARGON2_SALT_LEN = 16  # bytes

# ========== AES-GCM 参数 ==========
_AES_KEY_LEN = 32  # bytes（AES-256）
_AES_NONCE_LEN = 12  # bytes（NIST SP 800-38D 推荐 96 bit）

# 模块级 PasswordHasher 单例（避免每次调用重复构造）
_password_hasher = PasswordHasher(
    time_cost=_ARGON2_TIME_COST,
    memory_cost=_ARGON2_MEMORY_COST,
    parallelism=_ARGON2_PARALLELISM,
    hash_len=_ARGON2_HASH_LEN,
    salt_len=_ARGON2_SALT_LEN,
)


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


# ========== 用户密码（Argon2id）==========


def hash_password(password: str) -> str:
    """使用 Argon2id 对密码进行加盐哈希，返回 PHC 格式字符串。

    Argon2id 是 Argon2i（抗侧信道）和 Argon2d（抗 GPU）的混合变体，OWASP
    首选推荐。参数采用 OWASP 2023 基线：t=2, m=19456, p=1。

    返回的 PHC 字符串自包含盐、参数、哈希值，可直接存数据库的 password_hash
    字段，无需单独维护盐列。

    Args:
        password: 待哈希的用户密码明文。

    Returns:
        str: PHC 格式字符串，形如
            `$argon2id$v=19$m=19456,t=2,p=1$<salt_b64>$<hash_b64>`。

    Raises:
        ValueError: password 为空或非字符串。
    """
    if not isinstance(password, str):
        raise ValueError("password 必须是字符串")
    if not password:
        raise ValueError("password 不能为空")
    # 注意：不记录 password 或返回的 hash 到日志（§13.1 要求）
    return _password_hasher.hash(password)


def verify_password(password: str, phc_hash: str) -> bool:
    """使用 Argon2id 验证密码与存储的 PHC 哈希是否匹配。

    采用常量时间比对（argon2-cffi 内部实现），抵抗时序攻击。无论匹配与否
    都不会抛出异常，调用方根据返回值判断；只有哈希格式非法时才抛出异常。

    Args:
        password: 待验证的用户密码明文。
        phc_hash: 数据库中存储的 PHC 格式哈希（由 `hash_password` 生成）。

    Returns:
        bool: 匹配返回 True，否则 False。

    Raises:
        ValueError: phc_hash 格式非法（不是合法的 Argon2 PHC 字符串）。
    """
    if not isinstance(password, str) or not isinstance(phc_hash, str):
        raise ValueError("password 和 phc_hash 必须是字符串")
    if not phc_hash:
        raise ValueError("phc_hash 不能为空")
    try:
        return _password_hasher.verify(phc_hash, password)
    except VerifyMismatchError:
        # 密码不匹配，返回 False（不是异常）
        return False
    except (InvalidHashError, InvalidHash, VerificationError):
        # 哈希格式非法：完全非 argon2 格式抛 InvalidHashError；
        # $argon2id$ 前缀但内容非法（如 '$argon2id$malformed'）抛
        # VerificationError 且 __cause__ 为 None。两者都是配置错误，
        # 抛 ValueError 让调用方感知。
        # 注意：VerifyMismatchError 是 VerificationError 子类，但已被上方
        # except VerifyMismatchError 捕获，不会落到这里。
        raise ValueError("phc_hash 不是合法的 Argon2 PHC 字符串")
    except Argon2Error:
        # 其他底层错误，保守返回 False，避免异常导致服务中断
        return False


def needs_rehash(phc_hash: str) -> bool:
    """检测存储的 PHC 哈希是否需要重新哈希（参数已过时）。

    当 Argon2id 参数升级（如提高 memory_cost）后，老密码在登录时可通过
    `verify_password` 验证通过，然后用 `hash_password` 重新哈希存储。
    本函数判断是否需要触发此流程。

    Args:
        phc_hash: 数据库中存储的 PHC 格式哈希。

    Returns:
        bool: 参数与当前 `hash_password` 不一致时返回 True。
    """
    if not phc_hash:
        return False
    try:
        return _password_hasher.check_needs_rehash(phc_hash)
    except (InvalidHashError, InvalidHash, Argon2Error):
        # 哈希格式非法或不是 Argon2id，视为需要重新哈希
        return True


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
