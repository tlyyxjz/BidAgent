"""credentials.py 凭证安全单元测试（v4.1 §13.1）。

覆盖 app.utils.credentials 三层凭证安全实现：
- API Key：generate_api_key / hash_api_key / verify_api_key（HMAC-SHA256）
- 用户密码：hash_password / verify_password / needs_rehash（Argon2id）
- Cookie / 会话令牌：aes_gcm_encrypt / aes_gcm_decrypt /
  generate_session_token / verify_session_token（AES-256-GCM）

测试依赖 conftest.py 在导入 app 前设置的 SECRET_KEY 环境变量。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re

import pytest
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError
from cryptography.exceptions import InvalidTag

from app.utils.credentials import (
    _ARGON2_MEMORY_COST,
    _ARGON2_PARALLELISM,
    _ARGON2_TIME_COST,
    _AES_NONCE_LEN,
    aes_gcm_decrypt,
    aes_gcm_encrypt,
    generate_api_key,
    generate_session_token,
    hash_api_key,
    hash_password,
    needs_rehash,
    verify_api_key,
    verify_password,
    verify_session_token,
)


# ========== API Key（HMAC-SHA256）==========


class TestGenerateApiKey:
    """generate_api_key 测试。"""

    def test_generate_api_key_returns_url_safe_string(self) -> None:
        """生成的 API Key 应为 URL 安全字符串（base64url 字符集，43 字符）。

        secrets.token_urlsafe(32) 对应 256 位熵，输出 43 字符的 base64url
        字符串（仅含 A-Z a-z 0-9 - _，无 padding）。
        """
        key = generate_api_key()
        assert isinstance(key, str)
        # 32 字节 → 44 base64 字符（含 padding），rstrip("=") → 43 字符
        assert len(key) == 43
        # base64url 字符集：A-Z a-z 0-9 - _
        assert re.fullmatch(r"[A-Za-z0-9_\-]+", key) is not None
        # 高熵：两次调用应不同
        assert generate_api_key() != key


class TestHashApiKey:
    """hash_api_key 测试。"""

    def test_hash_api_key_returns_hmac_digest(self) -> None:
        """hash_api_key 应返回基于 SECRET_KEY 的 HMAC-SHA256 hex 摘要。

        手动用 hmac.new 复算预期值，确保实现确实是 HMAC 而非纯 SHA256。
        """
        key = "sk_test_example_key"
        digest = hash_api_key(key)

        # 手动计算预期 HMAC-SHA256
        secret = os.environ["SECRET_KEY"].encode("utf-8")
        expected = hmac.new(
            secret, key.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        assert digest == expected
        # 64 字符 hex（与 SHA256 hexdigest 长度一致，DB schema 无需变更）
        assert len(digest) == 64
        assert re.fullmatch(r"[0-9a-f]+", digest) is not None

        # 关键性质：与纯 SHA256 不同（证明引入了服务端密钥）
        plain_sha256 = hashlib.sha256(key.encode("utf-8")).hexdigest()
        assert digest != plain_sha256

    def test_hash_api_key_different_keys_different_hashes(self) -> None:
        """不同 API Key 应产生不同 HMAC 摘要（抗碰撞）。"""
        key1 = "sk_first_production_key"
        key2 = "sk_second_production_key"
        h1 = hash_api_key(key1)
        h2 = hash_api_key(key2)
        assert h1 != h2
        # 同一 key 重复计算应稳定一致
        assert hash_api_key(key1) == h1


class TestVerifyApiKey:
    """verify_api_key 测试。"""

    def test_verify_api_key_correct(self) -> None:
        """正确的 key 与其存储摘要比对应返回 True。"""
        key = generate_api_key()
        stored = hash_api_key(key)
        assert verify_api_key(key, stored) is True
        # 常量时间比对不应影响正确性
        assert verify_api_key(key, hash_api_key(key)) is True

    def test_verify_api_key_wrong_key_returns_false(self) -> None:
        """错误的 key 与存储摘要比对应返回 False。"""
        real_key = "sk_real_key_abc123"
        stored = hash_api_key(real_key)
        # 多种错误 key 均应失败
        assert verify_api_key("sk_wrong_key", stored) is False
        assert verify_api_key("", stored) is False
        assert verify_api_key(real_key + "tampered", stored) is False
        # 摘要被篡改也应失败
        assert verify_api_key(
            real_key, stored[:-1] + ("0" if stored[-1] != "0" else "1")
        ) is False


# ========== 用户密码（Argon2id）==========


class TestHashPassword:
    """hash_password（Argon2id）测试。"""

    def test_hash_password_returns_phc_format(self) -> None:
        """hash_password 应返回 OWASP 推荐参数的 Argon2id PHC 字符串。

        PHC 格式：$argon2id$v=19$m=<mem>,t=<time>,p=<par>$<salt>$<hash>
        """
        phc = hash_password("P@ssw0rd!")
        assert isinstance(phc, str)
        # 必须是 argon2id（不是 argon2i 或 argon2d）
        assert phc.startswith("$argon2id$")
        # 参数必须对齐 OWASP 推荐基线
        assert f"m={_ARGON2_MEMORY_COST}" in phc
        assert f"t={_ARGON2_TIME_COST}" in phc
        assert f"p={_ARGON2_PARALLELISM}" in phc
        # PHC 字符串应有 5 段（按 $ 分隔）
        assert phc.count("$") == 5

    def test_hash_password_uses_random_salt(self) -> None:
        """同一密码两次哈希应得到不同结果（盐随机生成）。"""
        p1 = hash_password("same-password")
        p2 = hash_password("same-password")
        assert p1 != p2
        # 但参数段应一致（m/t/p 相同）
        # 提取 $argon2id$v=19$m=19456,t=2,p=1$ 段
        p1_params = "$".join(p1.split("$")[:3])
        p2_params = "$".join(p2.split("$")[:3])
        assert p1_params == p2_params

    def test_hash_password_rejects_empty(self) -> None:
        """空密码应被拒绝。"""
        with pytest.raises(ValueError, match="不能为空"):
            hash_password("")

    def test_hash_password_rejects_non_string(self) -> None:
        """非字符串输入应被拒绝。"""
        with pytest.raises(ValueError, match="必须是字符串"):
            hash_password(12345)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="必须是字符串"):
            hash_password(None)  # type: ignore[arg-type]

    def test_hash_password_accepts_unicode(self) -> None:
        """Unicode 密码应能正常哈希（中文、emoji 等）。"""
        for pw in ("中文密码123", "🔒安全密码", " пароль "):
            phc = hash_password(pw)
            assert phc.startswith("$argon2id$")
            assert verify_password(pw, phc) is True


class TestVerifyPassword:
    """verify_password（Argon2id）测试。"""

    def test_verify_password_correct(self) -> None:
        """正确密码应验证通过。"""
        phc = hash_password("correct-password-123")
        assert verify_password("correct-password-123", phc) is True

    def test_verify_password_wrong_returns_false(self) -> None:
        """错误密码应返回 False（不抛异常）。"""
        phc = hash_password("real-password")
        assert verify_password("wrong-password", phc) is False
        assert verify_password("", phc) is False
        assert verify_password("real-password ", phc) is False  # 末尾空格
        assert verify_password("REAL-PASSWORD", phc) is False  # 大小写

    def test_verify_password_rejects_invalid_hash(self) -> None:
        """非法 PHC 哈希应抛 ValueError（配置错误，不是验证失败）。"""
        with pytest.raises(ValueError, match="合法的 Argon2 PHC"):
            verify_password("any-password", "not-a-valid-hash")
        with pytest.raises(ValueError, match="合法的 Argon2 PHC"):
            verify_password("any-password", "$argon2id$malformed")

    def test_verify_password_empty_hash_raises(self) -> None:
        """空 phc_hash 应抛 ValueError。"""
        with pytest.raises(ValueError, match="不能为空"):
            verify_password("any", "")

    def test_verify_password_non_string_inputs_raise(self) -> None:
        """非字符串输入应抛 ValueError。"""
        with pytest.raises(ValueError, match="必须是字符串"):
            verify_password(123, "$argon2id$abc")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="必须是字符串"):
            verify_password("pw", None)  # type: ignore[arg-type]

    def test_verify_password_constant_for_same_input(self) -> None:
        """同一密码 + 哈希多次验证结果应稳定一致。"""
        phc = hash_password("stable-pw")
        results = [verify_password("stable-pw", phc) for _ in range(5)]
        assert all(r is True for r in results)
        results_wrong = [verify_password("other", phc) for _ in range(5)]
        assert all(r is False for r in results_wrong)


class TestNeedsRehash:
    """needs_rehash 测试。"""

    def test_needs_rehash_false_for_current_params(self) -> None:
        """当前参数生成的哈希不需要 rehash。"""
        phc = hash_password("any-password")
        assert needs_rehash(phc) is False

    def test_needs_rehash_true_for_weak_params(self) -> None:
        """旧/弱参数的哈希应触发 rehash。

        用低 memory_cost 单独构造一个 PasswordHasher 生成哈希，
        主 hasher 应识别为需要 rehash。
        """
        weak_hasher = PasswordHasher(
            time_cost=1,
            memory_cost=1024,  # 远低于 19456
            parallelism=1,
        )
        weak_phc = weak_hasher.hash("any-password")
        assert weak_phc.startswith("$argon2id$")
        assert needs_rehash(weak_phc) is True

    def test_needs_rehash_empty_returns_false(self) -> None:
        """空哈希返回 False（不抛异常）。"""
        assert needs_rehash("") is False

    def test_needs_rehash_invalid_hash_returns_true(self) -> None:
        """非法哈希返回 True（视为需要重新哈希）。"""
        assert needs_rehash("not-a-valid-hash") is True
        assert needs_rehash("$argon2id$malformed") is True


# ========== Cookie / 会话令牌（AES-256-GCM）==========


class TestAesGcmEncrypt:
    """aes_gcm_encrypt 测试。"""

    def test_encrypt_returns_base64_token(self) -> None:
        """加密结果应为 base64url 字符串（可安全放入 Cookie）。"""
        token = aes_gcm_encrypt("user_id=42")
        assert isinstance(token, str)
        # base64url 字符集：A-Z a-z 0-9 - _ 和 = padding
        # urlsafe_b64encode 不含 + /
        assert re.fullmatch(r"[A-Za-z0-9_\-]+={0,2}", token) is not None

    def test_encrypt_generates_unique_nonce(self) -> None:
        """每次加密同一明文应得到不同密文（nonce 随机）。"""
        plaintext = "same-payload"
        t1 = aes_gcm_encrypt(plaintext)
        t2 = aes_gcm_encrypt(plaintext)
        t3 = aes_gcm_encrypt(plaintext)
        assert t1 != t2 != t3 != t1
        # 但三个都能正确解密
        assert aes_gcm_decrypt(t1) == plaintext
        assert aes_gcm_decrypt(t2) == plaintext
        assert aes_gcm_decrypt(t3) == plaintext

    def test_encrypt_rejects_empty(self) -> None:
        """空明文应被拒绝。"""
        with pytest.raises(ValueError, match="不能为空"):
            aes_gcm_encrypt("")

    def test_encrypt_rejects_non_string(self) -> None:
        """非字符串输入应被拒绝。"""
        with pytest.raises(ValueError, match="必须是字符串"):
            aes_gcm_encrypt(123)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="必须是字符串"):
            aes_gcm_encrypt(None)  # type: ignore[arg-type]


class TestAesGcmDecrypt:
    """aes_gcm_decrypt 测试。"""

    def test_decrypt_round_trip(self) -> None:
        """加密-解密往返应还原明文。"""
        for plaintext in (
            "user_id=42",
            "session-abc-XYZ",
            "中文载荷",
            "🔒emoji-payload",
            '{"user_id": 42, "role": "admin"}',
            "x" * 1000,  # 长载荷
        ):
            token = aes_gcm_encrypt(plaintext)
            assert aes_gcm_decrypt(token) == plaintext

    def test_decrypt_tamper_detected(self) -> None:
        """篡改密文应抛 InvalidTag（完整性校验）。"""
        token = aes_gcm_encrypt("sensitive-data")
        # 篡改 base64 字符串中的一个字符
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
        with pytest.raises(InvalidTag):
            aes_gcm_decrypt(tampered)

    def test_decrypt_truncated_token_rejected(self) -> None:
        """长度不足的 token 应抛 ValueError（不能解密）。"""
        # 一个长度仅 10 字节的 base64 字符串（解码后 < nonce(12)+tag(16)）
        short_token = base64.urlsafe_b64encode(b"0123456789").decode("ascii")
        with pytest.raises(ValueError, match="长度不足"):
            aes_gcm_decrypt(short_token)

    def test_decrypt_invalid_base64_rejected(self) -> None:
        """非法 base64 字符串应抛 ValueError。"""
        with pytest.raises(ValueError, match="base64"):
            aes_gcm_decrypt("!!!not-base64!!!")

    def test_decrypt_empty_token_rejected(self) -> None:
        """空 token 应抛 ValueError。"""
        with pytest.raises(ValueError, match="不能为空"):
            aes_gcm_decrypt("")

    def test_decrypt_non_string_rejected(self) -> None:
        """非字符串 token 应抛 ValueError。"""
        with pytest.raises(ValueError, match="必须是字符串"):
            aes_gcm_decrypt(123)  # type: ignore[arg-type]


class TestAesGcmAad:
    """AES-GCM AAD（附加认证数据）测试。"""

    def test_aad_round_trip(self) -> None:
        """带 AAD 的加密-解密应还原明文。"""
        aad = b"user_id=42"
        token = aes_gcm_encrypt("session-data", aad=aad)
        assert aes_gcm_decrypt(token, aad=aad) == "session-data"

    def test_aad_mismatch_fails(self) -> None:
        """AAD 不匹配应解密失败（InvalidTag）。"""
        token = aes_gcm_encrypt("session-data", aad=b"user_id=42")
        with pytest.raises(InvalidTag):
            aes_gcm_decrypt(token, aad=b"user_id=43")

    def test_aad_missing_fails(self) -> None:
        """加密时带 AAD，解密时不带 AAD 应失败。"""
        token = aes_gcm_encrypt("session-data", aad=b"context")
        with pytest.raises(InvalidTag):
            aes_gcm_decrypt(token)  # 缺少 AAD

    def test_aad_added_fails(self) -> None:
        """加密时不带 AAD，解密时带 AAD 应失败。"""
        token = aes_gcm_encrypt("session-data")
        with pytest.raises(InvalidTag):
            aes_gcm_decrypt(token, aad=b"unexpected")


class TestSessionTokenAlias:
    """generate_session_token / verify_session_token 语义化别名测试。"""

    def test_session_token_round_trip(self) -> None:
        """会话令牌生成-验证应还原载荷。"""
        token = generate_session_token("user-42")
        assert verify_session_token(token) == "user-42"

    def test_session_token_equivalent_to_aes_gcm(self) -> None:
        """generate_session_token 等价于 aes_gcm_encrypt。"""
        # 同一载荷，session_token 加密的可以用 aes_gcm_decrypt 解密
        token = generate_session_token("payload-xyz")
        assert aes_gcm_decrypt(token) == "payload-xyz"
        # 反之亦然
        token2 = aes_gcm_encrypt("payload-abc")
        assert verify_session_token(token2) == "payload-abc"

    def test_session_token_with_aad(self) -> None:
        """会话令牌支持 AAD 绑定上下文。"""
        aad = b"ip=192.168.1.1"
        token = generate_session_token("user-42", aad=aad)
        assert verify_session_token(token, aad=aad) == "user-42"
        # AAD 不匹配应失败
        with pytest.raises(InvalidTag):
            verify_session_token(token, aad=b"ip=10.0.0.1")


# ========== 跨层一致性 ==========


class TestCredentialLayersConsistency:
    """三层凭证安全实现的一致性测试。"""

    def test_api_key_and_password_are_independent(self) -> None:
        """API Key 哈希与密码哈希使用不同算法，互不干扰。"""
        api_key = generate_api_key()
        api_hash = hash_api_key(api_key)
        # API Key 哈希是 64 字符 hex（HMAC-SHA256）
        assert len(api_hash) == 64
        assert re.fullmatch(r"[0-9a-f]+", api_hash) is not None

        pw_hash = hash_password("some-password")
        # 密码哈希是 PHC 格式（$argon2id$...）
        assert pw_hash.startswith("$argon2id$")
        # 两者格式完全不同
        assert api_hash != pw_hash

    def test_aes_key_derived_from_same_secret_key(self) -> None:
        """AES 密钥与 HMAC 密钥都来自 SECRET_KEY（同一服务端密钥）。"""
        # 通过加密-解密 round-trip 间接验证 AES 密钥可用
        token = aes_gcm_encrypt("verify-key-available")
        assert aes_gcm_decrypt(token) == "verify-key-available"
        # 通过 HMAC 计算间接验证 HMAC 密钥可用
        assert len(hash_api_key("test")) == 64

    def test_no_plaintext_in_token(self) -> None:
        """加密令牌中不应包含明文（即使明文很短）。"""
        plaintext = "VERY_SECRET_PAYLOAD_123"
        token = aes_gcm_encrypt(plaintext)
        # base64 解码后是 nonce+ciphertext+tag，不应含明文字节
        blob = base64.urlsafe_b64decode(token.encode("ascii"))
        assert plaintext.encode("utf-8") not in blob
        # base64 字符串中也不应直接出现明文
        assert plaintext not in token



"""SECRET_KEY 配置错误场景与 Argon2Error 兜底分支测试。

覆盖 credentials.py 中以下未覆盖行：
- 103: _get_server_secret 的 SECRET_KEY 未设置分支
- 212-214: verify_password 的 Argon2Error 兜底分支
- 257: _get_aes_key 的 SECRET_KEY 未设置分支
- 264-265: SECRET_KEY 不是合法 hex
- 269: SECRET_KEY 长度不为 32 字节
"""


import pytest
from argon2.exceptions import Argon2Error
from unittest.mock import patch

from app.utils import credentials as creds




import pytest
from unittest.mock import patch
from argon2.exceptions import Argon2Error
from app.utils import credentials as creds


class TestServerSecretMissing:
    """SECRET_KEY 未设置时的错误处理。"""

    def test_hash_api_key_raises_when_secret_key_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SECRET_KEY 缺失时，hash_api_key 应抛 RuntimeError 提示生成方法。"""
        monkeypatch.delenv("SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError, match="SECRET_KEY 环境变量未设置"):
            creds.hash_api_key("sk_test")

    def test_aes_gcm_encrypt_raises_when_secret_key_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SECRET_KEY 缺失时，aes_gcm_encrypt 应抛 RuntimeError。"""
        monkeypatch.delenv("SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError, match="SECRET_KEY 环境变量未设置"):
            creds.aes_gcm_encrypt("payload")


class TestAesKeyInvalidSecret:
    """SECRET_KEY 格式非法时的错误处理。"""

    def test_aes_gcm_raises_when_secret_key_not_hex(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SECRET_KEY 非 hex 字符串应抛 RuntimeError。"""
        monkeypatch.setenv("SECRET_KEY", "not-hex-string-zzz")
        with pytest.raises(RuntimeError, match="不是合法的 hex"):
            creds.aes_gcm_encrypt("payload")

    def test_aes_gcm_raises_when_secret_key_wrong_length(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SECRET_KEY 长度不为 32 字节应抛 RuntimeError。"""
        # 16 字符 hex = 8 字节，远小于 32
        monkeypatch.setenv("SECRET_KEY", "a" * 16)
        with pytest.raises(RuntimeError, match="必须编码 32 字节"):
            creds.aes_gcm_encrypt("payload")


class TestVerifyPasswordArgon2ErrorFallback:
    """verify_password 的 Argon2Error 兜底分支。"""

    def test_verify_password_returns_false_on_argon2_error(self) -> None:
        """底层 Argon2Error（非 VerifyMismatch/InvalidHash/VerificationError）应返回 False。

        通过 mock _password_hasher.verify 抛 Argon2Error（基类，不是其子类），
        验证 except Argon2Error 分支返回 False 而非抛异常。
        """
        from unittest.mock import MagicMock
        phc = creds.hash_password("any-password")
        # PasswordHasher.verify 是 cffi 只读属性，无法 patch.object 单点 mock，
        # 改用 MagicMock 替换整个 _password_hasher，让其 verify 抛 Argon2Error
        # （基类实例，非 VerifyMismatch/InvalidHash/VerificationError 子类），
        # 验证 except Argon2Error 分支返回 False 而非抛异常。
        fake_hasher = MagicMock()
        fake_hasher.verify.side_effect = Argon2Error("simulated low-level error")
        with patch.object(creds, "_password_hasher", fake_hasher):
            assert creds.verify_password("any-password", phc) is False
