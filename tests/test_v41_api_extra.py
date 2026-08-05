"""v4.1 API 补充测试：v41_api.py + v41_extract.py 未覆盖行.

覆盖目标：
- v41_api.py: industry_category 过滤、各类 400/404 异常路径、
  _build_versions_payload_from_tender updated_at > created_at 分支
- v41_extract.py: _extraction_to_payload 多输入、_resolve_org_name、
  _collect_org_records、get_organization profile 构建、
  worker 非法 tender_id / 非法协议 source_url
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.models.database import AsyncSessionLocal, Base, engine
from app.models.evidence import Evidence, ExtractedField, FieldEvidenceLink
from app.models.tender import Tender
from app.models.tender_project import (
    NoticeSource,
    NoticeVersion,
    TenderNotice,
    TenderProject,
)

# 显式导入所有模型，确保 Base.metadata 注册全部表
from app.models import (  # noqa: F401
    NoticeParticipant,
    ProjectIdentifier,
    PushLog,
    Subscription,
)
from app.models.organization import Organization  # noqa: F401


# ==== fixtures（覆盖 conftest 同名 fixture）====


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# BE-C1: 覆盖 verify_api_key 依赖，使测试无需传 Bearer token
@pytest.fixture(autouse=True)
async def _override_v41_auth():
    from app.api.auth import verify_api_key
    from app.main import app
    from app.models.user import ApiKey, User

    async def _mock_verify():
        user = User(id=1, email="v41-extra@test.com", is_active=True)
        api_key = ApiKey(id=1, user_id=1, is_active=True)
        return user, api_key, "test-key"

    app.dependency_overrides[verify_api_key] = _mock_verify
    yield
    app.dependency_overrides.pop(verify_api_key, None)


# ==== seeding 辅助 ====


async def _seed_tender(**kwargs) -> int:
    defaults = dict(
        project_name="测试医疗设备采购项目",
        bid_number="TEST-2026-001",
        notice_type="tender",
        tender_org="北京大学第三医院",
        win_company="测试中标公司A",
        agency="北京市政府采购中心",
        source_platform="ccgp",
        source_url="http://example.gov.cn/test/001",
        publish_time=datetime(2026, 7, 20),
        win_amount=Decimal("12000000.00"),
        location="北京",
    )
    defaults.update(kwargs)
    async with AsyncSessionLocal() as db:
        t = Tender(**defaults)
        db.add(t)
        await db.commit()
        await db.refresh(t)
        return t.id


async def _seed_field_with_evidence(tender_id: int) -> tuple[int, int]:
    async with AsyncSessionLocal() as db:
        ev = Evidence(
            tender_id=tender_id,
            evidence_text="1200万元",
            raw_start=10,
            raw_end=16,
            match_method="exact",
            confidence=95,
            verified=True,
        )
        db.add(ev)
        await db.flush()
        f = ExtractedField(
            tender_id=tender_id,
            field_name="amount",
            field_status="present",
            raw_value="12000000.00",
            normalized_value="12000000.00",
            amount_type="budget",
            support_level="direct",
            primary_evidence_id=ev.id,
        )
        db.add(f)
        await db.flush()
        link = FieldEvidenceLink(
            field_id=f.id,
            evidence_id=ev.id,
            evidence_role="primary",
            sequence=0,
            is_required=True,
        )
        db.add(link)
        await db.commit()
        return f.id, ev.id


async def _seed_winner_field(tender_id: int, winner_name: str = "测试中标公司A"):
    """插入 ExtractedField(field_name=winner_name) 以供组织画像测试。"""
    async with AsyncSessionLocal() as db:
        f = ExtractedField(
            tender_id=tender_id,
            field_name="winner_name",
            field_status="present",
            raw_value=winner_name,
            normalized_value=winner_name,
            support_level="direct",
        )
        db.add(f)
        await db.commit()
        await db.refresh(f)
        return f.id


# ========== v41_api.py 测试 ==========


class TestProjectsSearchExtra:
    """GET /api/projects/search 补充。"""

    async def test_search_with_industry_category(self):
        """industry_category 过滤（line 61）。"""
        await _seed_tender(notice_type="tender", source_url="http://x.gov.cn/1")
        await _seed_tender(notice_type="award", source_url="http://x.gov.cn/2")
        async with _client() as ac:
            resp = await ac.get(
                "/api/projects/search",
                params={"industry_category": "tender"},
            )
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["total"] == 1
        assert body["data"]["items"][0]["notice_type"] == "tender"


class TestGetNoticeExtra:
    """GET /api/notices/{notice_id} 补充。"""

    async def test_invalid_notice_id_returns_400(self):
        """非法 notice_id → 400（line 109）。"""
        async with _client() as ac:
            resp = await ac.get("/api/notices/abc")
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == 400
        assert "非法" in body["msg"]

    async def test_notice_not_found_returns_404(self):
        """不存在的 notice_id → 404（lines 111-113）。"""
        async with _client() as ac:
            resp = await ac.get("/api/notices/99999")
        assert resp.status_code == 404
        assert resp.json()["code"] == 404


class TestGetNoticeSourcesExtra:
    """GET /api/notices/{notice_id}/sources 补充。"""

    async def test_invalid_notice_id_returns_400(self):
        """非法 notice_id → 400（line 137）。"""
        async with _client() as ac:
            resp = await ac.get("/api/notices/abc/sources")
        assert resp.status_code == 400

    async def test_notice_not_found_returns_404(self):
        """不存在的 notice_id → 404（lines 139-140）。"""
        async with _client() as ac:
            resp = await ac.get("/api/notices/99999/sources")
        assert resp.status_code == 404


class TestGetNoticeParticipantsExtra:
    """GET /api/notices/{notice_id}/participants 补充。"""

    async def test_invalid_notice_id_returns_400(self):
        """非法 notice_id → 400（line 201）。"""
        async with _client() as ac:
            resp = await ac.get("/api/notices/abc/participants")
        assert resp.status_code == 400

    async def test_notice_not_found_returns_404(self):
        """不存在的 notice_id → 404（lines 203-204）。"""
        async with _client() as ac:
            resp = await ac.get("/api/notices/99999/participants")
        assert resp.status_code == 404


class TestGetSourceVersionsExtra:
    """GET /api/sources/{source_id}/versions 补充。"""

    async def test_invalid_source_id_format_returns_400(self):
        """非 ULID 非 src_ 格式 → 400（line 243）。"""
        async with _client() as ac:
            resp = await ac.get("/api/sources/badformat/versions")
        assert resp.status_code == 400
        assert resp.json()["code"] == 400

    async def test_src_non_int_id_returns_400(self):
        """src_ 前缀但非整数 ID → 400（line 246）。"""
        async with _client() as ac:
            resp = await ac.get("/api/sources/src_abc/versions")
        assert resp.status_code == 400

    async def test_src_tender_not_found_returns_404(self):
        """src_{tender_id} 但 tender 不存在 → 404（lines 248-249）。"""
        async with _client() as ac:
            resp = await ac.get("/api/sources/src_99999/versions")
        assert resp.status_code == 404

    async def test_versions_with_updated_at(self):
        """Tender 有 updated_at > created_at → 版本数=2（line 304）。"""
        tid = await _seed_tender(source_url="http://example.gov.cn/updated/001")
        # 更新 tender 以触发 updated_at > created_at
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Tender).where(Tender.id == tid))
            tender = result.scalar_one()
            tender.project_name = "更新后的项目名称"
            # 显式拉开 updated_at（不依赖真实时钟，避免与容差阈值耦合）
            tender.updated_at = tender.created_at + timedelta(hours=1)
            await db.commit()
        async with _client() as ac:
            resp = await ac.get(f"/api/sources/src_{tid}/versions")
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["total_versions"] == 2
        assert body["data"]["data_source"] == "tender_fallback"


class TestGetFieldExtra:
    """GET /api/fields/{field_id} 补充。"""

    async def test_invalid_field_id_returns_400(self):
        """非法 field_id（无下划线）→ 400（lines 329-330）。"""
        async with _client() as ac:
            resp = await ac.get("/api/fields/noundercore")
        assert resp.status_code == 400
        assert resp.json()["code"] == 400

    async def test_field_not_found_returns_404(self):
        """字段不存在 → 404（lines 336-340）。"""
        async with _client() as ac:
            resp = await ac.get("/api/fields/999_nonexistent")
        assert resp.status_code == 404


# ========== v41_extract.py 测试 ==========


class TestExtractionToPayload:
    """_extraction_to_payload 直接调用（lines 332-354）。"""

    def test_none_returns_empty(self):
        from app.api.v41_extract import _extraction_to_payload

        assert _extraction_to_payload(None) == []

    def test_empty_fields_returns_empty(self):
        from app.api.v41_extract import _extraction_to_payload

        assert _extraction_to_payload({"fields": []}) == []
        assert _extraction_to_payload({"fields": None}) == []

    def test_dict_fields(self):
        from app.api.v41_extract import _extraction_to_payload

        extraction = {
            "fields": [
                {
                    "field_name": "amount",
                    "raw_value": "100万",
                    "normalized_value": "1000000",
                    "amount_type": "budget",
                    "support_level": "direct",
                    "candidate_evidences": [{"text": "预算100万"}],
                }
            ]
        }
        result = _extraction_to_payload(extraction)
        assert len(result) == 1
        assert result[0]["field_name"] == "amount"
        assert result[0]["raw_value"] == "100万"
        assert result[0]["support_level"] == "direct"
        assert len(result[0]["evidences"]) == 1

    def test_model_dump_fields(self):
        """字段对象有 model_dump 方法（line 342）。"""
        from app.api.v41_extract import _extraction_to_payload

        class FakeField:
            def model_dump(self):
                return {
                    "field_name": "winner_name",
                    "raw_value": "测试公司",
                    "field_status": "present",
                    "support_level": "direct",
                    "evidences": [],
                }

        class FakeExtraction:
            fields = [FakeField()]

        result = _extraction_to_payload(FakeExtraction())
        assert len(result) == 1
        assert result[0]["field_name"] == "winner_name"

    def test_non_dict_field_skipped(self):
        """非 dict 字段被跳过（line 343）。"""
        from app.api.v41_extract import _extraction_to_payload

        extraction = {"fields": ["not_a_dict", 123, {"field_name": "ok"}]}
        result = _extraction_to_payload(extraction)
        assert len(result) == 1
        assert result[0]["field_name"] == "ok"


class TestGetOrganizationExtra:
    """GET /api/organizations/{org_id} 补充。"""

    async def test_organization_not_found(self):
        """org_ 前缀但不匹配任何组织 → 404（line 411）。"""
        async with _client() as ac:
            resp = await ac.get(
                "/api/organizations/org_000000000000"
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == 404

    async def test_organization_with_profile(self):
        """有 winner_name ExtractedField → profile 非空（lines 423-442）。"""
        winner = "测试画像中标公司"
        tid = await _seed_tender(
            win_company=winner,
            tender_org="测试采购人B",
            source_url="http://example.gov.cn/profile/001",
        )
        await _seed_winner_field(tid, winner)
        async with _client() as ac:
            resp = await ac.get(f"/api/organizations/{winner}")
        body = resp.json()
        assert body["code"] == 0
        data = body["data"]
        assert data["org_name"] == winner
        # profile 应非空（有 win_records）
        assert data["profile"] is not None
        assert data["profile"]["win_count"] >= 1
        # data_completeness 结构
        assert "coverage_platforms" in data["data_completeness"]
        assert "valid_notice_count" in data["data_completeness"]


class TestResolveOrgName:
    """_resolve_org_name 直接调用（lines 463-479）。"""

    async def test_direct_name_returns_name(self):
        """非 org_ 前缀 → 直接返回名称（line 480）。"""
        from app.api.v41_extract import _resolve_org_name

        async with AsyncSessionLocal() as db:
            name = await _resolve_org_name("直接组织名", db)
        assert name == "直接组织名"

    async def test_hash_id_no_match_returns_none(self):
        """org_ 前缀但不匹配 → None（lines 463-479）。"""
        from app.api.v41_extract import _resolve_org_name

        async with AsyncSessionLocal() as db:
            name = await _resolve_org_name("org_aaaaaaaaaaaa", db)
        assert name is None

    async def test_hash_id_match_returns_name(self):
        """org_ 前缀且匹配 → 返回组织名。"""
        import hashlib

        from app.api.v41_extract import _resolve_org_name

        org_name = "哈希匹配测试公司"
        tid = await _seed_tender(
            win_company=org_name,
            source_url="http://example.gov.cn/hash/001",
        )
        await _seed_winner_field(tid, org_name)
        expected_hash = f"org_{hashlib.sha1(org_name.encode('utf-8')).hexdigest()[:12]}"
        async with AsyncSessionLocal() as db:
            name = await _resolve_org_name(expected_hash, db)
        assert name == org_name


class TestCollectOrgRecords:
    """_collect_org_records 直接调用（lines 491-502）。"""

    async def test_collect_records_with_winner_field(self):
        """有 winner_name ExtractedField → 返回记录列表。"""
        from app.api.v41_extract import _collect_org_records

        winner = "记录采集中标公司"
        tid = await _seed_tender(
            win_company=winner,
            tender_org="测试采购人C",
            location="北京",
            source_url="http://example.gov.cn/collect/001",
        )
        await _seed_winner_field(tid, winner)
        async with AsyncSessionLocal() as db:
            records = await _collect_org_records(winner, db)
        assert len(records) >= 1
        rec = records[0]
        assert rec["purchaser"] == "测试采购人C"
        assert rec["region"] == "北京"
        assert rec["source_platform"] == "ccgp"
        assert rec["notice_title"] == "测试医疗设备采购项目"

    async def test_collect_records_empty(self):
        """无匹配记录 → 空列表。"""
        from app.api.v41_extract import _collect_org_records

        async with AsyncSessionLocal() as db:
            records = await _collect_org_records("不存在的组织XYZ", db)
        assert records == []


class TestExtractTaskWorkerExtra:
    """抽取任务 worker 异常路径补充。"""

    async def test_worker_invalid_tender_id(self):
        """tender_id 为非整数字符串 → failed（lines 186-187）。"""
        from app.api.v41_extract import _EXTRACT_TASKS

        _EXTRACT_TASKS.clear()
        async with _client() as ac:
            create = await ac.post(
                "/api/extract/tasks",
                json={"tender_id": "not_a_number"},
            )
            task_id = create.json()["data"]["task_id"]
            status = "queued"
            data = None
            for _ in range(60):
                await asyncio.sleep(0.1)
                resp = await ac.get(f"/api/extract/tasks/{task_id}")
                data = resp.json()["data"]
                status = data["status"]
                if status in ("succeeded", "partially_succeeded", "failed"):
                    break
        assert status == "failed"
        assert data["error"] is not None

    async def test_worker_bad_protocol_source_url(self):
        """source_url 非法协议 → failed（lines 209-214）。"""
        from app.api.v41_extract import _EXTRACT_TASKS

        _EXTRACT_TASKS.clear()
        async with _client() as ac:
            create = await ac.post(
                "/api/extract/tasks",
                json={"source_url": "ftp://example.com/bad"},
            )
            task_id = create.json()["data"]["task_id"]
            status = "queued"
            data = None
            for _ in range(60):
                await asyncio.sleep(0.1)
                resp = await ac.get(f"/api/extract/tasks/{task_id}")
                data = resp.json()["data"]
                status = data["status"]
                if status in ("succeeded", "partially_succeeded", "failed"):
                    break
        assert status == "failed"
        assert "非法协议" in data["error"]

    async def test_worker_fetch_failure(self):
        """source_url http 但抓取失败 → failed（lines 235-240）。"""
        from app.api.v41_extract import _EXTRACT_TASKS

        _EXTRACT_TASKS.clear()
        async with _client() as ac:
            create = await ac.post(
                "/api/extract/tasks",
                json={"source_url": "http://nonexistent.invalid.example.com/x"},
            )
            task_id = create.json()["data"]["task_id"]
            status = "queued"
            data = None
            for _ in range(60):
                await asyncio.sleep(0.1)
                resp = await ac.get(f"/api/extract/tasks/{task_id}")
                data = resp.json()["data"]
                status = data["status"]
                if status in ("succeeded", "partially_succeeded", "failed"):
                    break
        assert status == "failed"
        assert "抓取失败" in data["error"]

    async def test_worker_no_api_key_after_fetch(self, monkeypatch):
        """抓取成功但无 DEEPSEEK_API_KEY → failed（lines 250-256）。"""
        from app.api.v41_extract import _EXTRACT_TASKS

        # mock httpx.AsyncClient 返回成功响应
        class _MockResp:
            text = "<html>测试内容</html>"
            status_code = 200

            def raise_for_status(self):
                pass

        class _MockClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def get(self, url):
                return _MockResp()

        import app.api.v41_extract as v41_extract_mod

        monkeypatch.setattr(v41_extract_mod.httpx, "AsyncClient", _MockClient)

        # 确保 DEEPSEEK_API_KEY 未设置
        monkeypatch.setattr(v41_extract_mod.settings, "DEEPSEEK_API_KEY", "")

        _EXTRACT_TASKS.clear()
        async with _client() as ac:
            create = await ac.post(
                "/api/extract/tasks",
                json={"source_url": "http://example.gov.cn/mock-fetch/001"},
            )
            task_id = create.json()["data"]["task_id"]
            status = "queued"
            data = None
            for _ in range(60):
                await asyncio.sleep(0.1)
                resp = await ac.get(f"/api/extract/tasks/{task_id}")
                data = resp.json()["data"]
                status = data["status"]
                if status in ("succeeded", "partially_succeeded", "failed"):
                    break
        assert status == "failed"
        assert "DEEPSEEK_API_KEY" in data["error"]
        assert data["result"]["fetched_text_length"] > 0


class TestOrganizationsSearchExtra:
    """GET /api/organizations/search 补充。"""

    async def test_search_with_pagination(self):
        """分页 + keyword 过滤。"""
        await _seed_tender(
            win_company="搜索公司A",
            source_url="http://example.gov.cn/org-search/001",
        )
        await _seed_tender(
            win_company="搜索公司B",
            source_url="http://example.gov.cn/org-search/002",
        )
        async with _client() as ac:
            resp = await ac.get(
                "/api/organizations/search",
                params={"keyword": "搜索", "page": 1, "page_size": 1},
            )
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["total"] >= 2
        assert len(body["data"]["items"]) == 1  # page_size=1
        assert body["data"]["page"] == 1
        assert body["data"]["page_size"] == 1