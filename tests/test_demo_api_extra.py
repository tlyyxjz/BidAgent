"""demo_api.py 单元测试 — 覆盖前端使用的 5 个 /api/demo/* 端点。

被测端点（前端 3 个页面使用）：
- GET  /api/demo/collector/status         (workbench.html)
- GET  /api/demo/organizations/{org_id}   (org_profile.html)
- POST /api/demo/pipeline/start           (chat.html)
- GET  /api/demo/pipeline/status          (chat.html)
- GET  /api/demo/report                   (chat.html)

参考 tests/test_v41_api.py 的测试风格：httpx.AsyncClient + ASGITransport，
直接 seeding 真实数据库记录（非 mock 数据），fixture 覆盖 conftest 同名 fixture
以避免同事务 drop+create 导致表丢失。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.database import AsyncSessionLocal, Base, engine
from app.models.job import (
    JOB_COMPLETED,
    JOB_FAILED,
    JOB_PENDING,
    JOB_RUNNING,
    ScrapeJob,
)
from app.models.tender import Tender
from app.models.user import User

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


async def _seed_user(email: str = "demo@test.com") -> int:
    async with AsyncSessionLocal() as db:
        u = User(email=email, plan="free")
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return u.id


async def _seed_tender(**kwargs) -> int:
    """插入一条 Tender 测试记录，返回其 id。

    created_at 显式设为本地当前时间（naive），确保 demo_collector_status 中
    ``Tender.created_at >= today_start``（local midnight）比较通过。
    """
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
        created_at=datetime.now(),
    )
    defaults.update(kwargs)
    async with AsyncSessionLocal() as db:
        t = Tender(**defaults)
        db.add(t)
        await db.commit()
        await db.refresh(t)
        return t.id


async def _seed_scrape_job(user_id: int, **kwargs) -> str:
    """插入一条 ScrapeJob 测试记录，返回其 id。"""
    now = datetime.now()
    defaults = dict(
        id=str(uuid.uuid4()),
        user_id=user_id,
        url="http://www.ccgp.gov.cn/test",
        status=JOB_COMPLETED,
        result_data=json.dumps(
            {
                "ingest": {
                    "inserted": 5,
                    "duplicates": 2,
                    "platforms_collected": ["ccgp"],
                }
            }
        ),
        created_at=now,
        completed_at=now,
    )
    defaults.update(kwargs)
    async with AsyncSessionLocal() as db:
        j = ScrapeJob(**defaults)
        db.add(j)
        await db.commit()
        await db.refresh(j)
        return j.id


# ==== a) GET /api/demo/collector/status ====


async def test_collector_status_empty_database():
    """空数据库：所有计数字段为 0，4 个平台状态为 idle。"""
    async with _client() as ac:
        resp = await ac.get("/api/demo/collector/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    data = body["data"]
    assert data["active_jobs"] == 0
    assert data["today_collected"] == 0
    assert data["today_deduplicated"] == 0
    assert data["today_failed"] == 0
    # 4 个固定平台全部 idle
    assert len(data["platforms"]) == 4
    for p in data["platforms"]:
        assert p["status"] == "idle"
        assert p["collected"] == 0
        assert p["failed_count"] == 0
        assert p["last_fetch"] is None
    assert data["recent_batches"] == []


async def test_collector_status_with_scrape_jobs():
    """有 ScrapeJob 记录时：active_jobs / today_failed / today_deduplicated / recent_batches 聚合正确。"""
    uid = await _seed_user()
    # 1 pending + 1 running → active_jobs = 2
    await _seed_scrape_job(uid, status=JOB_PENDING, url="http://www.ccgp.gov.cn/pending")
    await _seed_scrape_job(uid, status=JOB_RUNNING, url="http://www.ccgp.gov.cn/running")
    # 1 failed → today_failed = 1
    await _seed_scrape_job(uid, status=JOB_FAILED, url="http://www.ccgp.gov.cn/failed")
    # 1 completed with result_data → recent_batches 含 1 条，today_deduplicated = 2
    await _seed_scrape_job(uid, status=JOB_COMPLETED, url="http://www.ccgp.gov.cn/done")

    async with _client() as ac:
        resp = await ac.get("/api/demo/collector/status")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["active_jobs"] == 2
    assert data["today_failed"] == 1
    assert data["today_deduplicated"] == 2
    # recent_batches 应包含 1 条已完成批次
    assert len(data["recent_batches"]) == 1
    batch = data["recent_batches"][0]
    assert batch["inserted"] == 5
    assert batch["duplicates"] == 2
    assert batch["platforms"] == 1
    # ccgp 平台应有 failed_count=1
    ccgp = next(p for p in data["platforms"] if p["code"] == "ccgp")
    assert ccgp["failed_count"] == 1


async def test_collector_status_with_tenders_platform_distribution():
    """有 Tender 记录时：按 source_platform 聚合到 4 个固定平台的 collected 计数。"""
    await _seed_tender(source_platform="ccgp", source_url="http://www.ccgp.gov.cn/a")
    await _seed_tender(source_platform="ccgp", source_url="http://www.ccgp.gov.cn/b")
    await _seed_tender(
        source_platform="chinabidding", source_url="http://www.chinabidding.cn/c"
    )
    await _seed_tender(source_platform="ggzy", source_url="http://www.ggzy.gov.cn/d")

    async with _client() as ac:
        resp = await ac.get("/api/demo/collector/status")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["today_collected"] == 4
    platforms = {p["code"]: p for p in data["platforms"]}
    assert platforms["ccgp"]["collected"] == 2
    assert platforms["chinabidding"]["collected"] == 1
    assert platforms["ggzy"]["collected"] == 1
    assert platforms["qlm"]["collected"] == 0
    # ccgp 有采集记录，last_fetch 应被填充
    assert platforms["ccgp"]["last_fetch"] is not None


# ==== b) GET /api/demo/organizations/{org_id} ====


async def test_org_profile_existing_org():
    """存在的组织 ID（org_001）返回预置画像数据。"""
    async with _client() as ac:
        resp = await ac.get("/api/demo/organizations/org_001")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    data = body["data"]
    assert data["org_id"] == "org_001"
    assert data["org_name"] == "北第三医院"
    assert data["org_type"] == "医疗机构"
    assert data["region"] == "北京市海淀区"


async def test_org_profile_non_existing_org():
    """不存在的组织 ID：返回 200 + fallback org_name（mock 端点不返回 404）。"""
    async with _client() as ac:
        resp = await ac.get("/api/demo/organizations/nonexistent_org")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["org_id"] == "nonexistent_org"
    assert data["org_name"] == "组织 nonexistent_org"


async def test_org_profile_activity_90d_structure():
    """验证 activity_90d 结构：total/tender_count/award_count + daily 90 天。"""
    async with _client() as ac:
        resp = await ac.get("/api/demo/organizations/org_002")
    data = resp.json()["data"]
    activity = data["activity_90d"]
    assert isinstance(activity["total"], int)
    assert isinstance(activity["tender_count"], int)
    assert isinstance(activity["award_count"], int)
    assert len(activity["daily"]) == 90
    # 每条 daily 含 date + count
    for d in activity["daily"]:
        assert "date" in d
        assert "count" in d


async def test_org_profile_top3_purchasers_and_waste_bids():
    """验证 top3_purchasers / waste_bid_related / data_completeness 字段。

    此 mock 端点不输出信用评分（无 observation_score / credit_dimensions 字段），
    对齐 v4.1 §9.1「不输出信用评分」。
    """
    async with _client() as ac:
        resp = await ac.get("/api/demo/organizations/org_003")
    data = resp.json()["data"]
    # top3_purchasers
    top3 = data["top3_purchasers"]
    assert len(top3) == 3
    for p in top3:
        assert "name" in p
        assert "count" in p
        assert "ratio" in p
    assert isinstance(data["top3_concentration"], float)
    # waste_bid_related
    waste = data["waste_bid_related"]
    assert isinstance(waste, list)
    assert data["waste_bid_count"] == len(waste)
    for w in waste:
        assert "project_name" in w
        assert "waste_date" in w
        assert "reason" in w
    # data_completeness
    dc = data["data_completeness"]
    assert "platforms" in dc
    assert "total_notices" in dc
    assert "completeness_score" in dc
    # 此 mock 端点不输出信用评分
    assert "observation_score" not in data
    assert "credit_dimensions" not in data


# ==== c) POST /api/demo/pipeline/start ====


async def test_pipeline_start_normal(monkeypatch):
    """正常启动：mock run_pipeline 返回 session_id。"""
    from unittest.mock import AsyncMock

    mock_run = AsyncMock(return_value="mock-session-id")
    monkeypatch.setattr("app.agents.pipeline.run_pipeline", mock_run)

    async with _client() as ac:
        resp = await ac.post(
            "/api/demo/pipeline/start", params={"query": "医疗设备采购"}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    assert body["data"]["session_id"] == "mock-session-id"
    mock_run.assert_called_once_with({"query": "医疗设备采购"})


async def test_pipeline_start_missing_query():
    """缺少 query 参数：FastAPI 返回 422 校验错误。"""
    async with _client() as ac:
        resp = await ac.post("/api/demo/pipeline/start")
    assert resp.status_code == 422


# ==== d) GET /api/demo/pipeline/status ====


async def test_pipeline_status_valid_session():
    """有效 session_id：预先注入 session 到内存存储，返回 200 + session 数据。"""
    from app.agents.pipeline import _sessions, _sessions_lock

    test_sid = "test-valid-session-id"
    now_loop_time = 0.0
    session_data = {
        "session_id": test_sid,
        "stage": "intent",
        "progress": 10,
        "message": "Pipeline 启动中",
        "started_at": now_loop_time,
        "updated_at": now_loop_time,
        "finished_at": None,
        "error": None,
        "result": None,
        "stages": {
            "intent": {
                "status": "running",
                "started_at": now_loop_time,
                "finished_at": None,
            },
        },
    }
    async with _sessions_lock:
        _sessions[test_sid] = session_data

    async with _client() as ac:
        resp = await ac.get("/api/demo/pipeline/status", params={"sid": test_sid})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    assert body["data"]["session_id"] == test_sid
    assert body["data"]["stage"] == "intent"
    assert body["data"]["progress"] == 10


async def test_pipeline_status_invalid_session():
    """无效 session_id：返回 404。"""
    async with _client() as ac:
        resp = await ac.get(
            "/api/demo/pipeline/status", params={"sid": "nonexistent-sid-xxx"}
        )
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == 404
    assert body["data"] is None


# ==== e) GET /api/demo/report ====


async def test_report_generates_word_file():
    """生成 Word 报告：验证返回 FileResponse（content-type + content-disposition + 非空 body）。"""
    await _seed_tender()
    async with _client() as ac:
        resp = await ac.get("/api/demo/report", params={"query": "测试报告查询"})
    assert resp.status_code == 200
    # FileResponse 的 content-type 为 docx MIME
    content_type = resp.headers.get("content-type", "")
    assert (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        in content_type
    )
    # content-disposition 包含 attachment
    content_disposition = resp.headers.get("content-disposition", "")
    assert "attachment" in content_disposition
    assert ".docx" in content_disposition
    # body 非空（docx 文件字节）
    assert len(resp.content) > 0


async def test_report_default_query():
    """默认 query 参数（不传 query 时使用 "医疗设备采购"）。"""
    await _seed_tender()
    async with _client() as ac:
        resp = await ac.get("/api/demo/report")
    assert resp.status_code == 200
    content_disposition = resp.headers.get("content-disposition", "")
    assert "attachment" in content_disposition
    assert ".docx" in content_disposition
    assert len(resp.content) > 0


# ==== f) GET /api/demo/orgs/by-name/{name} (demo_org_by_name) ====


async def test_demo_org_by_name_existing_org_in_index():
    """命中 _ORG_INDEX（无真实数据）→ data_source=no_data，5 维度 score=None。

    验证返回的 6 维观察信号结构：org_id/org_name/org_type/region/observation_score/
    credit_dimensions/data_source/activity_90d/top3_purchasers/waste_bid_related/
    data_completeness。
    """
    async with _client() as ac:
        resp = await ac.get("/api/demo/orgs/by-name/北京大学第三医院")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    data = body["data"]
    # 基本字段
    assert data["org_name"] == "北京大学第三医院"
    assert data["org_type"] == "医疗机构"
    assert data["region"] == "北京市海淀区"
    assert data["org_id"] == "org_001"
    # 真实数据库无记录 → no_data
    assert data["data_source"] == "no_data"
    # v4.1 §9.1: observation_score 必须为 None（不输出信用评分）
    assert data["observation_score"] is None
    # observation_note 存在
    assert isinstance(data["observation_note"], str)


async def test_demo_org_by_name_unknown_org():
    """未命中真实数据与样本库 → 占位元数据（org_type=未知类型，region=未登记区域）。"""
    async with _client() as ac:
        resp = await ac.get("/api/demo/orgs/by-name/不存在组织XYZ123")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["data_source"] == "no_data"
    assert data["org_type"] == "未知类型"
    assert data["region"] == "未登记区域"
    assert data["observation_score"] is None
    # top3_purchasers 仍可渲染（不伪造评分）
    assert isinstance(data["top3_purchasers"], list)
    assert len(data["top3_purchasers"]) == 3


async def test_demo_org_by_name_observation_score_always_none():
    """v4.1 §9.1: observation_score 始终为 None（无论是否命中样本库）。"""
    for name in ["北京协和医院", "上海市教育委员会", "深圳卫健委", "完全不存在组织ABC"]:
        async with _client() as ac:
            resp = await ac.get(f"/api/demo/orgs/by-name/{name}")
        assert resp.status_code == 200
        assert resp.json()["data"]["observation_score"] is None


async def test_demo_org_by_name_activity_90d_structure():
    """验证 activity_90d 结构：total/tender_count/award_count + 90 天 daily。"""
    async with _client() as ac:
        resp = await ac.get("/api/demo/orgs/by-name/北京协和医院")
    data = resp.json()["data"]
    activity = data["activity_90d"]
    assert isinstance(activity["total"], int)
    assert isinstance(activity["tender_count"], int)
    assert isinstance(activity["award_count"], int)
    assert len(activity["daily"]) == 90
    for d in activity["daily"]:
        assert "date" in d
        assert "count" in d


async def test_demo_org_by_name_top3_purchasers_and_waste_bids():
    """验证 top3_purchasers / waste_bid_related / data_completeness 字段结构。"""
    async with _client() as ac:
        resp = await ac.get("/api/demo/orgs/by-name/中国科学院计算技术研究所")
    data = resp.json()["data"]
    # top3_purchasers
    top3 = data["top3_purchasers"]
    assert len(top3) == 3
    for p in top3:
        assert "name" in p
        assert "count" in p
        assert "ratio" in p
    assert isinstance(data["top3_concentration"], (int, float))
    # waste_bid_related
    waste = data["waste_bid_related"]
    assert isinstance(waste, list)
    assert data["waste_bid_count"] == len(waste)
    for w in waste:
        assert "project_name" in w
        assert "waste_date" in w
        assert "reason" in w
    # data_completeness
    dc = data["data_completeness"]
    assert "platforms" in dc
    assert "total_notices" in dc
    assert "completeness_score" in dc
    assert "tender_count" in dc
    assert "award_count" in dc
    assert "correction_count" in dc
    assert "time_range" in dc
    assert "missing_fields" in dc


async def test_demo_org_by_name_credit_dimensions_structure():
    """验证 credit_dimensions 5 维度 key/name/icon/display/description（对齐 observation_signals.py）。

    5 维度对齐 observation_signals.py 口径（v4.1 第九章）：
    concentration / amount_anomaly / frequency / region / purchaser。
    """
    async with _client() as ac:
        resp = await ac.get("/api/demo/orgs/by-name/北京市教育委员会")
    data = resp.json()["data"]
    dims = data["credit_dimensions"]
    expected_keys = {"concentration", "amount_anomaly", "frequency", "region", "purchaser"}
    actual_keys = {d["key"] for d in dims}
    assert actual_keys == expected_keys
    for d in dims:
        assert "name" in d
        assert "icon" in d
        assert "display" in d
        assert "description" in d
        # v4.1 §9.1: 每个维度 score/grade 为 None（不输出信用评分）
        assert d["score"] is None
        assert d["grade"] is None


async def test_demo_org_by_name_real_data_source():
    """DB 有真实 ExtractedField → data_source=real，5 维度 score 仍为 None。

    验证真实数据命中时 _build_5d_credit 路径被覆盖。
    """
    from app.models.evidence import ExtractedField

    tid = await _seed_tender(tender_org="测试真实组织A", notice_type="tender")
    async with AsyncSessionLocal() as db:
        f = ExtractedField(
            tender_id=tid,
            field_name="purchaser_name",
            field_status="present",
            raw_value="测试真实组织A",
            normalized_value="测试真实组织A",
            support_level="direct",
        )
        db.add(f)
        await db.commit()
    async with _client() as ac:
        resp = await ac.get("/api/demo/orgs/by-name/测试真实组织A")
    data = resp.json()["data"]
    assert data["data_source"] == "real"
    assert data["observation_score"] is None
    assert len(data["credit_dimensions"]) == 5
    for d in data["credit_dimensions"]:
        assert d["score"] is None
        assert d["grade"] is None
    # real 模式下 activity 来源于真实聚合
    assert data["activity_90d"]["total"] >= 1
    # top3_purchasers 来源于真实查询（至少 1 条）
    assert len(data["top3_purchasers"]) >= 1


async def test_demo_org_by_name_path_with_slash():
    """name:path 路由支持包含斜杠的组织名。"""
    async with _client() as ac:
        resp = await ac.get("/api/demo/orgs/by-name/某地/分部")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["org_name"] == "某地/分部"


async def test_demo_org_by_name_data_completeness_platforms():
    """data_completeness.platforms 在 no_data 模式下使用 fallback 列表。"""
    async with _client() as ac:
        resp = await ac.get("/api/demo/orgs/by-name/北京市教育委员会")
    dc = resp.json()["data"]["data_completeness"]
    assert isinstance(dc["platforms"], list)
    assert len(dc["platforms"]) >= 1

# ==== g) Direct function calls (for coverage tracking) ====
# httpx.ASGITransport may not be tracked by coverage.py; direct calls ensure coverage.


async def test_demo_org_by_name_direct_call_no_data():
    """Direct call to demo_org_by_name (no_data path) for coverage tracking."""
    import json as _json

    from app.api.demo_api import demo_org_by_name

    async with AsyncSessionLocal() as db:
        resp = await demo_org_by_name("北京大学第三医院", db)
    data = _json.loads(resp.body)["data"]
    assert data["data_source"] == "no_data"
    assert data["observation_score"] is None
    assert data["org_type"] == "医疗机构"
    assert len(data["credit_dimensions"]) == 5


async def test_demo_org_by_name_direct_call_real_data():
    """Direct call to demo_org_by_name (real data path) for coverage tracking."""
    import json as _json

    from app.api.demo_api import demo_org_by_name
    from app.models.evidence import ExtractedField

    tid = await _seed_tender(tender_org="测试真实组织B", notice_type="tender")
    async with AsyncSessionLocal() as db:
        f = ExtractedField(
            tender_id=tid,
            field_name="purchaser_name",
            field_status="present",
            raw_value="测试真实组织B",
            normalized_value="测试真实组织B",
            support_level="direct",
        )
        db.add(f)
        await db.commit()
    async with AsyncSessionLocal() as db:
        resp = await demo_org_by_name("测试真实组织B", db)
    data = _json.loads(resp.body)["data"]
    assert data["data_source"] == "real"
    assert data["observation_score"] is None
    assert len(data["credit_dimensions"]) == 5


async def test_demo_org_by_name_direct_call_unknown():
    """Direct call to demo_org_by_name (unknown org) for coverage tracking."""
    import json as _json

    from app.api.demo_api import demo_org_by_name

    async with AsyncSessionLocal() as db:
        resp = await demo_org_by_name("不存在组织XYZ", db)
    data = _json.loads(resp.body)["data"]
    assert data["data_source"] == "no_data"
    assert data["org_type"] == "未知类型"
    assert data["region"] == "未登记区域"