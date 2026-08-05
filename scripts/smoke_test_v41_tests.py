"""v4.1 合规采集层冒烟测试用例 mixin（6 个组件的端到端测试）。"""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


class SmokeTestTestsMixin:
    """冒烟测试用例 mixin（6 个组件的端到端测试）。"""

    async def test_credentials(self) -> None:
        """1. credentials.py - Argon2id + AES-GCM 往返。"""
        print("\n=== 1. credentials.py 冒烟测试 ===")
        try:
            from app.utils.credentials import (
                hash_password, verify_password, needs_rehash,
                aes_gcm_encrypt, aes_gcm_decrypt,
                generate_api_key, hash_api_key, verify_api_key,
                generate_session_token, verify_session_token,
            )

            # Argon2id
            phc = hash_password("smoke-test-password")
            assert phc.startswith("$argon2id$"), f"expected argon2id, got {phc[:20]}"
            assert verify_password("smoke-test-password", phc) is True
            assert verify_password("wrong", phc) is False
            assert needs_rehash(phc) is False
            self.record("Argon2id hash/verify", True, f"PHC format OK, verify OK")

            # AES-GCM
            token = aes_gcm_encrypt("user_id=smoke-test")
            decrypted = aes_gcm_decrypt(token)
            assert decrypted == "user_id=smoke-test"
            self.record("AES-GCM encrypt/decrypt", True, f"round-trip OK")

            # Session token
            stoken = generate_session_token("user-42")
            assert verify_session_token(stoken) == "user-42"
            self.record("Session token", True, "generate/verify OK")

            # API Key
            api_key = generate_api_key()
            assert len(api_key) == 43
            stored = hash_api_key(api_key)
            assert verify_api_key(api_key, stored) is True
            self.record("API Key HMAC-SHA256", True, f"key len={len(api_key)}")

        except Exception as exc:
            self.record("credentials", False, f"EXCEPTION: {exc}")

    async def test_rate_limiter(self) -> None:
        """2. rate_limiter.py - 域名级频率限制。"""
        print("\n=== 2. rate_limiter.py 冒烟测试 ===")
        try:
            from app.core.rate_limiter import DomainRateLimiter

            limiter = DomainRateLimiter(default_interval=0.1)

            # 首次请求不等待
            w1 = await limiter.wait("https://ccgp.gov.cn/a")
            assert w1 == 0.0, f"first request should not wait, got {w1}"
            self.record("首次请求不等待", True, f"waited={w1}")

            # 同域名二次请求应等待
            start = time.monotonic()
            w2 = await limiter.wait("https://ccgp.gov.cn/b")
            elapsed = time.monotonic() - start
            assert w2 > 0, f"second request should wait, got {w2}"
            assert elapsed >= 0.08, f"should sleep ~0.1s, elapsed={elapsed}"
            self.record("同域名二次请求等待", True, f"waited={w2:.3f}s elapsed={elapsed:.3f}s")

            # 不同域名不阻塞
            w3 = await limiter.wait("https://chinabidding.cn/a")
            assert w3 == 0.0, f"different domain should not wait, got {w3}"
            self.record("不同域名互不阻塞", True, f"waited={w3}")

            # release 回滚
            await limiter.wait("https://example.com/a")
            await limiter.release("https://example.com/a")
            w4 = await limiter.wait("https://example.com/b")
            assert w4 == 0.0, f"after release should not wait, got {w4}"
            self.record("release 回滚", True, "release 后立即放行")

        except Exception as exc:
            self.record("rate_limiter", False, f"EXCEPTION: {exc}")

    async def test_robots_checker(self) -> None:
        """3. robots_checker.py - robots.txt 解析（mock httpx）。"""
        print("\n=== 3. robots_checker.py 冒烟测试 ===")
        try:
            from app.core.robots_checker import RobotsChecker
            from urllib.robotparser import RobotFileParser

            checker = RobotsChecker()

            # mock: 允许所有（用 allow-example.com 域名）
            allow_parser = RobotFileParser()
            allow_parser.parse([])
            with patch.object(checker, "_fetch_and_parse", return_value=allow_parser):
                allowed = await checker.is_allowed("https://allow-example.com/page")
            assert allowed is True
            self.record("robots.txt 允许", True, "allow_all parser")

            # mock: 禁止所有（用 deny-example.com 域名，避免缓存干扰）
            deny_parser = RobotFileParser()
            deny_parser.parse(["User-agent: *", "Disallow: /"])
            with patch.object(checker, "_fetch_and_parse", return_value=deny_parser):
                allowed = await checker.is_allowed("https://deny-example.com/page")
            assert allowed is False
            self.record("robots.txt 禁止", True, "disallow / parser")

            # mock: 404 全允许（用 no-robots.com 域名）
            empty_parser = RobotFileParser()
            empty_parser.parse([])
            with patch.object(checker, "_fetch_and_parse", return_value=empty_parser):
                allowed = await checker.is_allowed("https://no-robots.com/page")
            assert allowed is True
            self.record("robots.txt 404 全允许", True, "RFC 9309")

        except Exception as exc:
            self.record("robots_checker", False, f"EXCEPTION: {exc}")

    async def test_cache_manager(self) -> None:
        """4. cache_manager.py - ETag/304 缓存。"""
        print("\n=== 4. cache_manager.py 冒烟测试 ===")
        try:
            from app.core.cache_manager import CacheManager

            cm = CacheManager(ttl=3600, max_entries=100)

            # 首次 get 应返回 None
            entry = await cm.get("https://example.com/api")
            assert entry is None
            self.record("首次 get 返回 None", True, "cache miss")

            # set 后 get 应命中
            await cm.set(
                "https://example.com/api",
                "<html>content</html>",
                etag='"abc123"',
                last_modified="Mon, 03 Aug 2026 10:00:00 GMT",
            )
            entry = await cm.get("https://example.com/api")
            assert entry is not None
            assert entry.body == "<html>content</html>"
            assert entry.etag == '"abc123"'
            self.record("set/get 命中", True, f"etag={entry.etag}")

            # 条件请求头
            headers = await cm.get_conditional_headers("https://example.com/api")
            assert "If-None-Match" in headers
            assert "If-Modified-Since" in headers
            self.record("条件请求头", True, f"headers={list(headers.keys())}")

            # handle_304
            body = await cm.handle_304("https://example.com/api")
            assert body == "<html>content</html>"
            self.record("handle_304 返回缓存", True, "304 复用缓存")

            # invalidate
            await cm.invalidate("https://example.com/api")
            entry = await cm.get("https://example.com/api")
            assert entry is None
            self.record("invalidate 失效", True, "invalidate 后 miss")

        except Exception as exc:
            self.record("cache_manager", False, f"EXCEPTION: {exc}")

    async def test_snapshot_manager(self) -> None:
        """5. snapshot_manager.py - 快照/哈希/版本管理。"""
        print("\n=== 5. snapshot_manager.py 冒烟测试 ===")
        try:
            from app.core.snapshot_manager import SnapshotManager
            import tempfile

            # 用临时目录避免污染
            with tempfile.TemporaryDirectory() as tmpdir:
                sm = SnapshotManager(storage_dir=Path(tmpdir))

                # 首次保存：新版本
                r1 = await sm.save_snapshot(
                    "https://example.com/page1",
                    "<html>v1</html>",
                    text="v1 content",
                )
                assert r1.is_new_version is True
                assert r1.version_number == 1
                assert len(r1.content_hash) == 64
                self.record("首次保存新版本", True, f"v{r1.version_number} hash={r1.content_hash[:16]}")

                # 相同内容：不创建新版本
                r2 = await sm.save_snapshot(
                    "https://example.com/page1",
                    "<html>v1</html>",
                    text="v1 content",
                )
                assert r2.is_new_version is False
                assert r2.version_number == 1
                self.record("相同内容不创建新版本", True, f"v{r2.version_number} unchanged")

                # 内容变化：新版本
                r3 = await sm.save_snapshot(
                    "https://example.com/page1",
                    "<html>v2</html>",
                    text="v2 content",
                    material=True,
                )
                assert r3.is_new_version is True
                assert r3.version_number == 2
                assert r3.material is True
                self.record("内容变化创建新版本", True, f"v{r3.version_number} material={r3.material}")

                # 历史版本
                history = await sm.get_history("https://example.com/page1")
                assert len(history) == 2
                self.record("历史版本查询", True, f"versions={len(history)}")

                # 统计
                stats = sm.stats()
                assert stats["urls"] == 1
                assert stats["total_versions"] == 2
                self.record("统计信息", True, f"urls={stats['urls']} versions={stats['total_versions']}")

        except Exception as exc:
            self.record("snapshot_manager", False, f"EXCEPTION: {exc}")

    async def test_template_monitor(self) -> None:
        """6. template_monitor.py - 结构签名（mock page）。"""
        print("\n=== 6. template_monitor.py 冒烟测试 ===")
        try:
            from app.core.template_monitor import TemplateMonitor

            tm = TemplateMonitor()

            # mock page
            mock_page = AsyncMock()
            mock_page.query_selector_all = AsyncMock(return_value=[MagicMock(), MagicMock()])
            mock_page.query_selector = AsyncMock(return_value=MagicMock())
            mock_page.query_selector.return_value.inner_text = AsyncMock(return_value="key text")

            selectors = {"title": "h1.title", "content": "div.content"}

            # 首次检查：True（首次记录）
            changed1 = await tm.check("ccgp", mock_page, selectors, key_selector="h1.title")
            assert changed1 is True
            self.record("首次检查返回 True", True, "first record")

            # 第二次相同结构：False
            changed2 = await tm.check("ccgp", mock_page, selectors, key_selector="h1.title")
            assert changed2 is False
            self.record("相同结构返回 False", True, "unchanged")

            # 结构变化：True
            mock_page.query_selector_all = AsyncMock(return_value=[MagicMock()])  # 命中数变化
            changed3 = await tm.check("ccgp", mock_page, selectors, key_selector="h1.title")
            assert changed3 is True
            self.record("结构变化返回 True", True, "structure changed")

            # 统计
            stats = tm.stats()
            assert stats["templates"] == 1
            self.record("统计信息", True, f"templates={stats['templates']}")

        except Exception as exc:
            self.record("template_monitor", False, f"EXCEPTION: {exc}")
