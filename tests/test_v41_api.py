"""v4.1 第12节标准 REST API 端点测试.

覆盖 12 个端点的契约与基础功能：
1. GET  /api/projects/search
2. GET  /api/projects/{project_id}
3. GET  /api/notices/{notice_id}
4. GET  /api/notices/{notice_id}/sources
5. GET  /api/notices/{notice_id}/participants
6. GET  /api/sources/{source_id}/versions
7. GET  /api/fields/{field_id}
8. GET  /api/organizations/search
9. GET  /api/organizations/{org_id}
10. POST /api/extract/tasks
11. GET  /api/extract/tasks/{task_id}
12. GET  /api/stats/quality

每个端点至少 1 个测试，外加若干 404 / 边界用例。
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.api.v41_extract import _EXTRACT_TASKS
from app.main import app
from app.models.database import AsyncSessionLocal, Base, engine
from app.models.evidence import Evidence, ExtractedField, FieldEvidenceLink
from app.models.tender import Tender

# 显式导入所有模型，确保 Base.metadata 注册全部表
# （避免 conftest 的 drop_all 在表缺失时失败导致 create_all 漏建表）
from app.models import (  # noqa: F401
    NoticeParticipant,
    NoticeSource,
    NoticeVersion,
    ProjectIdentifier,
    ScrapeJob,
    Subscription,
    PushLog,
    TenderNotice,
    TenderProject,
)
from app.models.organization import Organization  # noqa: F401


# BE-C1: 覆盖 verify_api_key 依赖，使测试无需传 Bearer token
@pytest.fixture(autouse=True)
async def _override_v41_auth():
    from app.api.auth import verify_api_key
    from app.main import app
    from app.models.user import ApiKey, User

    async def _mock_verify():
        user = User(id=1, email="v41-test@test.com", is_active=True)
        api_key = ApiKey(id=1, user_id=1, is_active=True)
        return user, api_key, "test-key"

    app.dependency_overrides[verify_api_key] = _mock_verify
    yield
    app.dependency_overrides.pop(verify_api_key, None)


async def _seed_tender(
    project_name: str = "测试医疗设备采购项目",
    bid_number: str = "TEST-2026-001",
    notice_type: str = "tender",
    tender_org: str | None = "北京大学第三医院",
    win_company: str | None = "测试中标公司A",
    agency: str | None = "北京市政府采购中心",
    core_content: str | None = "项目编号：TEST-2026-001；预算金额：1200万元。",
    source_platform: str | None = "ccgp",
    source_url: str | None = "http://example.gov.cn/test/001",
) -> int:
    """插入一条 Tender 测试记录，返回其 id。"""
    async with AsyncSessionLocal() as db:
        t = Tender(
            project_name=project_name,
            bid_number=bid_number,
            notice_type=notice_type,
            tender_org=tender_org,
            win_company=win_company,
            agency=agency,
            core_content=core_content,
            source_platform=source_platform,
            source_url=source_url,
            publish_time=datetime(2026, 7, 20),
            win_amount=Decimal("12000000.00"),
            location="北京",
        )
        db.add(t)
        await db.commit()
        await db.refresh(t)
        return t.id


async def _seed_field_with_evidence(tender_id: int) -> tuple[int, int]:
    """为指定 Tender 插入一个 ExtractedField + Evidence + Link，返回 (field_id, evidence_id)。"""
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


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ==== 1. GET /api/projects/search ====

async def test_projects_search_empty():
    """空库搜索返回空列表 + 分页字段。"""
    async with _client() as ac:
        resp = await ac.get("/api/projects/search")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["items"] == []
    assert body["data"]["total"] == 0
    assert body["data"]["page"] == 1


async def test_projects_search_with_keyword():
    """按关键词命中已 seeded 的项目。"""
    tid = await _seed_tender(project_name="医疗设备采购-2026")
    async with _client() as ac:
        resp = await ac.get("/api/projects/search", params={"keyword": "医疗设备"})
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["total"] >= 1
    assert any(item["project_id"] == str(tid) for item in body["data"]["items"])


# ==== 2. GET /api/projects/{project_id} ====

async def test_get_project():
    tid = await _seed_tender()
    async with _client() as ac:
        resp = await ac.get(f"/api/projects/{tid}")
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["project_id"] == str(tid)
    assert body["data"]["canonical_name"] == "测试医疗设备采购项目"
    assert isinstance(body["data"]["lifecycle"], list)
    assert body["data"]["lifecycle"][0]["notice_id"] == str(tid)


async def test_get_project_not_found():
    async with _client() as ac:
        resp = await ac.get("/api/projects/99999")
    assert resp.status_code == 404
    assert resp.json()["code"] == 404


# ==== 3. GET /api/notices/{notice_id} ====

async def test_get_notice():
    tid = await _seed_tender()
    async with _client() as ac:
        resp = await ac.get(f"/api/notices/{tid}")
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["notice_id"] == str(tid)
    assert body["data"]["title"] == "测试医疗设备采购项目"
    assert body["data"]["source_platform"] == "ccgp"


# ==== 4. GET /api/notices/{notice_id}/sources ====

async def test_get_notice_sources():
    tid = await _seed_tender()
    async with _client() as ac:
        resp = await ac.get(f"/api/notices/{tid}/sources")
    body = resp.json()
    assert body["code"] == 0
    assert len(body["data"]["sources"]) == 1
    assert body["data"]["sources"][0]["source_id"] == f"src_{tid}"
    assert body["data"]["lineage"]["origin_source_id"] == f"src_{tid}"


async def test_get_notice_sources_computed_official():
    """P1-10: ccgp.gov.cn 域名 → judge_source_role 判定 official_original（真实计算）。"""
    tid = await _seed_tender(
        source_url="https://www.ccgp.gov.cn/test/official/001",
        source_platform="ccgp",
    )
    async with _client() as ac:
        resp = await ac.get(f"/api/notices/{tid}/sources")
    body = resp.json()
    assert body["code"] == 0
    src = body["data"]["sources"][0]
    assert src["data_source"] == "computed_by_source_lineage"
    assert src["source_quality"] == "official_original"
    assert src["publication_role"] == "official_original"
    assert "官方域名" in src["quality_reason"]
    assert src["source_group"]  # 非空


async def test_get_notice_sources_computed_commercial():
    """P1-10: chinabidding 域名 → judge_source_role 判定 commercial_repost（真实计算）。"""
    tid = await _seed_tender(
        source_url="https://www.chinabidding.cn/test/commercial/001",
        source_platform="chinabidding",
    )
    async with _client() as ac:
        resp = await ac.get(f"/api/notices/{tid}/sources")
    body = resp.json()
    src = body["data"]["sources"][0]
    assert src["data_source"] == "computed_by_source_lineage"
    assert src["source_quality"] == "commercial_repost"
    assert src["publication_role"] == "commercial_repost"
    assert "商业域名" in src["quality_reason"]


async def test_get_notice_sources_from_notice_source_table():
    """P1-10: NoticeSource 表命中 → 直接用四层实体的 source_quality。"""
    from app.models.tender_project import (
        TenderProject, TenderNotice, NoticeSource,
    )
    custom_url = "https://www.ccgp.gov.cn/test/notice-source-table/001"
    tid = await _seed_tender(source_url=custom_url)
    # 插入四层实体（NoticeSource.source_quality=authorized_original）
    async with AsyncSessionLocal() as db:
        project = TenderProject(
            canonical_name="P1-10 测试项目",
            industry_category="service",
            resolution_status="resolved",
        )
        db.add(project)
        await db.flush()
        notice = TenderNotice(
            project_id=project.project_id,
            notice_type="tender",
            canonical_title="P1-10 测试公告",
            status="active",
        )
        db.add(notice)
        await db.flush()
        ns = NoticeSource(
            notice_id=notice.notice_id,
            source_url=custom_url,
            source_platform="ccgp",
            platform_type="government",
            publication_role="official_repost",
            source_quality="authorized_original",
            quality_reason="测试标注：授权转载",
            source_group=f"grp_{notice.notice_id[:8]}",
        )
        db.add(ns)
        await db.commit()
        ns_id = ns.notice_source_id

    async with _client() as ac:
        resp = await ac.get(f"/api/notices/{tid}/sources")
    body = resp.json()
    assert body["code"] == 0
    src = body["data"]["sources"][0]
    assert src["data_source"] == "notice_source_table"
    assert src["source_id"] == ns_id
    assert src["source_quality"] == "authorized_original"
    assert src["publication_role"] == "official_repost"
    assert src["quality_reason"] == "测试标注：授权转载"


# ==== 5. GET /api/notices/{notice_id}/participants ====

async def test_get_notice_participants():
    tid = await _seed_tender()
    async with _client() as ac:
        resp = await ac.get(f"/api/notices/{tid}/participants")
    body = resp.json()
    assert body["code"] == 0
    roles = {p["role"] for p in body["data"]["participants"]}
    assert {"purchaser", "procuring_agency", "winner"}.issubset(roles)


async def test_get_notice_participants_from_entity_table():
    """P0-1 下游：notice_participants 表命中 → 优先实体表数据。"""
    from app.models.tender_project import (
        NoticeParticipant, NoticeSource, TenderNotice, TenderProject,
    )
    custom_url = "https://www.ccgp.gov.cn/test/participants-entity/001"
    # tender 组织列全空（模拟真实库），数据只在实体表
    tid = await _seed_tender(
        source_url=custom_url, tender_org=None, win_company=None, agency=None,
    )
    async with AsyncSessionLocal() as db:
        project = TenderProject(
            canonical_name="参与方实体测试项目",
            industry_category="service",
            resolution_status="resolved",
        )
        db.add(project)
        await db.flush()
        notice = TenderNotice(
            project_id=project.project_id,
            notice_type="award",
            canonical_title="参与方实体测试公告",
            status="active",
        )
        db.add(notice)
        await db.flush()
        ns = NoticeSource(
            notice_id=notice.notice_id,
            source_url=custom_url,
            source_platform="ccgp",
            platform_type="government",
            publication_role="original",
            source_quality="official_original",
            source_group=f"grp_{notice.notice_id[:8]}",
        )
        db.add(ns)
        await db.flush()
        db.add(NoticeParticipant(
            notice_id=notice.notice_id,
            raw_name="测试采购单位",
            normalized_name="测试采购单位",
            participant_role="purchaser",
            resolution_status="resolved",
        ))
        db.add(NoticeParticipant(
            notice_id=notice.notice_id,
            raw_name="测试中标单位",
            normalized_name="测试中标单位",
            participant_role="winner",
            resolution_status="resolved",
        ))
        await db.commit()

    async with _client() as ac:
        resp = await ac.get(f"/api/notices/{tid}/participants")
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["data_source"] == "entity_table"
    roles = {p["role"] for p in body["data"]["participants"]}
    assert roles == {"purchaser", "winner"}
    assert body["data"]["total"] == 2


async def test_get_notice_participants_fallback_tender_columns():
    """无四层实体时回退 tender 组织列，data_source=tender_fallback。"""
    tid = await _seed_tender(source_url="http://no-entity.example.com/x/1")
    async with _client() as ac:
        resp = await ac.get(f"/api/notices/{tid}/participants")
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["data_source"] == "tender_fallback"
    assert body["data"]["total"] >= 1


async def test_get_project_lifecycle_from_entity_table():
    """P0-1 下游：同项目两条 TenderNotice → lifecycle 返回完整生命周期。"""
    from app.models.tender_project import (
        NoticeSource, TenderNotice, TenderProject,
    )
    custom_url = "https://www.ccgp.gov.cn/test/lifecycle-entity/001"
    tid = await _seed_tender(source_url=custom_url)
    async with AsyncSessionLocal() as db:
        project = TenderProject(
            canonical_name="生命周期实体测试项目",
            industry_category="goods",
            resolution_status="resolved",
        )
        db.add(project)
        await db.flush()
        n1 = TenderNotice(
            project_id=project.project_id,
            notice_type="tender",
            canonical_title="招标公告",
            status="active",
            publish_date=datetime(2026, 7, 1),
        )
        n2 = TenderNotice(
            project_id=project.project_id,
            notice_type="award",
            canonical_title="中标公告",
            status="active",
            publish_date=datetime(2026, 7, 20),
        )
        db.add_all([n1, n2])
        await db.flush()
        db.add(NoticeSource(
            notice_id=n2.notice_id,
            source_url=custom_url,
            source_platform="ccgp",
            platform_type="government",
            publication_role="original",
            source_quality="official_original",
            source_group=f"grp_{n2.notice_id[:8]}",
        ))
        await db.commit()

    async with _client() as ac:
        resp = await ac.get(f"/api/projects/{tid}")
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["data_source"] == "tender_notice_table"
    lc = body["data"]["lifecycle"]
    assert len(lc) == 2
    assert [x["notice_type"] for x in lc] == ["tender", "award"]


async def test_get_project_lifecycle_fallback():
    """无四层实体时 lifecycle 回退单公告，data_source=tender_fallback。"""
    tid = await _seed_tender(source_url="http://no-entity.example.com/x/2")
    async with _client() as ac:
        resp = await ac.get(f"/api/projects/{tid}")
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["data_source"] == "tender_fallback"
    assert len(body["data"]["lifecycle"]) == 1


# ==== 6. GET /api/sources/{source_id}/versions ====

async def test_get_source_versions():
    tid = await _seed_tender()
    async with _client() as ac:
        resp = await ac.get(f"/api/sources/src_{tid}/versions")
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["total_versions"] >= 1
    assert body["data"]["versions"][0]["change_type"] in {"initial", "none"}
    assert len(body["data"]["versions"][0]["content_sha256"]) == 64

async def _seed_notice_version_chain(source_url: str = "http://example.gov.cn/notice-version/001"):
    """插入 TenderProject + TenderNotice + NoticeSource + 2 条 NoticeVersion，返回 (notice_source_id, versions)。"""
    from app.models.tender_project import (
        TenderProject, TenderNotice, NoticeSource, NoticeVersion,
    )
    async with AsyncSessionLocal() as db:
        project = TenderProject(
            canonical_name="测试版本链项目",
            industry_category="goods",
            resolution_status="resolved",
        )
        db.add(project)
        await db.flush()

        notice = TenderNotice(
            project_id=project.project_id,
            notice_type="tender",
            canonical_title="测试版本链公告",
            status="active",
        )
        db.add(notice)
        await db.flush()

        source = NoticeSource(
            notice_id=notice.notice_id,
            source_url=source_url,
            source_platform="ccgp",
            platform_type="government",
            publication_role="original",
            source_quality="official_original",
            source_group=f"grp_{notice.notice_id[:8]}",
        )
        db.add(source)
        await db.flush()

        v1 = NoticeVersion(
            notice_source_id=source.notice_source_id,
            http_status=200,
            content_sha256="a" * 64,
            raw_text_sha256="b" * 64,
            change_type="initial",
            fetched_at=datetime(2026, 1, 1, 10, 0, 0),
        )
        db.add(v1)
        await db.flush()

        v2 = NoticeVersion(
            notice_source_id=source.notice_source_id,
            http_status=200,
            content_sha256="c" * 64,
            raw_text_sha256="d" * 64,
            previous_version_id=v1.version_id,
            change_type="material",
            fetched_at=datetime(2026, 1, 2, 10, 0, 0),
        )
        db.add(v2)
        await db.commit()
        return source.notice_source_id, [v2, v1]


async def test_get_source_versions_with_notice_version_ulid():
    """P1-9: ULID source_id 直接查 NoticeVersion 表，返回真实版本链。"""
    notice_source_id, versions = await _seed_notice_version_chain()
    async with _client() as ac:
        resp = await ac.get(f"/api/sources/{notice_source_id}/versions")
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["source_id"] == notice_source_id
    assert data["total_versions"] == 2
    assert data["data_source"] == "notice_version_table"
    # 按 fetched_at desc 排序，第一条是 v2
    v_first = data["versions"][0]
    assert v_first["change_type"] == "material"
    assert v_first["content_sha256"] == "c" * 64
    assert v_first["raw_text_sha256"] == "d" * 64
    assert v_first["http_status"] == 200
    assert len(v_first["version_id"]) == 26  # ULID
    # 版本链
    v_second = data["versions"][1]
    assert v_second["change_type"] == "initial"
    assert v_second["previous_version_id"] is None


async def test_get_source_versions_ulid_not_found():
    """P1-9: ULID 格式但无版本记录 → 404。"""
    fake_ulid = "01H" + "X" * 23  # 26 字符合法 ULID 格式，但不存在
    async with _client() as ac:
        resp = await ac.get(f"/api/sources/{fake_ulid}/versions")
    assert resp.status_code == 404
    assert resp.json()["code"] == 404


async def test_get_source_versions_src_fallback_to_tender():
    """P1-9: src_{tender_id} 且无 NoticeSource 关联 → 回退 Tender 表，data_source=tender_fallback。"""
    tid = await _seed_tender(source_url="http://example.gov.cn/no-notice-source/001")
    async with _client() as ac:
        resp = await ac.get(f"/api/sources/src_{tid}/versions")
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["data_source"] == "tender_fallback"
    assert data["total_versions"] >= 1
    assert data["versions"][0]["change_type"] in {"initial", "none"}
    assert len(data["versions"][0]["content_sha256"]) == 64


async def test_get_source_versions_src_links_to_notice_version():
    """P1-9: src_{tender_id} 但 Tender.source_url 关联到 NoticeSource → 走 NoticeVersion 表。"""
    custom_url = "http://example.gov.cn/linked-notice/001"
    # 先创建 Tender（用相同 source_url）
    tid = await _seed_tender(source_url=custom_url)
    # 再创建四层实体链（相同 source_url）
    notice_source_id, _versions = await _seed_notice_version_chain(source_url=custom_url)
    async with _client() as ac:
        resp = await ac.get(f"/api/sources/src_{tid}/versions")
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    # 应该走 NoticeVersion 表，而不是 Tender 回退
    assert data["data_source"] == "notice_version_table"
    assert data["source_id"] == notice_source_id
    assert data["total_versions"] == 2


# ==== 7. GET /api/fields/{field_id} ====

async def test_get_field_with_evidence():
    tid = await _seed_tender()
    _fid, _eid = await _seed_field_with_evidence(tid)
    field_id = f"{tid}_amount"
    async with _client() as ac:
        resp = await ac.get(f"/api/fields/{field_id}")
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["field_id"] == field_id
    assert data["field_name"] == "amount"
    assert data["support_level"] == "direct"
    assert len(data["values"]) == 1
    assert len(data["values"][0]["evidences"]) == 1
    ev = data["values"][0]["evidences"][0]
    assert ev["text"] == "1200万元"
    assert ev["verified"] is True


# ==== 8. GET /api/organizations/search ====

async def test_organizations_search():
    await _seed_tender(win_company="测试中标公司A")
    async with _client() as ac:
        resp = await ac.get("/api/organizations/search", params={"keyword": "测试中标"})
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["total"] >= 1
    assert any(item["org_name"] == "测试中标公司A" for item in body["data"]["items"])
    assert body["data"]["items"][0]["org_id"].startswith("org_")


# ==== 9. GET /api/organizations/{org_id} ====

async def test_get_organization_profile():
    """组织画像端点调用 observation_signals 返回 6 个 MVP 信号。"""
    await _seed_tender(
        win_company="测试中标公司A",
        tender_org="测试采购人A",
    )
    async with _client() as ac:
        resp = await ac.get("/api/organizations/测试中标公司A")
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["org_name"] == "测试中标公司A"
    assert len(data["signals"]) == 6  # 六个 MVP 信号
    signal_names = {s["signal_name"] for s in data["signals"]}
    assert "中标活跃度" in signal_names
    assert "公开中标集中度" in signal_names
    assert data["entity_resolution_status"] in {"resolved", "unresolved"}


# ==== 10. POST /api/extract/tasks ====

async def test_create_extract_task():
    _EXTRACT_TASKS.clear()
    async with _client() as ac:
        resp = await ac.post("/api/extract/tasks", json={"source_url": "http://example.gov.cn/x"})
    body = resp.json()
    assert body["code"] == 0
    task = body["data"]
    assert task["status"] == "queued"
    assert task["task_id"]
    assert task["source_url"] == "http://example.gov.cn/x"
    assert task["task_id"] in _EXTRACT_TASKS


# ==== 11. GET /api/extract/tasks/{task_id} ====

async def test_get_extract_task_status():
    _EXTRACT_TASKS.clear()
    async with _client() as ac:
        create = await ac.post("/api/extract/tasks", json={"source_url": "http://example.gov.cn/y"})
        task_id = create.json()["data"]["task_id"]
        resp = await ac.get(f"/api/extract/tasks/{task_id}")
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["task_id"] == task_id
    assert body["data"]["status"] == "queued"


async def test_get_extract_task_not_found():
    async with _client() as ac:
        resp = await ac.get("/api/extract/tasks/nonexistent-task-id")
    assert resp.status_code == 404
    assert resp.json()["code"] == 404


# ==== P1-8: worker 真实流转测试 ====

async def test_extract_task_worker_flow_with_tender_id():
    """P1-8: worker 真实流转 queued -> running -> succeeded（复用 DB Tender + 已抽取字段）。"""
    _EXTRACT_TASKS.clear()
    tid = await _seed_tender()
    await _seed_field_with_evidence(tid)
    async with _client() as ac:
        create = await ac.post("/api/extract/tasks", json={"tender_id": tid})
        assert create.json()["code"] == 0
        task_id = create.json()["data"]["task_id"]
        # 初始状态应为 queued（worker 启动前）
        assert create.json()["data"]["status"] == "queued"
        # 轮询直到状态稳定（worker 后台异步执行）
        status = "queued"
        data = None
        for _ in range(60):  # 最多等 6 秒
            await asyncio.sleep(0.1)
            resp = await ac.get(f"/api/extract/tasks/{task_id}")
            body = resp.json()
            assert body["code"] == 0
            data = body["data"]
            status = data["status"]
            if status in ("succeeded", "partially_succeeded", "failed"):
                break
    assert status == "succeeded", f"期望 succeeded，实际 {status}: {data}"
    assert data["result"] is not None
    assert data["result"]["tender_id"] == tid
    assert data["result"]["fields_count"] >= 1
    assert data["result"]["evidences_count"] >= 1
    assert data["result"]["extraction_source"] == "db_cached"
    assert data["started_at"] is not None
    assert data["finished_at"] is not None


async def test_extract_task_worker_flow_failed_no_source():
    """P1-8: worker 流转 queued -> failed（无 source_url/tender_id，明确失败原因）。"""
    _EXTRACT_TASKS.clear()
    async with _client() as ac:
        create = await ac.post("/api/extract/tasks", json={})
        task_id = create.json()["data"]["task_id"]
        status = "queued"
        data = None
        for _ in range(60):
            await asyncio.sleep(0.1)
            resp = await ac.get(f"/api/extract/tasks/{task_id}")
            body = resp.json()
            data = body["data"]
            status = data["status"]
            if status in ("succeeded", "partially_succeeded", "failed"):
                break
    assert status == "failed", f"期望 failed，实际 {status}: {data}"
    assert data["error"] is not None
    assert "source_url" in data["error"] or "tender_id" in data["error"]
    assert data["started_at"] is not None
    assert data["finished_at"] is not None


async def test_extract_task_worker_flow_with_source_url_db_hit():
    """P1-8: worker 通过 source_url 命中 DB Tender，流转到 succeeded。"""
    _EXTRACT_TASKS.clear()
    custom_url = "http://example.gov.cn/test/worker-flow-url"
    tid = await _seed_tender(source_url=custom_url)
    await _seed_field_with_evidence(tid)
    async with _client() as ac:
        create = await ac.post(
            "/api/extract/tasks",
            json={"source_url": custom_url},
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
    assert status == "succeeded", f"期望 succeeded，实际 {status}: {data}"
    assert data["result"]["tender_id"] == tid
    assert data["result"]["extraction_source"] == "db_cached"


# ==== 12. GET /api/stats/quality ====

async def test_stats_quality():
    tid = await _seed_tender()
    await _seed_field_with_evidence(tid)
    async with _client() as ac:
        resp = await ac.get("/api/stats/quality")
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["tender_count"] >= 1
    assert data["field_count"] >= 1
    assert data["evidence_count"] >= 1
    assert data["verified_evidence_count"] >= 1
    assert isinstance(data["support_level_distribution"], dict)
    assert isinstance(data["match_method_distribution"], dict)
    assert 0.0 <= data["verification_rate"] <= 1.0


# ==== 兼容性：旧路由不破坏 ====

async def test_legacy_real_demo_router_still_works():
    """/api/real/tenders 旧路由未被 v41 注册破坏。"""
    await _seed_tender()
    async with _client() as ac:
        resp = await ac.get("/api/real/tenders")
    assert resp.status_code == 200
    assert resp.json()["code"] == 200  # 旧路由用 200，v41 用 0


async def test_invalid_project_id_returns_400():
    async with _client() as ac:
        resp = await ac.get("/api/projects/abc")
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == 400
    assert "非法" in body["msg"]