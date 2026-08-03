"""source_whitelist.py unit tests (v4.1 sec 5.2).

Covers:
- Default whitelist loads 4 platforms (ccgp/ggzy/chinabidding/qianlima)
- is_allowed() accepts whitelisted domains and rejects unknown
- Subdomain matching (www.ccgp.gov.cn matches ccgp.gov.cn)
- decommission() blocks further scraping
- recommission() restores access
- add_source() / list_sources() / get_entry()
- Error cases: invalid domain, invalid platform_type, empty reason
- URL normalization (scheme/port/case)
"""

from __future__ import annotations

import pytest

from app.core.source_whitelist import SourceWhitelist, WhitelistEntry


@pytest.fixture
def wl() -> SourceWhitelist:
    """Fresh whitelist instance for each test (isolated from module singleton)."""
    instance = SourceWhitelist()
    instance.reset()
    return instance


class TestDefaultWhitelist:
    """Default whitelist loading tests."""

    def test_default_whitelist_has_4_platforms(self, wl: SourceWhitelist) -> None:
        """Default whitelist must include 4 platforms per v4.1 sec 5.3."""
        sources = wl.list_sources()
        assert len(sources) == 4

    def test_default_includes_ccgp(self, wl: SourceWhitelist) -> None:
        sources = wl.list_sources()
        domains = [s["domain"] for s in sources]
        assert "ccgp.gov.cn" in domains

    def test_default_includes_ggzy(self, wl: SourceWhitelist) -> None:
        sources = wl.list_sources()
        domains = [s["domain"] for s in sources]
        assert "ggzy.gov.cn" in domains

    def test_default_includes_chinabidding(self, wl: SourceWhitelist) -> None:
        sources = wl.list_sources()
        domains = [s["domain"] for s in sources]
        assert "chinabidding.cn" in domains

    def test_default_includes_qianlima(self, wl: SourceWhitelist) -> None:
        sources = wl.list_sources()
        domains = [s["domain"] for s in sources]
        assert "qianlima.com" in domains

    def test_ccgp_is_government_type(self, wl: SourceWhitelist) -> None:
        entry = wl.get_entry("ccgp.gov.cn")
        assert entry is not None
        assert entry.platform_type == "government"

    def test_qianlima_is_commercial_type(self, wl: SourceWhitelist) -> None:
        entry = wl.get_entry("qianlima.com")
        assert entry is not None
        assert entry.platform_type == "commercial"

    def test_all_default_entries_are_active(self, wl: SourceWhitelist) -> None:
        sources = wl.list_sources()
        assert all(s["status"] == "active" for s in sources)


class TestIsAllowed:
    """is_allowed() validation tests."""

    def test_allows_ccgp_url(self, wl: SourceWhitelist) -> None:
        allowed, reason = wl.is_allowed("https://www.ccgp.gov.cn/search")
        assert allowed is True
        assert reason == ""

    def test_allows_ggzy_url(self, wl: SourceWhitelist) -> None:
        allowed, _ = wl.is_allowed("https://ggzy.gov.cn/news/123")
        assert allowed is True

    def test_allows_qianlima_url(self, wl: SourceWhitelist) -> None:
        allowed, _ = wl.is_allowed("https://vip.qianlima.com/login.html")
        assert allowed is True

    def test_rejects_unknown_domain(self, wl: SourceWhitelist) -> None:
        allowed, reason = wl.is_allowed("https://example.com/page")
        assert allowed is False
        assert "不在白名单" in reason

    def test_rejects_empty_url(self, wl: SourceWhitelist) -> None:
        allowed, reason = wl.is_allowed("")
        assert allowed is False
        assert "解析失败" in reason

    def test_rejects_none_url(self, wl: SourceWhitelist) -> None:
        allowed, reason = wl.is_allowed(None)
        assert allowed is False
        assert "解析失败" in reason

    def test_subdomain_matches_parent(self, wl: SourceWhitelist) -> None:
        """www.ccgp.gov.cn should match ccgp.gov.cn whitelist entry."""
        allowed, _ = wl.is_allowed("https://www.ccgp.gov.cn/")
        assert allowed is True

    def test_subdomain_vip_qianlima_matches(self, wl: SourceWhitelist) -> None:
        allowed, _ = wl.is_allowed("https://vip.qianlima.com/")
        assert allowed is True

    def test_case_insensitive_domain(self, wl: SourceWhitelist) -> None:
        """CCGP.GOV.CN should match ccgp.gov.cn."""
        allowed, _ = wl.is_allowed("https://CCGP.GOV.CN/")
        assert allowed is True

    def test_url_with_port_matches(self, wl: SourceWhitelist) -> None:
        allowed, _ = wl.is_allowed("https://ccgp.gov.cn:443/")
        assert allowed is True

    def test_bare_domain_allowed(self, wl: SourceWhitelist) -> None:
        allowed, _ = wl.is_allowed("ccgp.gov.cn")
        assert allowed is True

    def test_bare_domain_with_www_prefix(self, wl: SourceWhitelist) -> None:
        allowed, _ = wl.is_allowed("www.ccgp.gov.cn")
        assert allowed is True

    def test_rejects_lookalike_domain(self, wl: SourceWhitelist) -> None:
        """ccgp.gov.cn.evil.com must NOT match ccgp.gov.cn."""
        allowed, _ = wl.is_allowed("https://ccgp.gov.cn.evil.com/")
        assert allowed is False

    def test_rejects_url_with_path_only(self, wl: SourceWhitelist) -> None:
        allowed, reason = wl.is_allowed("/relative/path")
        assert allowed is False


class TestDecommission:
    """decommission() tests."""

    @pytest.mark.asyncio
    async def test_decommission_blocks_domain(self, wl: SourceWhitelist) -> None:
        await wl.decommission("ccgp.gov.cn", reason="平台条款变更，暂停采集")
        allowed, reason = wl.is_allowed("https://www.ccgp.gov.cn/")
        assert allowed is False
        assert "已下架" in reason
        assert "平台条款变更" in reason

    @pytest.mark.asyncio
    async def test_decommission_records_timestamp(self, wl: SourceWhitelist) -> None:
        await wl.decommission("chinabidding.cn", reason="测试下架")
        entry = wl.get_entry("chinabidding.cn")
        assert entry is not None
        assert entry.status == "decommissioned"
        assert entry.decommissioned_at is not None
        assert entry.decommissioned_at != ""

    @pytest.mark.asyncio
    async def test_decommission_other_domains_unaffected(
        self, wl: SourceWhitelist
    ) -> None:
        """Decommissioning one domain must not affect others."""
        await wl.decommission("ccgp.gov.cn", reason="孤立下架")
        # ggzy 仍然可用
        allowed, _ = wl.is_allowed("https://ggzy.gov.cn/")
        assert allowed is True

    @pytest.mark.asyncio
    async def test_decommission_unknown_domain_raises(
        self, wl: SourceWhitelist
    ) -> None:
        with pytest.raises(KeyError):
            await wl.decommission("nonexistent.com", reason="不存在的域名")

    @pytest.mark.asyncio
    async def test_decommission_empty_reason_raises(
        self, wl: SourceWhitelist
    ) -> None:
        with pytest.raises(ValueError, match="下架原因必填"):
            await wl.decommission("ccgp.gov.cn", reason="")

    @pytest.mark.asyncio
    async def test_decommission_whitespace_reason_raises(
        self, wl: SourceWhitelist
    ) -> None:
        with pytest.raises(ValueError, match="下架原因必填"):
            await wl.decommission("ccgp.gov.cn", reason="   ")

    @pytest.mark.asyncio
    async def test_decommission_supports_subdomain_input(
        self, wl: SourceWhitelist
    ) -> None:
        """Decommission via subdomain URL should also work."""
        await wl.decommission("https://www.ccgp.gov.cn/", reason="通过子域下架")
        entry = wl.get_entry("ccgp.gov.cn")
        assert entry is not None
        assert entry.status == "decommissioned"


class TestRecommission:
    """recommission() tests."""

    @pytest.mark.asyncio
    async def test_recommission_restores_access(self, wl: SourceWhitelist) -> None:
        await wl.decommission("ccgp.gov.cn", reason="临时下架")
        await wl.recommission("ccgp.gov.cn")
        allowed, _ = wl.is_allowed("https://ccgp.gov.cn/")
        assert allowed is True

    @pytest.mark.asyncio
    async def test_recommission_clears_reason(self, wl: SourceWhitelist) -> None:
        await wl.decommission("ccgp.gov.cn", reason="临时下架")
        await wl.recommission("ccgp.gov.cn")
        entry = wl.get_entry("ccgp.gov.cn")
        assert entry is not None
        assert entry.status == "active"
        assert entry.decommissioned_reason is None
        assert entry.decommissioned_at is None

    @pytest.mark.asyncio
    async def test_recommission_active_is_idempotent(
        self, wl: SourceWhitelist
    ) -> None:
        """Recommissioning an already-active source is a no-op."""
        entry_before = wl.get_entry("ccgp.gov.cn")
        await wl.recommission("ccgp.gov.cn")
        entry_after = wl.get_entry("ccgp.gov.cn")
        assert entry_after is not None
        assert entry_after.status == "active"

    @pytest.mark.asyncio
    async def test_recommission_unknown_domain_raises(
        self, wl: SourceWhitelist
    ) -> None:
        with pytest.raises(KeyError):
            await wl.recommission("nonexistent.com")


class TestAddSource:
    """add_source() tests."""

    @pytest.mark.asyncio
    async def test_add_new_source(self, wl: SourceWhitelist) -> None:
        entry = await wl.add_source(
            "bidcenter.com.cn",
            platform_name="中国采购与招标网",
            platform_type="commercial",
            notes="新增商业平台",
        )
        assert entry.domain == "bidcenter.com.cn"
        assert entry.platform_name == "中国采购与招标网"
        # 新增后即可采集
        allowed, _ = wl.is_allowed("https://www.bidcenter.com.cn/")
        assert allowed is True

    @pytest.mark.asyncio
    async def test_add_source_invalid_platform_type_raises(
        self, wl: SourceWhitelist
    ) -> None:
        with pytest.raises(ValueError, match="非法 platform_type"):
            await wl.add_source(
                "test.com",
                platform_name="Test",
                platform_type="invalid_type",
            )

    @pytest.mark.asyncio
    async def test_add_source_invalid_domain_raises(
        self, wl: SourceWhitelist
    ) -> None:
        with pytest.raises(ValueError, match="非法域名"):
            await wl.add_source("", platform_name="Test")

    @pytest.mark.asyncio
    async def test_add_existing_source_updates_metadata(
        self, wl: SourceWhitelist
    ) -> None:
        await wl.add_source(
            "ccgp.gov.cn",
            platform_name="更新后的名称",
            platform_type="government",
            notes="更新备注",
        )
        entry = wl.get_entry("ccgp.gov.cn")
        assert entry is not None
        assert entry.platform_name == "更新后的名称"
        assert entry.notes == "更新备注"

    @pytest.mark.asyncio
    async def test_add_source_normalizes_domain(self, wl: SourceWhitelist) -> None:
        """add_source should normalize 'https://www.test.com/path' to 'test.com'."""
        await wl.add_source(
            "https://www.test-add.com/path",
            platform_name="Test",
        )
        entry = wl.get_entry("test-add.com")
        assert entry is not None
        assert entry.domain == "test-add.com"


class TestListSources:
    """list_sources() tests."""

    def test_list_returns_all_sources(self, wl: SourceWhitelist) -> None:
        sources = wl.list_sources()
        assert len(sources) == 4

    def test_list_filter_active(self, wl: SourceWhitelist) -> None:
        sources = wl.list_sources(status="active")
        assert len(sources) == 4
        assert all(s["status"] == "active" for s in sources)

    @pytest.mark.asyncio
    async def test_list_filter_decommissioned(self, wl: SourceWhitelist) -> None:
        await wl.decommission("ccgp.gov.cn", reason="测试")
        decommissioned = wl.list_sources(status="decommissioned")
        assert len(decommissioned) == 1
        assert decommissioned[0]["domain"] == "ccgp.gov.cn"

        active = wl.list_sources(status="active")
        assert len(active) == 3

    def test_list_sorted_by_domain(self, wl: SourceWhitelist) -> None:
        sources = wl.list_sources()
        domains = [s["domain"] for s in sources]
        assert domains == sorted(domains)

    def test_list_returns_dict_with_required_fields(
        self, wl: SourceWhitelist
    ) -> None:
        sources = wl.list_sources()
        required = {
            "domain",
            "platform_name",
            "platform_type",
            "status",
            "decommissioned_reason",
            "decommissioned_at",
            "added_at",
            "notes",
        }
        for s in sources:
            assert required.issubset(s.keys()), f"Missing fields in {s}"

    def test_list_invalid_status_filter_returns_empty(
        self, wl: SourceWhitelist
    ) -> None:
        sources = wl.list_sources(status="invalid_status")
        assert len(sources) == 0


class TestGetEntry:
    """get_entry() tests."""

    def test_get_existing_entry(self, wl: SourceWhitelist) -> None:
        entry = wl.get_entry("ccgp.gov.cn")
        assert entry is not None
        assert entry.platform_name == "中国政府采购网"

    def test_get_nonexistent_entry(self, wl: SourceWhitelist) -> None:
        entry = wl.get_entry("nonexistent.com")
        assert entry is None

    def test_get_entry_by_subdomain(self, wl: SourceWhitelist) -> None:
        entry = wl.get_entry("www.ccgp.gov.cn")
        assert entry is not None
        assert entry.domain == "ccgp.gov.cn"


class TestReset:
    """reset() tests."""

    @pytest.mark.asyncio
    async def test_reset_clears_decommission(self, wl: SourceWhitelist) -> None:
        await wl.decommission("ccgp.gov.cn", reason="测试")
        wl.reset()
        entry = wl.get_entry("ccgp.gov.cn")
        assert entry is not None
        assert entry.status == "active"
        assert entry.decommissioned_reason is None

    @pytest.mark.asyncio
    async def test_reset_clears_added_sources(self, wl: SourceWhitelist) -> None:
        await wl.add_source("newdomain.com", platform_name="New")
        assert wl.get_entry("newdomain.com") is not None
        wl.reset()
        assert wl.get_entry("newdomain.com") is None
        assert len(wl.list_sources()) == 4


class TestWhitelistEntry:
    """WhitelistEntry dataclass tests."""

    def test_to_dict_has_all_fields(self) -> None:
        entry = WhitelistEntry(
            domain="test.com",
            platform_name="Test",
        )
        d = entry.to_dict()
        assert d["domain"] == "test.com"
        assert d["platform_name"] == "Test"
        assert d["platform_type"] == "commercial"
        assert d["status"] == "active"
        assert d["decommissioned_reason"] is None
        assert d["decommissioned_at"] is None
        assert d["added_at"] != ""
        assert d["notes"] == ""

    def test_default_status_is_active(self) -> None:
        entry = WhitelistEntry(domain="x.com", platform_name="X")
        assert entry.status == "active"

    def test_default_platform_type_is_commercial(self) -> None:
        entry = WhitelistEntry(domain="x.com", platform_name="X")
        assert entry.platform_type == "commercial"