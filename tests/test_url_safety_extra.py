"""url_safety.py 补充测试：提升覆盖率 80% → 95%+。

覆盖未覆盖行：51, 53, 66-69, 72, 78-79, 82, 131, 134-135

策略：
- _is_ip_blocked / _check_dns_records 内部函数未被 conftest mock，直接调用
- is_safe_url 被 conftest autouse fixture mock，通过模块级导入保存原始函数引用
  （from ... import 在模块导入时执行，先于 fixture，拿到原始函数对象）
- DNS 解析相关分支用 monkeypatch 替换 socket.getaddrinfo
"""
from __future__ import annotations

import ipaddress
import socket
from unittest.mock import patch

import pytest

# 在模块导入时保存原始 is_safe_url（conftest 的 autouse fixture 在每个测试前
# 才会用 monkeypatch 替换 app.utils.url_safety.is_safe_url，但此处 from import
# 拿到的是原始函数对象引用，不受模块属性替换影响）
from app.utils.url_safety import (
    _BLOCKED_HOSTNAMES,
    _check_dns_records,
    _is_ip_blocked,
    is_safe_url as _real_is_safe_url,
)


# ============================================================
# 测试套件 1：_is_ip_blocked 多类别 IP 检测（行 51, 53）
# ============================================================

class TestIsIpBlockedCategories:
    """覆盖 _is_ip_blocked 中 multicast / reserved 分支。"""

    def test_multicast_ipv4_blocked(self):
        """行 51：224.0.0.1（组播地址）应被拒绝。"""
        ip = ipaddress.ip_address("224.0.0.1")
        blocked, reason = _is_ip_blocked(ip)
        assert blocked, "224.0.0.1 (multicast) 必须被拒绝"
        assert "multicast" in reason

    def test_multicast_ipv6_blocked(self):
        """行 51：IPv6 组播地址 ff02::1 应被拒绝。"""
        ip = ipaddress.ip_address("ff02::1")
        blocked, reason = _is_ip_blocked(ip)
        assert blocked
        assert "multicast" in reason

    def test_reserved_ipv4_blocked(self):
        """行 53：240.0.0.1（保留地址）应被拒绝。"""
        ip = ipaddress.ip_address("240.0.0.1")
        blocked, reason = _is_ip_blocked(ip)
        assert blocked, "240.0.0.1 (reserved) 必须被拒绝"
        assert "reserved" in reason

    def test_reserved_ipv6_blocked(self):
        """行 53：IPv6 保留地址应被拒绝。"""
        # ::是未指定地址，fe80:: 是 link-local（已测），用 2001:db8:: （文档用途，is_reserved）
        ip = ipaddress.ip_address("2001:db8::1")
        blocked, _ = _is_ip_blocked(ip)
        assert blocked

    def test_public_ip_not_blocked(self):
        """公网 IP 不应被拒绝（8.8.8.8）。"""
        ip = ipaddress.ip_address("8.8.8.8")
        blocked, _ = _is_ip_blocked(ip)
        assert not blocked


# ============================================================
# 测试套件 2：_check_dns_records DNS 解析错误分支（行 66-69, 72, 78-79, 82）
# ============================================================

class TestCheckDnsRecordsErrors:
    """覆盖 _check_dns_records 中各种 DNS 解析错误分支。"""

    def test_dns_gaierror_returns_false(self, monkeypatch):
        """行 66-67：socket.gaierror 时返回 (False, reason)。"""
        def _raise_gaierror(*args, **kwargs):
            raise socket.gaierror("name resolution failed")

        monkeypatch.setattr(socket, "getaddrinfo", _raise_gaierror)
        blocked, reason = _check_dns_records("nonexistent.invalid")
        assert not blocked
        assert "dns resolve failed" in reason

    def test_dns_generic_exception_returns_false(self, monkeypatch):
        """行 68-69：非 gaierror 的其他异常时返回 (False, reason)。"""
        def _raise_generic(*args, **kwargs):
            raise RuntimeError("unexpected error")

        monkeypatch.setattr(socket, "getaddrinfo", _raise_generic)
        blocked, reason = _check_dns_records("broken.example.com")
        assert not blocked
        assert "dns resolve error" in reason

    def test_dns_no_records_returns_false(self, monkeypatch):
        """行 72：DNS 解析返回空列表时返回 (False, reason)。"""
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [])
        blocked, reason = _check_dns_records("empty.example.com")
        assert not blocked
        assert "dns no records" in reason

    def test_dns_returns_non_ip_string_skipped(self, monkeypatch):
        """行 78-79：DNS 返回的地址中包含非 IP 字符串时跳过（ValueError）。"""
        # getaddrinfo 返回 (family, type, proto, canonname, sockaddr)
        # sockaddr[0] 是 IP 字符串；构造一个非 IP 的字符串触发 ValueError
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *a, **k: [
                (socket.AF_INET, 0, 0, "", ("not-an-ip", 0)),
            ],
        )
        blocked, reason = _check_dns_records("weird.example.com")
        # 非 IP 字符串被跳过，没有 IP 被校验，返回 (False, "")
        assert not blocked
        assert reason == ""

    def test_dns_resolved_to_blocked_ip_returns_true(self, monkeypatch):
        """行 82：DNS 解析到内网 IP 时返回 (True, reason)。"""
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *a, **k: [
                (socket.AF_INET, 0, 0, "", ("10.0.0.1", 0)),
            ],
        )
        blocked, reason = _check_dns_records("internal.example.com")
        assert blocked
        assert "dns resolved to" in reason
        assert "private" in reason

    def test_dns_resolved_to_loopback_returns_true(self, monkeypatch):
        """行 82：DNS 解析到 127.0.0.1 时返回 (True, reason)。"""
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *a, **k: [
                (socket.AF_INET, 0, 0, "", ("127.0.0.1", 0)),
            ],
        )
        blocked, reason = _check_dns_records("loopback.example.com")
        assert blocked
        assert "loopback" in reason

    def test_dns_resolved_to_public_ip_returns_false(self, monkeypatch):
        """DNS 解析到公网 IP 时返回 (False, "")。"""
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *a, **k: [
                (socket.AF_INET, 0, 0, "", ("8.8.8.8", 0)),
            ],
        )
        blocked, reason = _check_dns_records("public.example.com")
        assert not blocked
        assert reason == ""

    def test_dns_mixed_ips_one_blocked_returns_true(self, monkeypatch):
        """DNS 返回多个 IP，其中一个是内网 IP → 被拦截。"""
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *a, **k: [
                (socket.AF_INET, 0, 0, "", ("8.8.8.8", 0)),
                (socket.AF_INET, 0, 0, "", ("192.168.1.1", 0)),
            ],
        )
        blocked, reason = _check_dns_records("mixed.example.com")
        assert blocked
        assert "private" in reason


# ============================================================
# 测试套件 3：is_safe_url 完整链路（行 131, 134-135）
# ============================================================

class TestIsSafeUrlFullChain:
    """覆盖 is_safe_url 中 DNS 拦截和异常捕获分支。

    使用模块级保存的 _real_is_safe_url（不受 conftest mock 影响）。
    """

    def test_is_safe_url_dns_blocked_returns_false(self, monkeypatch):
        """行 131：域名 DNS 解析到内网 IP 时 is_safe_url 返回不安全。"""
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *a, **k: [
                (socket.AF_INET, 0, 0, "", ("10.0.0.1", 0)),
            ],
        )
        safe, reason = _real_is_safe_url("http://internal.example.com/page")
        assert not safe
        assert "dns resolved" in reason

    def test_is_safe_url_dns_no_records_returns_true(self, monkeypatch):
        """DNS 无记录时 is_safe_url 返回安全（域名无法解析不视为 SSRF）。"""
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [])
        safe, reason = _real_is_safe_url("http://empty.example.com/page")
        assert safe
        assert reason == ""

    def test_is_safe_url_dns_gaierror_returns_true(self, monkeypatch):
        """行 67：DNS 解析失败（gaierror）时 is_safe_url 返回安全。"""
        def _raise_gaierror(*args, **kwargs):
            raise socket.gaierror("name resolution failed")

        monkeypatch.setattr(socket, "getaddrinfo", _raise_gaierror)
        safe, _ = _real_is_safe_url("http://nonexistent.invalid/page")
        assert safe

    def test_is_safe_url_parse_error_returns_false(self, monkeypatch):
        """行 134-135：内部异常时 is_safe_url 返回不安全。

        通过 monkeypatch 让 urlparse 抛异常来触发外层 except。
        """
        import app.utils.url_safety as url_safety_mod

        def _raise_on_parse(*args, **kwargs):
            raise RuntimeError("parse boom")

        monkeypatch.setattr("app.utils.url_safety.urlparse", _raise_on_parse)
        safe, reason = _real_is_safe_url("http://example.com/page")
        assert not safe
        assert "url parse error" in reason

    def test_is_safe_url_public_domain_returns_true(self, monkeypatch):
        """公网域名 DNS 解析到公网 IP 时返回安全。"""
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *a, **k: [
                (socket.AF_INET, 0, 0, "", ("93.184.216.34", 0)),
            ],
        )
        safe, reason = _real_is_safe_url("http://example.com/page")
        assert safe
        assert reason == ""

    def test_is_safe_url_ipv6_address_blocked(self):
        """IPv6 地址形式的 URL 直接被 _is_ip_blocked 拦截。"""
        safe, reason = _real_is_safe_url("http://[::1]/admin")
        assert not safe
        assert "loopback" in reason

    def test_is_safe_url_multicast_address_blocked(self):
        """行 51：IPv4 组播地址 URL 被拒绝。"""
        safe, reason = _real_is_safe_url("http://224.0.0.1/stream")
        assert not safe
        assert "multicast" in reason

    def test_is_safe_url_reserved_address_blocked(self):
        """行 53：IPv4 保留地址 URL 被拒绝。"""
        safe, reason = _real_is_safe_url("http://240.0.0.1/secret")
        assert not safe
        assert "reserved" in reason

    def test_is_safe_url_link_local_blocked(self):
        """链路本地地址 URL 被拒绝（云元数据防护）。"""
        safe, reason = _real_is_safe_url("http://169.254.169.254/latest/meta-data/")
        assert not safe
        assert "link-local" in reason


# ============================================================
# 测试套件 4：IPv6 mapped IPv4 递归
# ============================================================

class TestIpv6MappedIpv4:
    """覆盖 _is_ip_blocked 中 IPv6 mapped IPv4 递归调用。"""

    def test_ipv6_mapped_loopback_blocked(self):
        """::ffff:127.0.0.1 应递归调用 _is_ip_blocked 并返回 loopback。"""
        ip = ipaddress.ip_address("::ffff:127.0.0.1")
        blocked, reason = _is_ip_blocked(ip)
        assert blocked
        assert "loopback" in reason

    def test_ipv6_mapped_private_blocked(self):
        """::ffff:10.0.0.1 应递归调用并返回 private。"""
        ip = ipaddress.ip_address("::ffff:10.0.0.1")
        blocked, reason = _is_ip_blocked(ip)
        assert blocked
        assert "private" in reason

    def test_ipv6_mapped_public_not_blocked(self):
        """::ffff:8.8.8.8 应递归调用并返回安全。"""
        ip = ipaddress.ip_address("::ffff:8.8.8.8")
        blocked, _ = _is_ip_blocked(ip)
        assert not blocked


# ============================================================
# 测试套件 5：边界情况
# ============================================================

class TestEdgeCases:
    """各种边界输入。"""

    def test_empty_url_returns_invalid(self):
        safe, reason = _real_is_safe_url("")
        assert not safe
        assert "invalid hostname" in reason

    def test_none_scheme_rejected(self):
        """无 scheme 的 URL 被拒绝。"""
        safe, reason = _real_is_safe_url("example.com/path")
        assert not safe

    def test_ftp_scheme_rejected(self):
        """ftp scheme 被拒绝。"""
        safe, reason = _real_is_safe_url("ftp://example.com/file")
        assert not safe
        assert "scheme" in reason

    def test_blacklisted_localhost_rejected(self):
        """localhost 在黑名单中被拒绝。"""
        safe, reason = _real_is_safe_url("http://localhost/admin")
        assert not safe
        assert "internal hostname" in reason

    def test_blacklisted_metadata_rejected(self):
        """metadata.google.internal 在黑名单中被拒绝。"""
        safe, reason = _real_is_safe_url("http://metadata.google.internal/computeMetadata/")
        assert not safe
        assert "internal hostname" in reason

    def test_port_in_url_works(self):
        """带端口号的 URL 正常处理（IP 形式）。"""
        safe, _ = _real_is_safe_url("http://8.8.8.8:8080/path")
        # 8.8.8.8 是公网 IP，应返回安全
        assert safe

    def test_blocked_hostname_in_blacklist_set(self):
        """验证黑名单集合内容。"""
        assert "localhost" in _BLOCKED_HOSTNAMES
        assert "metadata.google.internal" in _BLOCKED_HOSTNAMES
        assert "metadata" in _BLOCKED_HOSTNAMES
