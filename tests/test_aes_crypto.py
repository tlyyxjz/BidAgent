"""app/utils/aes_crypto.py 单元测试。

覆盖目标：
- 加解密往返（不同明文 / 相同明文 / 空字符串 / Unicode / 超长字符串）
- 错误密钥 / 篡改密文 应抛 InvalidTag
- 会话令牌 generate_session_token / verify_session_token：
  生成、有效、过期（AAD 时间窗口）、篡改、错误密钥

注意：
- conftest.py 已设置 SECRET_KEY='a'*64（64 字符 hex → 32 字节 AES-256 密钥）
- 错误密钥场景用 monkeypatch 临时改写 SECRET_KEY
- 所有函数同步，无需 asyncio 标记
"""

from __future__ import annotations

import base64
import os

import pytest
from cryptography.exceptions import InvalidTag

from app.utils.aes_crypto import (
    _get_aes_key,
    aes_gcm_decrypt,
    aes_gcm_encrypt,
    generate_session_token,
    verify_session_token,
)


# ========== 加解密往返 ==========


class TestEncryptDecryptRoundTrip:
    """加解密往返测试。"""

    def test_encrypt_decrypt_roundtrip(self) -> None:
        """加密后解密应返回原文。"""
        plaintext = "user_id=42&role=admin"
        token = aes_gcm_encrypt(plaintext)
        assert isinstance(token, str)
        assert aes_gcm_decrypt(token) == plaintext

    def test_encrypt_different_plaintexts(self) -> None:
        """不同明文应产生不同密文。"""
        t1 = aes_gcm_encrypt("plaintext-A")
        t2 = aes_gcm_encrypt("plaintext-B")
        assert t1 != t2
        # 解密还原各自原文
        assert aes_gcm_decrypt(t1) == "plaintext-A"
        assert aes_gcm_decrypt(t2) == "plaintext-B"

    def test_encrypt_same_plaintext_different_ciphertext(self) -> None:
        """相同明文两次加密应产生不同密文（因 nonce 随机）。"""
        plaintext = "same-payload"
        t1 = aes_gcm_encrypt(plaintext)
        t2 = aes_gcm_encrypt(plaintext)
        t3 = aes_gcm_encrypt(plaintext)
        assert t1 != t2
        assert t1 != t3
        assert t2 != t3
        # 三次解密都应还原原文
        assert aes_gcm_decrypt(t1) == plaintext
        assert aes_gcm_decrypt(t2) == plaintext
        assert aes_gcm_decrypt(t3) == plaintext

    def test_empty_string(self) -> None:
        """空字符串：aes_gcm_encrypt 明确拒绝空明文（ValueError）。"""
        with pytest.raises(ValueError, match="不能为空"):
            aes_gcm_encrypt("")

    def test_unicode_string(self) -> None:
        """中文字符串加解密往返。"""
        for plaintext in (
            "中文载荷",
            "用户ID=42;角色=管理员",
            "🔒emoji-会话-token",
            "上海充电桩招标信息",
        ):
            token = aes_gcm_encrypt(plaintext)
            assert aes_gcm_decrypt(token) == plaintext

    def test_long_string(self) -> None:
        """超长字符串（10KB）加解密往返。"""
        plaintext = "x" * 10240  # 10KB
        token = aes_gcm_encrypt(plaintext)
        assert len(token) > 0
        assert aes_gcm_decrypt(token) == plaintext


# ========== 错误密钥 / 篡改密文 ==========


class TestWrongKeyAndTamper:
    """错误密钥与篡改检测。"""

    def test_decrypt_wrong_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """用错误密钥解密应抛 InvalidTag。"""
        # 1. 用默认 SECRET_KEY 加密
        token = aes_gcm_encrypt("sensitive-data")
        # 2. 切换到不同的 SECRET_KEY（仍是合法 64 字符 hex，但不同字节）
        monkeypatch.setenv("SECRET_KEY", "b" * 64)
        # 3. 用新密钥解密旧 token → InvalidTag
        with pytest.raises(InvalidTag):
            aes_gcm_decrypt(token)

    def test_decrypt_tampered_ciphertext(self) -> None:
        """篡改密文后解密应失败（InvalidTag）。"""
        token = aes_gcm_encrypt("sensitive-data")
        # 篡改 base64 字符串最后一个字符
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
        with pytest.raises(InvalidTag):
            aes_gcm_decrypt(tampered)


# ========== 会话令牌 ==========


class TestSessionToken:
    """generate_session_token / verify_session_token 测试。"""

    def test_generate_session_token(self) -> None:
        """生成令牌格式正确（base64url 字符串）。"""
        token = generate_session_token("user-42")
        assert isinstance(token, str)
        assert len(token) > 0
        # base64url 字符集：A-Z a-z 0-9 - _ 和 = padding
        assert all(c.isalnum() or c in "-_=" for c in token)
        # 令牌可被 aes_gcm_decrypt 解开（语义化别名内部调用同一函数）
        assert aes_gcm_decrypt(token) == "user-42"

    def test_verify_session_token_valid(self) -> None:
        """有效令牌验证通过。"""
        token = generate_session_token("user-42")
        payload = verify_session_token(token)
        assert payload == "user-42"

    def test_verify_session_token_expired(self) -> None:
        """过期令牌验证失败。

        aes_crypto 本身不内置过期检查，应用层需在 payload 中编码 expiry，
        或通过 AAD 绑定时间窗口。此处用 AAD 模拟「签发时窗口」与
        「验证时窗口」不一致 → InvalidTag。
        """
        # 签发时绑定过去的时间窗口
        issued_window = b"time_window=2024-01-01"
        token = generate_session_token("user-42", aad=issued_window)
        # 验证时使用当前时间窗口（AAD 不匹配）→ InvalidTag
        current_window = b"time_window=2026-08-05"
        with pytest.raises(InvalidTag):
            verify_session_token(token, aad=current_window)
        # 同一窗口仍可解密（验证函数本身能还原载荷）
        assert verify_session_token(token, aad=issued_window) == "user-42"

    def test_verify_session_token_tampered(self) -> None:
        """篡改令牌验证失败（InvalidTag）。"""
        token = generate_session_token("user-42")
        # 篡改最后一个字符
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
        with pytest.raises(InvalidTag):
            verify_session_token(tampered)

    def test_verify_session_token_wrong_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """错误密钥的令牌验证失败（InvalidTag）。"""
        token = generate_session_token("user-42")
        # 切换到不同的 SECRET_KEY
        monkeypatch.setenv("SECRET_KEY", "c" * 64)
        with pytest.raises(InvalidTag):
            verify_session_token(token)


# ========== 额外边界场景 ==========


class TestAesCryptoBoundary:
    """AES 加解密边界场景补充。"""

    def test_get_aes_key_returns_32_bytes(self) -> None:
        """_get_aes_key 返回 32 字节密钥（AES-256）。"""
        key = _get_aes_key()
        assert isinstance(key, bytes)
        assert len(key) == 32

    def test_encrypt_token_contains_nonce_and_tag(self) -> None:
        """加密结果解码后至少包含 nonce(12B) + tag(16B) = 28 字节。"""
        token = aes_gcm_encrypt("x")
        blob = base64.urlsafe_b64decode(token.encode("ascii"))
        # nonce(12) + ciphertext(>=1) + tag(16) >= 29
        assert len(blob) >= 12 + 1 + 16

    def test_decrypt_with_mismatched_aad_fails(self) -> None:
        """加密时带 AAD，解密时不带 / 带不同 AAD → InvalidTag。"""
        aad = b"user_id=42"
        token = aes_gcm_encrypt("session-data", aad=aad)
        # 不带 AAD 解密
        with pytest.raises(InvalidTag):
            aes_gcm_decrypt(token)
        # 带不同 AAD 解密
        with pytest.raises(InvalidTag):
            aes_gcm_decrypt(token, aad=b"user_id=43")
        # 带正确 AAD 解密成功
        assert aes_gcm_decrypt(token, aad=aad) == "session-data"

    def test_encrypt_rejects_non_string(self) -> None:
        """非字符串明文应抛 ValueError。"""
        with pytest.raises(ValueError, match="必须是字符串"):
            aes_gcm_encrypt(123)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="必须是字符串"):
            aes_gcm_encrypt(None)  # type: ignore[arg-type]

    def test_decrypt_rejects_empty_token(self) -> None:
        """空 token 应抛 ValueError。"""
        with pytest.raises(ValueError, match="不能为空"):
            aes_gcm_decrypt("")

    def test_decrypt_rejects_invalid_base64(self) -> None:
        """非法 base64 字符串应抛 ValueError。"""
        with pytest.raises(ValueError, match="base64"):
            aes_gcm_decrypt("!!!not-base64!!!")

    def test_decrypt_rejects_truncated_token(self) -> None:
        """长度不足的 token 应抛 ValueError。"""
        short_token = base64.urlsafe_b64encode(b"0123456789").decode("ascii")
        with pytest.raises(ValueError, match="长度不足"):
            aes_gcm_decrypt(short_token)

    def test_secret_key_missing_raises_runtime_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SECRET_KEY 未设置时，_get_aes_key 应抛 RuntimeError。"""
        monkeypatch.delenv("SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            _get_aes_key()

    def test_secret_key_invalid_hex_raises_runtime_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SECRET_KEY 非 hex 字符串应抛 RuntimeError。"""
        monkeypatch.setenv("SECRET_KEY", "not-a-hex-string-zzzz")
        with pytest.raises(RuntimeError, match="hex"):
            _get_aes_key()

    def test_secret_key_wrong_length_raises_runtime_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SECRET_KEY 长度非 32 字节应抛 RuntimeError。"""
        # 16 字节 hex（32 字符）→ 解码后 16 字节，不满足 AES-256 要求
        monkeypatch.setenv("SECRET_KEY", "a" * 32)
        with pytest.raises(RuntimeError, match="32 字节"):
            _get_aes_key()
