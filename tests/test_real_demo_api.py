# -*- coding: utf-8 -*-
"""v4.1 /api/real/tenders/* endpoint tests."""
from __future__ import annotations
import pytest
from datetime import datetime, timedelta
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.models.database import AsyncSessionLocal, Base, engine
from app.models.tender import Tender


@pytest.fixture(autouse=True)
async def _setup_db():
    from pathlib import Path
    Path("data").mkdir(parents=True, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as db:
        t = Tender(
            project_name="test project",
            bid_number="TEST-2026-001",
            budget_amount=1000000,
            location="shanghai",
            publish_time=datetime(2026, 7, 1),
            deadline=datetime(2026, 8, 1),
            tender_org="test purchaser",
            agency="test agency",
            notice_type="tender",
            source_platform="ccgp",
            source_url="https://www.ccgp.gov.cn/test/1",
            core_content="test content",
        )
        db.add(t)
        await db.commit()
        await db.refresh(t)
        tid = t.id
    yield tid
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
class TestRealTendersDetail:
    async def test_detail_returns_200(self, _setup_db):
        tid = _setup_db
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(f"/api/real/tenders/{tid}/detail")
        assert resp.status_code == 200
        assert resp.json()["code"] == 200

    async def test_detail_has_required_fields(self, _setup_db):
        tid = _setup_db
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(f"/api/real/tenders/{tid}/detail")
        d = resp.json()["data"]
        assert "clean_raw_text" in d
        assert "tender_id" in d
        assert "project_name" in d
        assert "fields" in d
        assert isinstance(d["fields"], list)

    async def test_detail_not_found(self, _setup_db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/real/tenders/99999/detail")
        assert resp.json()["code"] != 200


@pytest.mark.asyncio
class TestRealTendersVersions:
    async def test_versions_returns_200(self, _setup_db):
        tid = _setup_db
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(f"/api/real/tenders/{tid}/versions")
        assert resp.status_code == 200
        assert resp.json()["code"] == 200

    async def test_versions_has_stats(self, _setup_db):
        tid = _setup_db
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(f"/api/real/tenders/{tid}/versions")
        d = resp.json()["data"]
        assert "versions" in d
        assert "stats" in d

    async def test_versions_not_found(self, _setup_db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/real/tenders/99999/versions")
        assert resp.json()["code"] != 200


@pytest.mark.asyncio
class TestRealTendersOrganization:
    async def test_org_returns_200(self, _setup_db):
        tid = _setup_db
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(f"/api/real/tenders/{tid}/organization")
        assert resp.status_code == 200
        assert resp.json()["code"] == 200

    async def test_org_not_found(self, _setup_db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/real/tenders/99999/organization")
        assert resp.json()["code"] != 200


@pytest.mark.asyncio
class TestRealTendersList:
    async def test_list_returns_200(self, _setup_db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/real/tenders")
        assert resp.status_code == 200
        assert resp.json()["code"] == 200

    async def test_list_has_tenders(self, _setup_db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/real/tenders")
        d = resp.json()["data"]
        assert "tenders" in d
        assert len(d["tenders"]) >= 1
