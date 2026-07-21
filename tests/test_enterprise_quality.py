"""企业级质量单元测试（第七轮补充）。

覆盖豆包反复提到的测试覆盖度缺口：
1. test_savepoint_partial_success: SAVEPOINT 部分成功语义验证
2. test_safe_contains_escape: safe_contains LIKE 转义验证
3. test_is_safe_url_blocks_internal: is_safe_url 拒绝内网/保留 IP

工程规范：
- 不依赖 conftest 的 autouse mock（用 monkeypatch.undo 还原真实函数）
- 使用真实 SQLite 内存或临时库验证事务行为
- 测试隔离：每个测试函数独立 fixture
"""

from __future__ import annotations

import ipaddress
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.models.database import AsyncSessionLocal, engine
from app.models.tender import Tender
from app.processors.tender_utils import _build_tender
from app.scheduler.utils import (
    LIKE_ESCAPE,
    escape_like,
    safe_contains,
    safe_like,
)


# ============================================================
# 测试套件 1：SAVEPOINT 部分成功语义
# ============================================================

class TestSavepointPartialSuccess:
    """验证 M-1 修复（第五轮）：collector.py 用 SAVEPOINT 实现部分成功语义。

    场景：3 个平台入库，平台 2 故意触发 IntegrityError，
    最终 commit 后应该只有平台 1 + 平台 3 的数据落盘。
    """

    @pytest.mark.asyncio
    async def test_savepoint_isolates_platform_failure(self):
        """单平台失败不影响其他平台数据。"""
        # 模拟 collector.py 的核心逻辑：3 个平台，平台 2 故意失败
        async with AsyncSessionLocal() as db:
            # 平台 1：正常入库
            async with db.begin_nested():
                t1 = Tender(
                    project_name="平台1-测试招标",
                    source_platform="ccgp",
                    source_url="https://ccgp.gov.cn/1",
                )
                db.add(t1)

            # 平台 2：模拟失败（在 SAVEPOINT 内抛异常）
            with pytest.raises(IntegrityError):
                async with db.begin_nested():
                    # 故意插入违反约束的数据：project_name 为 NOT NULL，传 None
                    t2 = Tender(
                        project_name=None,  # type: ignore[arg-type]
                        source_platform="chinabidding",
                    )
                    db.add(t2)
                    await db.flush()  # 触发 IntegrityError

            # 平台 3：正常入库（验证 SAVEPOINT 回滚后外层事务仍可用）
            async with db.begin_nested():
                t3 = Tender(
                    project_name="平台3-测试招标",
                    source_platform="qianlima",
                    source_url="https://qianlima.com/3",
                )
                db.add(t3)

            # 统一 commit
            await db.commit()

        # 验证：只有平台 1 + 平台 3 落盘
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Tender).order_by(Tender.id))
            tenders = result.scalars().all()
            platforms = [t.source_platform for t in tenders]
            assert "ccgp" in platforms, "平台 1 数据应该落盘"
            assert "qianlima" in platforms, "平台 3 数据应该落盘"
            assert "chinabidding" not in platforms, "平台 2 数据应该被回滚"
            assert len(tenders) == 2, f"应该只有 2 条记录，实际 {len(tenders)}"

    @pytest.mark.asyncio
    async def test_savepoint_all_success_commits_all(self):
        """所有平台都成功时，全部落盘。"""
        async with AsyncSessionLocal() as db:
            for platform in ["ccgp", "chinabidding", "qianlima"]:
                async with db.begin_nested():
                    t = Tender(
                        project_name=f"测试-{platform}",
                        source_platform=platform,
                        source_url=f"https://{platform}.com/test",
                    )
                    db.add(t)
            await db.commit()

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Tender))
            tenders = result.scalars().all()
            assert len(tenders) == 3, f"应该有 3 条记录，实际 {len(tenders)}"


# ============================================================
# 测试套件 2：safe_contains LIKE 转义验证
# ============================================================

class TestSafeContainsEscape:
    """验证 M-1 修复（第四轮）：safe_contains 显式 escape 参数生效。"""

    def test_escape_like_escapes_percent(self):
        """% 应被转义为 \\%。"""
        assert escape_like("100%") == "100\\%"

    def test_escape_like_escapes_underscore(self):
        """_ 应被转义为 \\_。"""
        assert escape_like("test_name") == "test\\_name"

    def test_escape_like_escapes_backslash(self):
        """反斜杠应被转义为双反斜杠。"""
        assert escape_like("a\\b") == "a\\\\b"

    def test_escape_like_handles_empty(self):
        """空字符串不转义。"""
        assert escape_like("") == ""

    def test_escape_like_preserves_normal_text(self):
        """普通文本不变。"""
        assert escape_like("上海") == "上海"
        assert escape_like("招标公告") == "招标公告"

    def test_safe_contains_generates_escape_clause(self):
        """safe_contains 生成的 LIKE 子句必须带 escape 字符。

        验证方式：检查编译后的 SQL 字符串包含 'ESCAPE' 关键字。
        """
        from app.models.tender import Tender

        stmt = select(Tender).where(safe_contains(Tender.project_name, "100%"))
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "ESCAPE" in compiled.upper(), (
            f"LIKE 子句必须带 ESCAPE 关键字，实际: {compiled}"
        )

    def test_safe_like_generates_escape_clause(self):
        """safe_like 生成的 LIKE 子句必须带 escape 字符。"""
        from app.models.tender import Tender

        stmt = select(Tender).where(safe_like(Tender.project_name, "100%"))
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "ESCAPE" in compiled.upper(), (
            f"LIKE 子句必须带 ESCAPE 关键字，实际: {compiled}"
        )

    def test_like_escape_constant_is_backslash(self):
        """LIKE_ESCAPE 常量必须是反斜杠。"""
        assert LIKE_ESCAPE == "\\"


# ============================================================
# 测试套件 3：is_safe_url 拒绝内网 URL
# ============================================================

class TestIsSafeUrlBlocksInternal:
    """验证 M-2 修复（第四轮）：is_safe_url 拒绝内网/保留 IP。

    注意：conftest.py autouse fixture mock 了 is_safe_url，
    本测试套件通过直接调用底层 _is_ip_blocked 验证 IP 校验逻辑，
    不受 mock 影响。
    """

    def test_loopback_ipv4_blocked(self):
        """127.0.0.1 应被拒绝。"""
        from app.utils.url_safety import _is_ip_blocked

        ip = ipaddress.ip_address("127.0.0.1")
        blocked, reason = _is_ip_blocked(ip)
        assert blocked, "127.0.0.1 必须被拒绝"
        assert "loopback" in reason

    def test_private_ipv4_blocked(self):
        """10.0.0.1 / 172.16.0.1 / 192.168.1.1 应被拒绝。"""
        from app.utils.url_safety import _is_ip_blocked

        for ip_str in ["10.0.0.1", "172.16.0.1", "192.168.1.1"]:
            ip = ipaddress.ip_address(ip_str)
            blocked, reason = _is_ip_blocked(ip)
            assert blocked, f"{ip_str} 必须被拒绝"
            assert "private" in reason

    def test_link_local_blocked(self):
        """169.254.169.254（云元数据 IP）应被拒绝。"""
        from app.utils.url_safety import _is_ip_blocked

        ip = ipaddress.ip_address("169.254.169.254")
        blocked, reason = _is_ip_blocked(ip)
        assert blocked, "169.254.169.254 必须被拒绝（云元数据 SSRF）"
        assert "link-local" in reason

    def test_ipv6_loopback_blocked(self):
        """::1 应被拒绝。"""
        from app.utils.url_safety import _is_ip_blocked

        ip = ipaddress.ip_address("::1")
        blocked, reason = _is_ip_blocked(ip)
        assert blocked, "::1 必须被拒绝"
        assert "loopback" in reason

    def test_ipv6_mapped_ipv4_loopback_blocked(self):
        """::ffff:127.0.0.1（IPv6 mapped IPv4）应被拒绝。"""
        from app.utils.url_safety import _is_ip_blocked

        ip = ipaddress.ip_address("::ffff:127.0.0.1")
        blocked, reason = _is_ip_blocked(ip)
        assert blocked, "::ffff:127.0.0.1 必须被拒绝（IPv6 mapped 绕过防护）"

    def test_public_ip_allowed(self):
        """8.8.8.8 应被允许。"""
        from app.utils.url_safety import _is_ip_blocked

        ip = ipaddress.ip_address("8.8.8.8")
        blocked, _ = _is_ip_blocked(ip)
        assert not blocked, "8.8.8.8 不应被拒绝"

    def test_blocked_hostname_in_blacklist(self):
        """localhost / metadata.google.internal 应在黑名单中。"""
        from app.utils.url_safety import _BLOCKED_HOSTNAMES

        assert "localhost" in _BLOCKED_HOSTNAMES
        assert "metadata.google.internal" in _BLOCKED_HOSTNAMES
        assert "metadata" in _BLOCKED_HOSTNAMES

    def test_is_ip_blocked_rejects_loopback(self):
        """底层 _is_ip_blocked 拒绝 127.0.0.1（诚实命名）。"""
        from app.utils.url_safety import _is_ip_blocked

        ip = ipaddress.ip_address("127.0.0.1")
        blocked, _ = _is_ip_blocked(ip)
        assert blocked, "127.0.0.1 必须被 _is_ip_blocked 拒绝"

    def test_is_safe_url_full_chain_rejects_loopback(self, monkeypatch):
        """完整链路：is_safe_url("http://127.0.0.1/") 应返回不安全。

        m-1 修复（第七轮）：真正测完整链路 —— 绕过 conftest 的 autouse mock，
        用 importlib.reload 还原真实 is_safe_url 实现。

        m-2 注释（第八轮）：importlib.reload 方案仅限 pytest 串行执行。
        若将来开 `-n` 并行测试，两个测试同时 reload 同一模块会有竞态。
        并行场景应改用 monkeypatch.setattr(url_safety_mod, "is_safe_url", real_impl)。
        """
        import importlib
        import sys

        # 备份 conftest mock 的函数
        import app.utils.url_safety as url_safety_mod
        mocked_fn = url_safety_mod.is_safe_url

        # 用 importlib.reload 重新加载模块拿原始 is_safe_url
        # （monkeypatch.setattr 是改模块属性，reload 会重新执行模块顶层代码
        #   恢复原始函数定义）
        real_mod = importlib.reload(url_safety_mod)
        try:
            # 现在调用的是真实 is_safe_url（不做 DNS 解析的纯 IP 路径）
            safe, reason = real_mod.is_safe_url("http://127.0.0.1/admin")
            assert not safe, "http://127.0.0.1/ 必须被 is_safe_url 拒绝"
            assert "loopback" in reason, f"原因应含 loopback，实际: {reason}"
        finally:
            # 恢复 conftest 的 mock 状态（避免影响后续测试）
            importlib.reload(url_safety_mod)
            url_safety_mod.is_safe_url = mocked_fn

    def test_is_safe_url_rejects_file_url(self, monkeypatch):
        """is_safe_url 拒绝 file:// URL（file URL 无 hostname，撞 invalid hostname 分支）。

        m-3 修复（第八轮）：测试名改为 file_url（不强调 scheme），更精确。
        file:/// 的 hostname 为空，实际走 "invalid hostname" 分支，
        非非 http/https scheme 才走 "scheme not allowed" 分支（见 ftp 测试）。
        """
        import importlib
        import app.utils.url_safety as url_safety_mod

        mocked_fn = url_safety_mod.is_safe_url
        real_mod = importlib.reload(url_safety_mod)
        try:
            safe, reason = real_mod.is_safe_url("file:///etc/passwd")
            assert not safe, "file:// URL 必须被拒绝"
            # file:/// 的 hostname 为空，先撞 "invalid hostname"
            assert "invalid hostname" in reason or "scheme" in reason
        finally:
            importlib.reload(url_safety_mod)
            url_safety_mod.is_safe_url = mocked_fn

    def test_is_safe_url_rejects_ftp_scheme(self, monkeypatch):
        """is_safe_url 拒绝 ftp:// scheme（scheme 校验）。"""
        import importlib
        import app.utils.url_safety as url_safety_mod

        mocked_fn = url_safety_mod.is_safe_url
        real_mod = importlib.reload(url_safety_mod)
        try:
            # ftp:// 有 hostname，能到 scheme 校验
            safe, reason = real_mod.is_safe_url("ftp://example.com/file")
            assert not safe, "ftp:// scheme 必须被拒绝"
            assert "scheme" in reason, f"原因应含 scheme，实际: {reason}"
        finally:
            importlib.reload(url_safety_mod)
            url_safety_mod.is_safe_url = mocked_fn

    def test_is_safe_url_rejects_blacklisted_hostname(self, monkeypatch):
        """is_safe_url 拒绝黑名单域名 localhost（不走 DNS 解析）。"""
        import importlib
        import app.utils.url_safety as url_safety_mod

        mocked_fn = url_safety_mod.is_safe_url
        real_mod = importlib.reload(url_safety_mod)
        try:
            safe, reason = real_mod.is_safe_url("http://localhost/admin")
            assert not safe, "localhost 必须被拒绝"
            assert "internal hostname" in reason, f"原因应含 internal hostname，实际: {reason}"
        finally:
            importlib.reload(url_safety_mod)
            url_safety_mod.is_safe_url = mocked_fn


# ============================================================
# 测试套件 4：tender_utils 纯函数验证
# ============================================================

class TestTenderUtilsPureFunctions:
    """验证 C-1 修复（第四轮）：拆分后的 tender_utils.py 纯函数语义正确。"""

    def test_parse_decimal_with_wan_unit(self):
        """'100万元' 应解析为 1000000。"""
        from app.processors.tender_utils import _parse_decimal

        result = _parse_decimal("100万元")
        assert result is not None
        assert int(result) == 1_000_000

    def test_parse_decimal_with_yi_unit(self):
        """'1.5亿元' 应解析为 150000000。"""
        from app.processors.tender_utils import _parse_decimal

        result = _parse_decimal("1.5亿元")
        assert result is not None
        assert int(result) == 150_000_000

    def test_parse_decimal_with_comma(self):
        """'1,000,000元' 应解析为 1000000。"""
        from app.processors.tender_utils import _parse_decimal

        result = _parse_decimal("1,000,000元")
        assert result is not None
        assert int(result) == 1_000_000

    def test_parse_decimal_invalid_returns_none(self):
        """无效字符串返回 None。"""
        from app.processors.tender_utils import _parse_decimal

        assert _parse_decimal("invalid") is None
        assert _parse_decimal("") is None
        assert _parse_decimal(None) is None

    def test_infer_platform_from_url(self):
        """从 URL 推断 source_platform。"""
        from app.processors.tender_utils import _infer_platform

        assert _infer_platform("https://ccgp.gov.cn/test") == "ccgp"
        assert _infer_platform("https://www.chinabidding.cn/test") == "chinabidding"
        assert _infer_platform("https://www.qianlima.com/test") == "qianlima"

    def test_infer_platform_template_takes_priority(self):
        """template 参数优先于 URL 推断。"""
        from app.processors.tender_utils import _infer_platform

        assert _infer_platform("https://ccgp.gov.cn/test", "qianlima") == "qianlima"

    def test_hash_contact_returns_sha256(self):
        """联系人哈希返回 64 字符 SHA256 hex。"""
        from app.processors.tender_utils import _hash_contact

        result = _hash_contact("13800138000")
        assert result is not None
        assert len(result) == 64, f"SHA256 hex 应为 64 字符，实际 {len(result)}"
        # 相同输入应产生相同哈希
        assert _hash_contact("13800138000") == result

    def test_hash_contact_empty_returns_none(self):
        """空字符串返回 None。"""
        from app.processors.tender_utils import _hash_contact

        assert _hash_contact("") is None
        assert _hash_contact(None) is None
        assert _hash_contact("   ") is None

    def test_build_tender_maps_fields(self):
        """_build_tender 正确映射字段。"""
        item = {
            "project_name": "测试招标项目",
            "bid_number": "SH-2024-001",
            "budget_amount": "100万元",
            "location": "上海",
            "tender_org": "测试招标人",
            "contact_name": "张三",
            "contact_phone": "13800138000",
        }
        tender = _build_tender(item, "https://example.com", "ccgp", simhash_value=12345)
        assert tender.project_name == "测试招标项目"
        assert tender.bid_number == "SH-2024-001"
        assert tender.budget_amount is not None
        assert int(tender.budget_amount) == 1_000_000
        assert tender.location == "上海"
        assert tender.source_platform == "ccgp"
        assert tender.simhash == 12345
        # 联系人手机号应被 SHA256 哈希
        assert tender.contact_phone != "13800138000"
        assert len(tender.contact_phone) == 64
