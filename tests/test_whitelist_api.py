"""Whitelist admin API tests (v4.1 sec 5.2)."""

from __future__ import annotations

import pytest

from app.core.source_whitelist import source_whitelist


@pytest.fixture(autouse=True)
def _reset_whitelist():
    """Each test gets a fresh whitelist."""
    source_whitelist.reset()
    yield
    source_whitelist.reset()


class TestListWhitelist:
    """GET /admin/sources/whitelist."""

    @pytest.mark.asyncio
    async def test_list_returns_default_sources(self, client, admin_headers):
        resp = await client.get("/admin/sources/whitelist", headers=admin_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 200
        assert len(body["data"]) == 4

    @pytest.mark.asyncio
    async def test_list_filter_by_status(self, client, admin_headers):
        # Decommission one first
        await source_whitelist.decommission("ccgp.gov.cn", reason="test")
        resp = await client.get(
            "/admin/sources/whitelist?status=decommissioned",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["domain"] == "ccgp.gov.cn"

    @pytest.mark.asyncio
    async def test_list_requires_admin_secret(self, client):
        resp = await client.get("/admin/sources/whitelist")
        assert resp.status_code in (401, 403)


class TestAddWhitelistSource:
    """POST /admin/sources/whitelist."""

    @pytest.mark.asyncio
    async def test_add_new_source(self, client, admin_headers):
        resp = await client.post(
            "/admin/sources/whitelist",
            headers=admin_headers,
            json={
                "domain": "bidcenter.com.cn",
                "platform_name": "中国采购与招标网",
                "platform_type": "commercial",
                "notes": "新增商业平台",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["code"] == 201
        assert body["data"]["domain"] == "bidcenter.com.cn"

    @pytest.mark.asyncio
    async def test_add_invalid_platform_type_returns_400(self, client, admin_headers):
        resp = await client.post(
            "/admin/sources/whitelist",
            headers=admin_headers,
            json={
                "domain": "test.com",
                "platform_name": "Test",
                "platform_type": "invalid",
            },
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_add_empty_domain_returns_400(self, client, admin_headers):
        resp = await client.post(
            "/admin/sources/whitelist",
            headers=admin_headers,
            json={"domain": "", "platform_name": "Test"},
        )
        assert resp.status_code == 400


class TestDecommissionSource:
    """POST /admin/sources/whitelist/{domain}/decommission."""

    @pytest.mark.asyncio
    async def test_decommission_existing_source(self, client, admin_headers):
        resp = await client.post(
            "/admin/sources/whitelist/ccgp.gov.cn/decommission",
            headers=admin_headers,
            json={"reason": "平台条款变更"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["status"] == "decommissioned"
        assert body["data"]["decommissioned_reason"] == "平台条款变更"

    @pytest.mark.asyncio
    async def test_decommission_nonexistent_returns_404(self, client, admin_headers):
        resp = await client.post(
            "/admin/sources/whitelist/nonexistent.com/decommission",
            headers=admin_headers,
            json={"reason": "test"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_decommission_empty_reason_returns_422(self, client, admin_headers):
        # Pydantic min_length=1 -> 422 validation error
        resp = await client.post(
            "/admin/sources/whitelist/ccgp.gov.cn/decommission",
            headers=admin_headers,
            json={"reason": ""},
        )
        assert resp.status_code == 422


class TestRecommissionSource:
    """POST /admin/sources/whitelist/{domain}/recommission."""

    @pytest.mark.asyncio
    async def test_recommission_after_decommission(self, client, admin_headers):
        await source_whitelist.decommission("ccgp.gov.cn", reason="临时下架")
        resp = await client.post(
            "/admin/sources/whitelist/ccgp.gov.cn/recommission",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["status"] == "active"
        assert body["data"]["decommissioned_reason"] is None

    @pytest.mark.asyncio
    async def test_recommission_nonexistent_returns_404(self, client, admin_headers):
        resp = await client.post(
            "/admin/sources/whitelist/nonexistent.com/recommission",
            headers=admin_headers,
        )
        assert resp.status_code == 404
