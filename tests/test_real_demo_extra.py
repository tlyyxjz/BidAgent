"""real_demo.py 额外测试 — 覆盖 /api/real/tenders/* 4 个端点 + _infer_org_meta.

被测端点（与 Turbo HTML 对齐）:
- GET /api/real/tenders                      → search.html
- GET /api/real/tenders/{id}/detail          → notice_detail.html
- GET /api/real/tenders/{id}/versions        → version_history.html
- GET /api/real/tenders/{id}/organization    → org_profile.html

参考 tests/test_demo_api_extra.py 的测试风格：httpx.AsyncClient + ASGITransport，
独立重建表（避免 conftest 同事务 drop+create 丢表）。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.real_demo import _infer_org_meta
from app.main import app
from app.models.database import AsyncSessionLocal, Base, engine
from app.models.evidence import Evidence, ExtractedField, FieldEvidenceLink
from app.models.tender import Tender
from app.models.user import utc_now

# 显式导入所有模型，确保 Base.metadata 注册全部表
from app.models import (  # noqa: F401
    NoticeParticipant,
    NoticeSource,
    NoticeVersion,
    ProjectIdentifier,
    PushLog,
    Subscription,
    TenderNotice,
    TenderProject,
)
from app.models.organization import Organization  # noqa: F401


# ==== fixtures（覆盖 conftest 同名 fixture，避免同事务 drop+create 丢表）====


@pytest.fixture(autouse=True)
async def _clean_pipeline_sessions():
    """每个测试前后清理 pipeline 内存 session，避免测试间状态干扰。"""
    from app.agents.pipeline import _sessions, _sessions_lock

    async with _sessions_lock:
        _sessions.clear()
    yield
    async with _sessions_lock:
        _sessions.clear()


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ==== 数据 seeding 辅助函数 ====


async def _seed_tender(**kwargs) -> int:
    """插入一条 Tender 测试记录，返回其 id。"""
    defaults = dict(
        project_name="测试医疗设备采购项目",
        bid_number="TEST-2026-001",
        notice_type="tender",
        tender_org="北京大学第三医院",
        win_company="测试中标公司A",
        source_platform="ccgp",
        source_url="http://example.gov.cn/test/001",
        publish_time=datetime(2026, 7, 20),
        win_amount=Decimal("12000000.00"),
        location="北京",
        core_content="北京市政府采购中心\n医疗设备采购项目招标公告\n项目编号：BJGPC-2026-0042",
    )
    defaults.update(kwargs)
    async with AsyncSessionLocal() as db:
        t = Tender(**defaults)
        db.add(t)
        await db.commit()
        await db.refresh(t)
        return t.id


async def _seed_field(tender_id: int, field_name: str, raw_value: str, **kwargs) -> int:
    """插入一条 ExtractedField 记录，返回其 id。"""
    defaults = dict(
        tender_id=tender_id,
        field_name=field_name,
        field_status="present",
        raw_value=raw_value,
        normalized_value=raw_value,
        support_level="direct",
    )
    defaults.update(kwargs)
    async with AsyncSessionLocal() as db:
        f = ExtractedField(**defaults)
        db.add(f)
        await db.commit()
        await db.refresh(f)
        return f.id


async def _seed_evidence(
    tender_id: int, text: str, start: int, end: int, **kwargs
) -> int:
    """插入一条 Evidence 记录，返回其 id。"""
    defaults = dict(
        tender_id=tender_id,
        evidence_text=text,
        raw_start=start,
        raw_end=end,
        match_method="exact",
        verified=True,
    )
    defaults.update(kwargs)
    async with AsyncSessionLocal() as db:
        e = Evidence(**defaults)
        db.add(e)
        await db.commit()
        await db.refresh(e)
        return e.id


async def _seed_link(
    field_id: int, evidence_id: int, role: str = "primary", sequence: int = 0
) -> int:
    """插入一条 FieldEvidenceLink 记录，返回其 id。"""
    async with AsyncSessionLocal() as db:
        link = FieldEvidenceLink(
            field_id=field_id,
            evidence_id=evidence_id,
            evidence_role=role,
            sequence=sequence,
            is_required=True,
        )
        db.add(link)
        await db.commit()
        await db.refresh(link)
        return link.id


# ==== a) GET /api/real/tenders ====


async def test_real_list_tenders_format():
    """列表格式：每条含 id/name/type/amount/purchaser/publish_date/platform。"""
    await _seed_tender()
    async with _client() as ac:
        resp = await ac.get("/api/real/tenders")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    tenders = body["data"]["tenders"]
    assert isinstance(tenders, list)
    assert len(tenders) >= 1
    for t in tenders:
        assert "id" in t
        assert "name" in t
        assert "type" in t
        assert "amount" in t
        assert "purchaser" in t
        assert "publish_date" in t
        assert "platform" in t


async def test_real_list_tenders_includes_field_values():
    """列表应包含从 ExtractedField 批量查询的 amount/purchaser_name/publish_date。"""
    tid = await _seed_tender()
    await _seed_field(tid, "amount", "12,000,000.00")
    await _seed_field(tid, "purchaser_name", "北京大学第三医院")
    await _seed_field(tid, "publish_date", "2026-07-20")
    async with _client() as ac:
        resp = await ac.get("/api/real/tenders")
    tenders = resp.json()["data"]["tenders"]
    target = next(t for t in tenders if t["id"] == tid)
    assert target["amount"] == "12,000,000.00"
    assert target["purchaser"] == "北京大学第三医院"
    assert target["publish_date"] == "2026-07-20"


async def test_real_list_tenders_empty_database():
    """空数据库：返回空列表。"""
    async with _client() as ac:
        resp = await ac.get("/api/real/tenders")
    assert resp.status_code == 200
    assert resp.json()["data"]["tenders"] == []


async def test_real_list_tenders_publish_date_fallback():
    """无 publish_date 字段时：fallback 到 created_at.strftime。"""
    tid = await _seed_tender()
    # 不 seed publish_date 字段 → fallback 到 created_at
    async with _client() as ac:
        resp = await ac.get("/api/real/tenders")
    tenders = resp.json()["data"]["tenders"]
    target = next(t for t in tenders if t["id"] == tid)
    # publish_date 应为 created_at 的日期格式（非空）
    assert target["publish_date"] != ""


# ==== b) GET /api/real/tenders/{id}/detail ====


async def test_real_detail_structure():
    """详情结构：clean_raw_text/tender_id/project_name/fields。"""
    tid = await _seed_tender(core_content="测试原文内容")
    async with _client() as ac:
        resp = await ac.get(f"/api/real/tenders/{tid}/detail")
    assert resp.status_code == 200
    d = resp.json()["data"]
    assert d["tender_id"] == tid
    assert d["clean_raw_text"] == "测试原文内容"
    assert d["project_name"] == "测试医疗设备采购项目"
    assert isinstance(d["fields"], list)


async def test_real_detail_fields_structure():
    """详情字段结构：6 个核心字段，每个含 field_id/field_name/field_label/support_level/field_status/values。"""
    tid = await _seed_tender()
    await _seed_field(tid, "purchaser_name", "北京大学第三医院")
    async with _client() as ac:
        resp = await ac.get(f"/api/real/tenders/{tid}/detail")
    fields = resp.json()["data"]["fields"]
    # 6 个核心字段（FIELD_ORDER）
    assert len(fields) == 6
    field_names = {f["field_name"] for f in fields}
    assert field_names == {
        "project_identifier",
        "purchaser_name",
        "winner_name",
        "amount",
        "publish_date",
        "bid_deadline",
    }
    for f in fields:
        assert "field_id" in f
        assert "field_name" in f
        assert "field_label" in f
        assert "support_level" in f
        assert "field_status" in f
        assert "values" in f
    # purchaser_name 应 present
    purchaser_field = next(f for f in fields if f["field_name"] == "purchaser_name")
    assert purchaser_field["field_status"] == "present"
    assert len(purchaser_field["values"]) == 1
    assert purchaser_field["values"][0]["raw_value"] == "北京大学第三医院"


async def test_real_detail_with_evidences():
    """详情字段含证据：Evidence + FieldEvidenceLink 关联查询。"""
    tid = await _seed_tender(core_content="项目编号：BJGPC-2026-0042")
    fid = await _seed_field(tid, "project_identifier", "BJGPC-2026-0042")
    eid = await _seed_evidence(tid, "BJGPC-2026-0042", 5, 21, verified=True)
    await _seed_link(fid, eid, role="primary", sequence=0)
    async with _client() as ac:
        resp = await ac.get(f"/api/real/tenders/{tid}/detail")
    fields = resp.json()["data"]["fields"]
    pid_field = next(f for f in fields if f["field_name"] == "project_identifier")
    assert len(pid_field["values"]) == 1
    evidences = pid_field["values"][0]["evidences"]
    assert len(evidences) == 1
    ev = evidences[0]
    assert ev["text"] == "BJGPC-2026-0042"
    assert ev["start"] == 5
    assert ev["end"] == 21
    assert ev["role"] == "primary"
    assert ev["match_method"] == "exact"
    # verified=True → confidence=0.95
    assert ev["confidence"] == 0.95


async def test_real_detail_evidence_unverified_confidence():
    """未验证证据 → confidence=0.5。"""
    tid = await _seed_tender(core_content="采购人：测试采购人")
    fid = await _seed_field(tid, "purchaser_name", "测试采购人")
    eid = await _seed_evidence(
        tid, "测试采购人", 4, 9, verified=False, match_method="substring"
    )
    await _seed_link(fid, eid, role="primary", sequence=0)
    async with _client() as ac:
        resp = await ac.get(f"/api/real/tenders/{tid}/detail")
    fields = resp.json()["data"]["fields"]
    purchaser_field = next(f for f in fields if f["field_name"] == "purchaser_name")
    ev = purchaser_field["values"][0]["evidences"][0]
    assert ev["confidence"] == 0.5
    assert ev["match_method"] == "substring"


async def test_real_detail_not_found():
    """不存在的 ID：返回 404 + code=404。"""
    async with _client() as ac:
        resp = await ac.get("/api/real/tenders/99999/detail")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == 404
    assert body["data"] is None


async def test_real_detail_absent_field_marked():
    """未抽取的字段标记为 absent + unsupported + 空 values。"""
    tid = await _seed_tender()
    # 不 seed 任何字段
    async with _client() as ac:
        resp = await ac.get(f"/api/real/tenders/{tid}/detail")
    fields = resp.json()["data"]["fields"]
    for f in fields:
        assert f["field_status"] == "absent"
        assert f["support_level"] == "unsupported"
        assert f["values"] == []


async def test_real_detail_multiple_values():
    """同字段多值（amount 多值）：values 列表含多条记录。"""
    tid = await _seed_tender()
    await _seed_field(tid, "amount", "12,000,000.00", amount_type="budget")
    await _seed_field(tid, "amount", "1200万元", amount_type="ceiling")
    async with _client() as ac:
        resp = await ac.get(f"/api/real/tenders/{tid}/detail")
    fields = resp.json()["data"]["fields"]
    amount_field = next(f for f in fields if f["field_name"] == "amount")
    assert len(amount_field["values"]) == 2
    assert amount_field["values"][0]["amount_type"] == "budget"
    assert amount_field["values"][1]["amount_type"] == "ceiling"


# ==== c) GET /api/real/tenders/{id}/versions ====


async def test_real_versions_sha256():
    """版本历史：content_sha256 / material_sha256 为完整 64 位 SHA256。"""
    tid = await _seed_tender(core_content="测试版本内容")
    async with _client() as ac:
        resp = await ac.get(f"/api/real/tenders/{tid}/versions")
    assert resp.status_code == 200
    d = resp.json()["data"]
    versions = d["versions"]
    assert len(versions) >= 1
    v = versions[0]
    assert len(v["content_sha256"]) == 64
    assert len(v["material_sha256"]) == 64
    # 验证 content_sha256 与原文一致
    expected_content_sha = hashlib.sha256("测试版本内容".encode("utf-8")).hexdigest()
    assert v["content_sha256"] == expected_content_sha


async def test_real_versions_material_sha256_calculation():
    """material_sha256 基于 raw_text[:500] + raw_text[-500:] 计算。"""
    content = "A" * 600  # 超过 500 字符
    tid = await _seed_tender(core_content=content)
    expected_material_sha = hashlib.sha256(
        (content[:500] + content[-500:]).encode("utf-8")
    ).hexdigest()
    async with _client() as ac:
        resp = await ac.get(f"/api/real/tenders/{tid}/versions")
    v = resp.json()["data"]["versions"][0]
    assert v["material_sha256"] == expected_material_sha


async def test_real_versions_stats_structure():
    """版本统计：total_versions/has_material_change/source_platform/source_url/first_seen/last_seen。"""
    tid = await _seed_tender(source_platform="ccgp", source_url="http://test/1")
    async with _client() as ac:
        resp = await ac.get(f"/api/real/tenders/{tid}/versions")
    stats = resp.json()["data"]["stats"]
    assert "total_versions" in stats
    assert "has_material_change" in stats
    assert "source_platform" in stats
    assert "source_url" in stats
    assert "first_seen" in stats
    assert "last_seen" in stats
    assert stats["source_platform"] == "ccgp"
    assert stats["source_url"] == "http://test/1"
    assert stats["total_versions"] >= 1


async def test_real_versions_not_found():
    """不存在的 ID：返回 404。"""
    async with _client() as ac:
        resp = await ac.get("/api/real/tenders/99999/versions")
    assert resp.status_code == 404
    assert resp.json()["code"] == 404


async def test_real_versions_single_version():
    """单版本（updated_at == created_at）：versions 仅 1 条，change_type=create。"""
    tid = await _seed_tender()
    async with _client() as ac:
        resp = await ac.get(f"/api/real/tenders/{tid}/versions")
    versions = resp.json()["data"]["versions"]
    assert len(versions) == 1
    assert versions[0]["change_type"] == "create"
    assert versions[0]["change_type_label"] == "初始抓取"


async def test_real_versions_with_updated_at():
    """updated_at > created_at 时：生成第二条 none 版本（versions[0]）。"""
    tid = await _seed_tender(created_at=utc_now() - timedelta(days=30))
    async with _client() as ac:
        resp = await ac.get(f"/api/real/tenders/{tid}/versions")
    versions = resp.json()["data"]["versions"]
    assert len(versions) == 2
    # 最新版本（versions[0]）应为 none
    assert versions[0]["change_type"] == "none"
    assert versions[0]["change_type_label"] == "复查无变化"
    # 原始版本（versions[1]）仍为 create
    assert versions[1]["change_type"] == "create"


# ==== d) GET /api/real/tenders/{id}/organization ====


async def test_real_organization_structure():
    """组织信息结构：org_id/org_name/org_type/region/activity_90d/top3_*/waste_bid_*/data_completeness。"""
    tid = await _seed_tender(tender_org="北京大学第三医院")
    await _seed_field(tid, "purchaser_name", "北京大学第三医院")
    async with _client() as ac:
        resp = await ac.get(f"/api/real/tenders/{tid}/organization")
    assert resp.status_code == 200
    d = resp.json()["data"]
    assert "org_id" in d
    assert "org_name" in d
    assert "org_type" in d
    assert "region" in d
    assert "activity_90d" in d
    assert "top3_concentration" in d
    assert "top3_purchasers" in d
    assert "waste_bid_count" in d
    assert "waste_bid_related" in d
    assert "data_completeness" in d
    # org_name 取自 purchaser_name
    assert d["org_name"] == "北京大学第三医院"
    # _infer_org_meta("北京大学第三医院", "purchaser") → ("医疗机构", "北京")
    assert d["org_type"] == "医疗机构"
    assert d["region"] == "北京"


async def test_real_organization_activity_90d():
    """activity_90d 含 90 天 daily 数据 + total/tender_count/award_count。"""
    tid = await _seed_tender()
    await _seed_field(tid, "purchaser_name", "测试采购人")
    async with _client() as ac:
        resp = await ac.get(f"/api/real/tenders/{tid}/organization")
    activity = resp.json()["data"]["activity_90d"]
    assert len(activity["daily"]) == 90
    assert isinstance(activity["total"], int)
    assert isinstance(activity["tender_count"], int)
    assert isinstance(activity["award_count"], int)
    for d in activity["daily"]:
        assert "date" in d
        assert "count" in d


async def test_real_organization_not_found():
    """不存在的 ID：返回 404。"""
    async with _client() as ac:
        resp = await ac.get("/api/real/tenders/99999/organization")
    assert resp.status_code == 404
    assert resp.json()["code"] == 404


async def test_real_organization_fallback_to_project_name():
    """无 purchaser_name/winner_name 时：fallback 到 project_name 前 20 字符。"""
    tid = await _seed_tender(project_name="[Demo] 这是一个很长的项目名称用于测试截断")
    # 不 seed purchaser_name / winner_name
    async with _client() as ac:
        resp = await ac.get(f"/api/real/tenders/{tid}/organization")
    d = resp.json()["data"]
    # fallback: project_name.replace("[Demo] ", "")[:20]
    assert d["org_name"] == "这是一个很长的项目名称用于测试截断"[:20]


async def test_real_organization_winner_preferred():
    """同时有 winner_name 和 purchaser_name 时：优先取 winner_name。"""
    tid = await _seed_tender()
    await _seed_field(tid, "purchaser_name", "采购人甲")
    await _seed_field(tid, "winner_name", "中标人乙")
    async with _client() as ac:
        resp = await ac.get(f"/api/real/tenders/{tid}/organization")
    d = resp.json()["data"]
    assert d["org_name"] == "中标人乙"


async def test_real_organization_data_completeness():
    """data_completeness 结构：platforms/tender_count/award_count/correction_count/missing_fields。"""
    tid = await _seed_tender(source_platform="ccgp")
    await _seed_field(tid, "purchaser_name", "测试采购人")
    async with _client() as ac:
        resp = await ac.get(f"/api/real/tenders/{tid}/organization")
    dc = resp.json()["data"]["data_completeness"]
    assert "platforms" in dc
    assert "tender_count" in dc
    assert "award_count" in dc
    assert "correction_count" in dc
    assert "missing_fields" in dc
    assert isinstance(dc["platforms"], list)


# ==== e) Direct function calls (for coverage tracking) ====
# httpx.ASGITransport may not be tracked by coverage.py; direct calls ensure coverage.


async def test_direct_list_tenders():
    """Direct call to list_tenders for coverage tracking."""
    from app.api.real_demo import list_tenders

    await _seed_tender()
    async with AsyncSessionLocal() as db:
        resp = await list_tenders(db)
    import json as _json

    body = _json.loads(resp.body)
    assert body["code"] == 200
    assert len(body["data"]["tenders"]) >= 1


async def test_direct_list_tenders_with_fields():
    """Direct call to list_tenders with seeded field values."""
    from app.api.real_demo import list_tenders

    tid = await _seed_tender()
    await _seed_field(tid, "amount", "999")
    await _seed_field(tid, "purchaser_name", "采购人X")
    async with AsyncSessionLocal() as db:
        resp = await list_tenders(db)
    import json as _json

    tenders = _json.loads(resp.body)["data"]["tenders"]
    target = next(t for t in tenders if t["id"] == tid)
    assert target["amount"] == "999"
    assert target["purchaser"] == "采购人X"


async def test_direct_get_tender_detail():
    """Direct call to get_tender_detail for coverage tracking."""
    from app.api.real_demo import get_tender_detail

    tid = await _seed_tender(core_content="测试原文")
    await _seed_field(tid, "purchaser_name", "测试采购人")
    async with AsyncSessionLocal() as db:
        resp = await get_tender_detail(tid, db)
    import json as _json

    body = _json.loads(resp.body)
    assert body["code"] == 200
    assert body["data"]["tender_id"] == tid
    assert body["data"]["clean_raw_text"] == "测试原文"


async def test_direct_get_tender_detail_not_found():
    """Direct call to get_tender_detail with non-existent ID → 404."""
    from app.api.real_demo import get_tender_detail

    async with AsyncSessionLocal() as db:
        resp = await get_tender_detail(99999, db)
    import json as _json

    body = _json.loads(resp.body)
    assert body["code"] == 404
    assert body["data"] is None


async def test_direct_get_tender_detail_with_evidence():
    """Direct call to get_tender_detail with evidence links."""
    from app.api.real_demo import get_tender_detail

    tid = await _seed_tender(core_content="编号：TEST-001")
    fid = await _seed_field(tid, "project_identifier", "TEST-001")
    eid = await _seed_evidence(tid, "TEST-001", 3, 10)
    await _seed_link(fid, eid)
    async with AsyncSessionLocal() as db:
        resp = await get_tender_detail(tid, db)
    import json as _json

    fields = _json.loads(resp.body)["data"]["fields"]
    pid_field = next(f for f in fields if f["field_name"] == "project_identifier")
    assert len(pid_field["values"][0]["evidences"]) == 1


async def test_direct_get_tender_versions():
    """Direct call to get_tender_versions for coverage tracking."""
    from app.api.real_demo import get_tender_versions

    tid = await _seed_tender(core_content="版本内容")
    async with AsyncSessionLocal() as db:
        resp = await get_tender_versions(tid, db)
    import json as _json

    body = _json.loads(resp.body)
    assert body["code"] == 200
    assert len(body["data"]["versions"]) >= 1


async def test_direct_get_tender_versions_not_found():
    """Direct call to get_tender_versions with non-existent ID → 404."""
    from app.api.real_demo import get_tender_versions

    async with AsyncSessionLocal() as db:
        resp = await get_tender_versions(99999, db)
    import json as _json

    assert _json.loads(resp.body)["code"] == 404


async def test_direct_get_tender_versions_updated():
    """Direct call to get_tender_versions with updated_at > created_at."""
    from app.api.real_demo import get_tender_versions

    tid = await _seed_tender(created_at=utc_now() - timedelta(days=30))
    async with AsyncSessionLocal() as db:
        resp = await get_tender_versions(tid, db)
    import json as _json

    versions = _json.loads(resp.body)["data"]["versions"]
    assert len(versions) == 2


async def test_direct_get_tender_organization():
    """Direct call to get_tender_organization for coverage tracking."""
    from app.api.real_demo import get_tender_organization

    tid = await _seed_tender()
    await _seed_field(tid, "purchaser_name", "北京大学第三医院")
    async with AsyncSessionLocal() as db:
        resp = await get_tender_organization(tid, db)
    import json as _json

    body = _json.loads(resp.body)
    assert body["code"] == 200
    assert body["data"]["org_name"] == "北京大学第三医院"


async def test_direct_get_tender_organization_not_found():
    """Direct call to get_tender_organization with non-existent ID → 404."""
    from app.api.real_demo import get_tender_organization

    async with AsyncSessionLocal() as db:
        resp = await get_tender_organization(99999, db)
    import json as _json

    assert _json.loads(resp.body)["code"] == 404


async def test_direct_get_tender_organization_no_fields():
    """Direct call to get_tender_organization with no purchaser/winner fields → fallback."""
    from app.api.real_demo import get_tender_organization

    tid = await _seed_tender(project_name="[Demo] 测试项目名称")
    async with AsyncSessionLocal() as db:
        resp = await get_tender_organization(tid, db)
    import json as _json

    data = _json.loads(resp.body)["data"]
    assert "org_name" in data


# ==== f) _infer_org_meta 函数 ====


def test_infer_org_meta_hospital():
    """医院 → 医疗机构。"""
    org_type, region = _infer_org_meta("北京大学第三医院", "purchaser")
    assert org_type == "医疗机构"
    assert region == "北京"


def test_infer_org_meta_university():
    """大学 → 教育机构。"""
    org_type, region = _infer_org_meta("北京大学", "purchaser")
    assert org_type == "教育机构"
    assert region == "北京"


def test_infer_org_meta_government():
    """委员会 → 政府机构。"""
    org_type, region = _infer_org_meta("北京市教育委员会", "purchaser")
    assert org_type == "政府机构"
    assert region == "北京"


def test_infer_org_meta_company():
    """公司 → 企业。"""
    org_type, region = _infer_org_meta("中国某科技有限公司", "winner")
    assert org_type == "企业"
    assert region == "未知"


def test_infer_org_meta_research_institute():
    """研究所 → 事业单位。"""
    org_type, region = _infer_org_meta("某计算技术研究所", "purchaser")
    assert org_type == "事业单位"
    assert region == "未知"


def test_infer_org_meta_center():
    """中心 → 事业单位。"""
    org_type, region = _infer_org_meta("北京市政府采购中心", "purchaser")
    assert org_type == "事业单位"
    assert region == "北京"


def test_infer_org_meta_empty_name():
    """空名称 → (未知, 未知)。"""
    org_type, region = _infer_org_meta("", "winner")
    assert org_type == "未知"
    assert region == "未知"


def test_infer_org_meta_none_name():
    """None 名称 → (未知, 未知)。"""
    org_type, region = _infer_org_meta(None, "purchaser")
    assert org_type == "未知"
    assert region == "未知"


def test_infer_org_meta_unknown_winner_defaults_to_enterprise():
    """无关键字 + winner 角色 → 默认企业。"""
    org_type, region = _infer_org_meta("某某某某", "winner")
    assert org_type == "企业"


def test_infer_org_meta_unknown_purchaser_defaults_to_government():
    """无关键字 + purchaser 角色 → 默认政府机构。"""
    org_type, region = _infer_org_meta("某某某某", "purchaser")
    assert org_type == "政府机构"


def test_infer_org_meta_region_shanghai():
    """上海 → 上海。"""
    org_type, region = _infer_org_meta("上海某公司", "winner")
    assert region == "上海"


def test_infer_org_meta_region_guangdong():
    """深圳 → 广东。"""
    org_type, region = _infer_org_meta("深圳市某局", "purchaser")
    assert region == "广东"
    assert org_type == "政府机构"


def test_infer_org_meta_region_zhejiang():
    """杭州 → 浙江。"""
    org_type, region = _infer_org_meta("杭州某大学", "purchaser")
    assert region == "浙江"
    assert org_type == "教育机构"


def test_infer_org_meta_bureau():
    """局 → 政府机构。"""
    org_type, region = _infer_org_meta("北京市财政局", "purchaser")
    assert org_type == "政府机构"
    assert region == "北京"