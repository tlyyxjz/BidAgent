"""URL 安全校验：防 SSRF。

M-7 修复：从 attachment_downloader.py 抽出复用，让 scraper.scrape() 也调用，
避免抓取内网地址（127.0.0.1、169.254.169.254 云元数据等）。
M-2 修复（第四轮）：增加 DNS 解析校验，防 localtest.me / nip.io 绕过。

工程规范：
- 纯函数（同步），调用方在 async 上下文用 asyncio.to_thread 包裹
- 拒绝内网/回环/链路本地/保留/组播 IP
- 拒绝非 http/https scheme
- 拒绝明显的内网域名（localhost / metadata.google.internal）
- M-2：对所有域名做 DNS 解析，校验解析到的所有 IP
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

# 已知的内网域名黑名单（即使 DNS 解析也要拦截）
_BLOCKED_HOSTNAMES = frozenset({
    "localhost",
    "metadata.google.internal",
    "metadata",
    "ip-ranges.amazonaws.com",  # 防御性
})


def _is_ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> tuple[bool, str]:
    """校验 IP 对象是否为内网/保留地址。

    m-2 修复（第六轮）：重构签名接收 ip 对象（而非字符串），避免外层 + 内层
    两次 ipaddress.ip_address 解析。调用方负责解析。

    m-3 修复（第七轮）：调整判断顺序 —— 更具体的类别优先（loopback / link-local
    优先于 private）。Python 3.13 的 ipaddress 模块对 127.0.0.1 同时返回
    is_private=True 和 is_loopback=True，先判断 is_loopback 才能给出精确日志。
    """
    # IPv6 mapped IPv4（如 ::ffff:127.0.0.1）先解包再校验
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _is_ip_blocked(ip.ipv4_mapped)

    # 顺序：更具体的类别优先（loopback / link-local / multicast / reserved / private）
    if ip.is_loopback:
        return True, f"loopback ip blocked: {ip}"
    if ip.is_link_local:
        return True, f"link-local ip blocked: {ip}"
    if ip.is_multicast:
        return True, f"multicast ip blocked: {ip}"
    if ip.is_reserved:
        return True, f"reserved ip blocked: {ip}"
    if ip.is_private:
        return True, f"private ip blocked: {ip}"
    return False, ""


def _check_dns_records(hostname: str) -> tuple[bool, str]:
    """对域名做 DNS 解析，校验所有解析到的 IP。

    M-2 修复：防 localtest.me / nip.io / xip.io 等公共 DNS 解析服务绕过。
    """
    try:
        addrs = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        return False, f"dns resolve failed: {hostname} ({exc})"
    except Exception as exc:  # noqa: BLE001
        return False, f"dns resolve error: {hostname} ({exc})"

    if not addrs:
        return False, f"dns no records: {hostname}"

    for addr in addrs:
        ip_str = addr[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        blocked, reason = _is_ip_blocked(ip)
        if blocked:
            return True, f"dns resolved to {reason}: {hostname} -> {ip}"

    return False, ""


def is_safe_url(url: str) -> tuple[bool, str]:
    """防 SSRF：拒绝内网/回环/链路本地地址。

    Args:
        url: 待校验的 URL

    Returns:
        (is_safe, reason): is_safe=True 时 reason 为空，否则 reason 说明拒绝原因

    注意：
        - 同步函数，内部会做 DNS 解析（socket.getaddrinfo）
        - 在 async 上下文中调用时建议用 asyncio.to_thread 包裹，避免阻塞事件循环
        - TOCTOU 风险：DNS 解析时安全，实际请求时 DNS rebinding 到内网，
          本函数无法防范。生产环境应在 Playwright page.route 回调中再次校验。
    """
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return False, "invalid hostname"

        # 仅允许 http/https
        if parsed.scheme not in ("http", "https"):
            return False, f"scheme not allowed: {parsed.scheme}"

        hostname_lower = hostname.lower()

        # 已知黑名单域名
        if hostname_lower in _BLOCKED_HOSTNAMES:
            return False, f"internal hostname blocked: {hostname_lower}"

        # m-3 修复（第五轮）：合并 IP 检测逻辑，避免两次 ip_address 解析
        # m-2 修复（第六轮）：_is_ip_blocked 接收 ip 对象，外层解析一次传入
        # 先判断是否为 IP 形式，是 IP 就直接校验，是域名就走 DNS 解析
        try:
            ip_obj = ipaddress.ip_address(hostname_lower)
            # IP 形式：一次解析，传入 _is_ip_blocked
            blocked, reason = _is_ip_blocked(ip_obj)
            if blocked:
                return False, reason
        except ValueError:
            # 域名形式：做 DNS 解析后校验所有 IP
            blocked, reason = _check_dns_records(hostname_lower)
            if blocked:
                return False, reason

        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"url parse error: {exc}"


async def is_safe_url_async(url: str) -> tuple[bool, str]:
    """is_safe_url 的 async 版本，用 asyncio.to_thread 包装 DNS 解析。

    供 async 调用方使用（如 scraper.py 的 page.route 回调），避免阻塞事件循环。
    """
    return await asyncio.to_thread(is_safe_url, url)
