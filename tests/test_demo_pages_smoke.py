# -*- coding: utf-8 -*-
"""v4.1 Demo page smoke tests."""
from __future__ import annotations
import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app


@pytest.fixture(autouse=True)
async def _client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
class TestWorkbenchPage:
    async def test_workbench_returns_200(self, _client):
        resp = await _client.get("/ui")
        assert resp.status_code == 200

    async def test_workbench_has_kpi_ids(self, _client):
        resp = await _client.get("/ui")
        html = resp.text
        assert "kpiTotalTenders" in html
        assert "kpiFieldPrecision" in html
        assert "kpiEvidenceCount" in html
        assert "kpiOrgCount" in html


@pytest.mark.asyncio
class TestSearchPage:
    async def test_search_returns_200(self, _client):
        resp = await _client.get("/static/search.html")
        assert resp.status_code == 200

    async def test_search_has_amount_unit_parsing(self, _client):
        resp = await _client.get("/static/search.html")
        html = resp.text
        assert "10000" in html or "100000000" in html


@pytest.mark.asyncio
class TestNoticeDetailPage:
    async def test_detail_returns_200(self, _client):
        resp = await _client.get("/static/notice_detail.html")
        assert resp.status_code == 200

    async def test_detail_uses_real_api(self, _client):
        resp = await _client.get("/static/notice_detail.html")
        html = resp.text
        assert "/api/real/tenders/" in html
        # old API paths should not appear in fetch calls
        assert html.count('fetch("/api/notices/') == 0
        assert html.count("fetch('/api/notices/") == 0
        assert html.count('fetch("/api/fields/') == 0


@pytest.mark.asyncio
class TestQualityDashboardPage:
    async def test_quality_returns_200(self, _client):
        resp = await _client.get("/static/quality_dashboard.html")
        assert resp.status_code == 200

    async def test_quality_null_fpr_exists(self, _client):
        """D 组 unjustified_rate 应为 0.0（完整流水线无依据率降为 0），null_fpr 数据应存在。"""
        resp = await _client.get("/static/quality_dashboard.html")
        html = resp.text
        assert 'null_fpr' in html
        assert 'unjustified_rate:0.0' in html or 'unjustified_rate: 0.0' in html

    async def test_quality_fetches_real_data(self, _client):
        resp = await _client.get("/static/quality_dashboard.html")
        assert "/api/stats/quality" in resp.text

    async def test_quality_ablation_json_available(self, _client):
        resp = await _client.get("/static/data/ablation_v41.json")
        assert resp.status_code == 200


@pytest.mark.asyncio
class TestVersionHistoryPage:
    async def test_versions_returns_200(self, _client):
        resp = await _client.get("/static/version_history.html")
        assert resp.status_code == 200

    async def test_versions_uses_real_api(self, _client):
        resp = await _client.get("/static/version_history.html")
        html = resp.text
        assert "/api/real/tenders/" in html
        assert html.count("fetch('/api/notices/") == 0
        assert html.count("fetch('/api/sources/") == 0
        assert html.count("fetch('/api/demo/sources/") == 0
