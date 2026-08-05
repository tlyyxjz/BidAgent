"""API Key 与凭证安全工具（v4.1 §13.1 升级）。

三层凭证安全实现：

1. API Key（HMAC-SHA256 摘要）— 实现见 ``app.utils.api_key``
   - ``generate_api_key`` 使用 ``secrets.token_urlsafe(32)`` 生成 256 位熵的 URL 安全
     随机字符串。
   - ``hash_api_key`` 以 SECRET_KEY 环境变量为 HMAC 密钥，对 API Key 计算 SHA256
     摘要，返回 64 字符 hex 字符串（与原 SHA256 hexdigest 长度一致，DB schema 无需变更）。
   - ``verify_api_key`` 重新计算传入 key 的 HMAC 摘要，并与存储摘要进行常量时间比对。
   - HMAC 引入服务端密钥，即使数据库泄露，攻击者也无法离线爆破出原始 API Key。

2. 用户密码（Argon2id）— 实现保留在本模块
   - ``hash_password`` 使用 Argon2id（OWASP 推荐参数）对密码进行加盐哈希，返回
     PHC 格式字符串（含盐、参数、哈希值，可直接存数据库）。
   - ``verify_password`` 使用 ``argon2.PasswordVerifier`` 常量时间比对。
   - Argon2id 是抗 GPU/ASIC 攻击的现代密码哈希算法，2015 年 Password Hashing
     Competition 冠军，OWASP 首选推荐。

3. Cookie / 会话令牌（AES-GCM-256）— 实现见 ``app.utils.aes_crypto``
   - ``aes_gcm_encrypt`` 使用 AES-256-GCM 对明文进行认证加密，返回 base64 编码的
     ``nonce || ciphertext || tag``（nonce 每次随机生成 12 字节，保证唯一性）。
   - ``aes_gcm_decrypt`` 解密并验证完整性，任何篡改都会抛出 ``InvalidTag``。
   - 密钥来自 SECRET_KEY（32 字节 hex 解码），与 HMAC 共用服务端密钥。

本模块从 ``api_key`` / ``aes_crypto`` 子模块 re-export 函数与常量，以保持
``from app.utils.credentials import ...`` 接口不变。

设计要点：
- 所有函数都不记录明文密码、明文 API Key、密钥字节串到日志（遵循 §13.1
  "日志不得记录凭证" 要求）。
- Argon2id 参数采用 OWASP 2023 推荐值：memory_cost=19456 KiB、time_cost=2、
  parallelism=1，对应 approximately 47 MiB memory、2 iterations。
- AES-GCM nonce 长度固定 12 字节（96 bit），是 NIST SP 800-38D 推荐值，
  随机生成且不重用，无需额外计数器。
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import (
    Argon2Error,
    InvalidHash,
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)

# Re-export API Key（HMAC-SHA256）— 实现在 app.utils.api_key
from app.utils.api_key import (  # noqa: F401
    _get_server_secret,
    generate_api_key,
    hash_api_key,
    verify_api_key,
)

# Re-export Cookie / 会话令牌（AES-GCM-256）— 实现在 app.utils.aes_crypto
from app.utils.aes_crypto import (  # noqa: F401
    _AES_KEY_LEN,
    _AES_NONCE_LEN,
    _get_aes_key,
    aes_gcm_decrypt,
    aes_gcm_encrypt,
    generate_session_token,
    verify_session_token,
)

__all__ = [
    # API Key
    "generate_api_key",
    "hash_api_key",
    "verify_api_key",
    # 密码
    "hash_password",
    "verify_password",
    "needs_rehash",
    # AES-GCM / 会话令牌
    "aes_gcm_encrypt",
    "aes_gcm_decrypt",
    "generate_session_token",
    "verify_session_token",
    # 常量
    "_ARGON2_TIME_COST",
    "_ARGON2_MEMORY_COST",
    "_ARGON2_PARALLELISM",
    "_AES_KEY_LEN",
    "_AES_NONCE_LEN",
]


# ========== Argon2id 参数（OWASP 2023 推荐）==========
# memory_cost=19456 KiB（19 MiB），time_cost=2，parallelism=1
# 对应 OWASP "Argon2id" 推荐基线：t=2, m=19456, p=1
_ARGON2_TIME_COST = 2
_ARGON2_MEMORY_COST = 19456  # KiB
_ARGON2_PARALLELISM = 1
_ARGON2_HASH_LEN = 32  # bytes
_ARGON2_SALT_LEN = 16  # bytes

# 模块级 PasswordHasher 单例（避免每次调用重复构造）
_password_hasher = PasswordHasher(
    time_cost=_ARGON2_TIME_COST,
    memory_cost=_ARGON2_MEMORY_COST,
    parallelism=_ARGON2_PARALLELISM,
    hash_len=_ARGON2_HASH_LEN,
    salt_len=_ARGON2_SALT_LEN,
)


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
