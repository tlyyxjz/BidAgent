"""admin / tender / subscribe API 补充测试.

覆盖目标：
- app/api/admin.py: 数据删除端点、create_api_key user not found、create_user 无效 plan、
  decommission whitespace reason
- app/api/tender.py: CRUD 全流程 + 列表筛选 + 统计 + 404
- app/api/subscribe.py: 创建/列表/查询/触发/取消 + 校验 + 404

参考 tests/test_demo_api_extra.py 的测试风格：httpx.AsyncClient + ASGITransport，
fixture 覆盖 conftest 同名 fixture 以避免同事务 drop+create 导致表丢失。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from sqlalchemy import delete
from app.models.database import AsyncSessionLocal, Base, engine
from app.models.evidence import ExtractedField
from app.models.subscription import Subscription
from app.models.tender import Tender
from app.models.tender_project import (
    NoticeSource,
    NoticeVersion,
    TenderNotice,
    TenderProject,
)
from app.models.user import ApiKey, User

# 显式导入所有模型，确保 Base.metadata 注册全部表
from app.models import (  # noqa: F401
    NoticeParticipant,
    ProjectIdentifier,
    PushLog,
)
from app.models.organization import Organization  # noqa: F401

from tests.conftest import auth_headers


# ==== fixtures（覆盖 conftest 同名 fixture，避免同事务 drop+create 丢表）====


def _admin_headers() -> dict[str, str]:
    """管理员认证头。"""
    return {"X-Admin-Secret": "test-admin-secret-12345"}


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ==== 数据 seeding 辅助函数 ====


async def _seed_user(email: str = "admin_test@test.com", plan: str = "free") -> int:
    async with AsyncSessionLocal() as db:
        u = User(email=email, plan=plan)
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return u.id


async def _seed_api_key(user_id: int, name: str = "test") -> str:
    from app.api.auth import generate_api_key, hash_api_key

    raw_key = generate_api_key()
    async with AsyncSessionLocal() as db:
        k = ApiKey(user_id=user_id, key_hash=hash_api_key(raw_key), name=name)
        db.add(k)
        await db.commit()
    return raw_key


async def _seed_tender(**kwargs) -> int:
    """插入一条 Tender 测试记录，返回其 id。"""
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
        budget_amount=Decimal("12000000.00"),
        location="北京",
    )
    defaults.update(kwargs)
    async with AsyncSessionLocal() as db:
        t = Tender(**defaults)
        db.add(t)
        await db.commit()
        await db.refresh(t)
        return t.id


async def _seed_subscription(
    user_id: int,
    raw_query: str = "北京医疗设备采购",
    **kwargs,
) -> int:
    """直接在 DB 中插入订阅，绕过 LLM parse_query。"""
    defaults = dict(
        user_id=user_id,
        raw_query=raw_query,
        parsed_filters={"raw_query": raw_query},
        frequency_cron=None,
        trigger_type="immediate",
        platforms=["ccgp"],
        push_channels=["email"],
        notify_email=None,
        webhook_url=None,
        is_active=True,
    )
    defaults.update(kwargs)
    async with AsyncSessionLocal() as db:
        s = Subscription(**defaults)
        db.add(s)
        await db.commit()
        await db.refresh(s)
        return s.id


async def _seed_notice_source_chain(source_url: str = "http://example.gov.cn/chain/001"):
    """插入 TenderProject → TenderNotice → NoticeSource 链，返回 (notice_source_id, notice_id)。"""
    async with AsyncSessionLocal() as db:
        project = TenderProject(
            canonical_name="测试删除项目",
            industry_category="goods",
            resolution_status="resolved",
        )
        db.add(project)
        await db.flush()
        notice = TenderNotice(
            project_id=project.project_id,
            notice_type="tender",
            canonical_title="测试删除公告",
            status="active",
        )
        db.add(notice)
        await db.flush()
        ns = NoticeSource(
            notice_id=notice.notice_id,
            source_url=source_url,
            source_platform="ccgp",
            platform_type="government",
            publication_role="original",
            source_quality="official_original",
            source_group=f"grp_{notice.notice_id[:8]}",
        )
        db.add(ns)
        await db.commit()
        await db.refresh(ns)
        return ns.notice_source_id, notice.notice_id


async def _seed_notice_version(
    notice_source_id: str,
    snapshot_path: str | None = None,
) -> str:
    """插入一条 NoticeVersion，返回 version_id。"""
    async with AsyncSessionLocal() as db:
        v = NoticeVersion(
            notice_source_id=notice_source_id,
            http_status=200,
            content_sha256="a" * 64,
            raw_text_sha256="b" * 64,
            change_type="initial",
            snapshot_path=snapshot_path,
        )
        db.add(v)
        await db.commit()
        await db.refresh(v)
        return v.version_id


# ========== admin.py 测试 ==========


class TestAdminUserManagement:
    """用户管理端点补充测试。"""

    async def test_list_users_with_data(self):
        """list_users 返回已 seeded 的用户。"""
        uid = await _seed_user(email="list_test@test.com")
        async with _client() as ac:
            resp = await ac.get("/admin/users", headers=_admin_headers())
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 200
        assert any(u["id"] == uid for u in body["data"])

    async def test_create_user_invalid_plan(self):
        """无效 plan → 400。"""
        async with _client() as ac:
            resp = await ac.post(
                "/admin/users",
                headers=_admin_headers(),
                json={"email": "badplan@test.com", "plan": "platinum"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == 400

    async def test_create_api_key_user_not_found(self):
        """为不存在的用户创建 API key → 404。"""
        async with _client() as ac:
            resp = await ac.post(
                "/admin/users/99999/api-keys",
                headers=_admin_headers(),
                json={"name": "ghost"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == 404

    async def test_create_api_key_success(self):
        """为存在的用户创建 API key → 201 + 明文 key。"""
        uid = await _seed_user(email="keyuser@test.com")
        async with _client() as ac:
            resp = await ac.post(
                f"/admin/users/{uid}/api-keys",
                headers=_admin_headers(),
                json={"name": "production"},
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["data"]["api_key"].startswith("sk_")
        assert body["data"]["name"] == "production"
        assert body["data"]["user_id"] == uid

    async def test_list_api_keys(self):
        """列出用户的所有 API key（不含明文）。"""
        uid = await _seed_user(email="listkeys@test.com")
        await _seed_api_key(uid, name="k1")
        await _seed_api_key(uid, name="k2")
        async with _client() as ac:
            resp = await ac.get(
                f"/admin/users/{uid}/api-keys", headers=_admin_headers()
            )
        assert resp.status_code == 200
        keys = resp.json()["data"]
        assert len(keys) == 2
        for k in keys:
            assert "api_key" not in k
            assert "key_hash" not in k


class TestAdminWhitelistDecommission:
    """白名单 decommission ValueError 路径（line 296-297）。"""

    async def test_decommission_whitespace_reason_returns_400(self):
        """whitespace-only reason 通过 Pydantic min_length=1 但被 service 拒绝 → 400。"""
        from app.core.source_whitelist import source_whitelist

        source_whitelist.reset()
        async with _client() as ac:
            resp = await ac.post(
                "/admin/sources/whitelist/ccgp.gov.cn/decommission",
                headers=_admin_headers(),
                json={"reason": "   "},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == 400
        source_whitelist.reset()


class TestAdminDataDeletion:
    """数据删除 API（v4.1 sec 13.3）。"""

    async def test_delete_by_source_url(self):
        """按来源 URL 删除数据 → 200 + deleted_counts。"""
        await _seed_tender(source_url="http://example.gov.cn/delete/url/001")
        async with _client() as ac:
            resp = await ac.post(
                "/admin/deletion/by-source-url",
                headers=_admin_headers(),
                json={
                    "target": "http://example.gov.cn/delete/url/001",
                    "request_basis": "GDPR Article 17",
                    "operator": "admin",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 200
        assert body["data"]["scope"] == "source_url"
        assert body["data"]["deleted_counts"].get("tenders", 0) >= 1
        assert body["data"]["error"] is None

    async def test_delete_by_source_platform(self):
        """按来源平台删除数据 → 200。"""
        await _seed_tender(
            source_platform="testplat",
            source_url="http://example.gov.cn/delete/plat/001",
        )
        async with _client() as ac:
            resp = await ac.post(
                "/admin/deletion/by-source-platform",
                headers=_admin_headers(),
                json={
                    "target": "testplat",
                    "request_basis": "平台下架",
                    "operator": "admin",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["scope"] == "source_platform"
        assert body["data"]["deleted_counts"].get("tenders", 0) >= 1

    async def test_delete_notice_source_instance(self):
        """删除单个公告来源实例 → 200。"""
        ns_id, _ = await _seed_notice_source_chain(
            source_url="http://example.gov.cn/delete/ns/001"
        )
        async with _client() as ac:
            resp = await ac.post(
                f"/admin/deletion/notice-source/{ns_id}",
                headers=_admin_headers(),
                json={
                    "target": ns_id,
                    "request_basis": "用户注销",
                    "operator": "admin",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["scope"] == "notice_source_instance"
        assert body["data"]["deleted_counts"].get("notice_sources", 0) == 1

    async def test_delete_notice_source_instance_not_found(self):
        """不存在的 source_id → 200 + error 字段。"""
        async with _client() as ac:
            resp = await ac.post(
                "/admin/deletion/notice-source/01HHHHHHHHHHHHHHHHHHHHHHHH",
                headers=_admin_headers(),
                json={
                    "target": "01HHHHHHHHHHHHHHHHHHHHHHHH",
                    "request_basis": "测试",
                    "operator": "admin",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["error"] is not None

    async def test_delete_page_snapshot_no_path(self):
        """删除页面快照：版本无 snapshot_path → error='no snapshot_path on this version'。"""
        ns_id, _ = await _seed_notice_source_chain(
            source_url="http://example.gov.cn/delete/snap/001"
        )
        vid = await _seed_notice_version(ns_id, snapshot_path=None)
        async with _client() as ac:
            resp = await ac.post(
                f"/admin/deletion/page-snapshot/{vid}",
                headers=_admin_headers(),
                json={
                    "target": vid,
                    "request_basis": "GDPR",
                    "operator": "admin",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["scope"] == "page_snapshot"
        assert body["data"]["error"] is not None

    async def test_delete_page_snapshot_not_found(self):
        """删除页面快照：版本不存在 → error 字段。"""
        async with _client() as ac:
            resp = await ac.post(
                "/admin/deletion/page-snapshot/01HHHHHHHHHHHHHHHHHHHHHHHH",
                headers=_admin_headers(),
                json={
                    "target": "01HHHHHHHHHHHHHHHHHHHHHHHH",
                    "request_basis": "GDPR",
                    "operator": "admin",
                },
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["error"] is not None

    async def test_delete_user_authorized_data(self):
        """删除用户授权数据 → 200 + users=1。"""
        uid = await _seed_user(email="delete_user@test.com")
        await _seed_api_key(uid)
        await _seed_subscription(uid)
        async with _client() as ac:
            resp = await ac.post(
                f"/admin/deletion/user-authorized-data/{uid}",
                headers=_admin_headers(),
                json={
                    "target": str(uid),
                    "request_basis": "用户注销",
                    "operator": "admin",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["scope"] == "user_authorized_data"
        assert body["data"]["deleted_counts"].get("users", 0) == 1
        assert body["data"]["deleted_counts"].get("api_keys", 0) >= 1

    async def test_delete_user_authorized_data_not_found(self):
        """删除不存在的用户 → 200 + error。"""
        async with _client() as ac:
            resp = await ac.post(
                "/admin/deletion/user-authorized-data/99999",
                headers=_admin_headers(),
                json={
                    "target": "99999",
                    "request_basis": "测试",
                    "operator": "admin",
                },
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["error"] is not None


# ========== tender.py 测试 ==========


class TestTenderAdminAPI:
    """招标信息 Admin 路由（需 X-Admin-Secret）。"""

    async def test_create_tender(self):
        """POST /api/tenders → 201。"""
        async with _client() as ac:
            resp = await ac.post(
                "/api/tenders",
                headers=_admin_headers(),
                json={
                    "project_name": "测试创建招标项目",
                    "source_platform": "ccgp",
                    "source_url": "http://example.gov.cn/create/001",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 201
        assert body["data"]["project_name"] == "测试创建招标项目"

    async def test_create_tenders_batch(self):
        """POST /api/tenders/batch → 201 + count。"""
        async with _client() as ac:
            resp = await ac.post(
                "/api/tenders/batch",
                headers=_admin_headers(),
                json={
                    "items": [
                        {
                            "project_name": "批量项目一",
                            "source_platform": "ccgp",
                            "source_url": "http://example.gov.cn/batch/001",
                        },
                        {
                            "project_name": "批量项目二",
                            "source_platform": "ccgp",
                            "source_url": "http://example.gov.cn/batch/002",
                        },
                    ]
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["count"] == 2

    async def test_delete_tender(self):
        """DELETE /api/tenders/{id} → 200。"""
        tid = await _seed_tender(source_url="http://example.gov.cn/del/001")
        async with _client() as ac:
            resp = await ac.delete(
                f"/api/tenders/{tid}", headers=_admin_headers()
            )
        assert resp.status_code == 200
        assert resp.json()["msg"] == "已删除"

    async def test_delete_tender_not_found(self):
        """DELETE 不存在的 tender → 404。"""
        async with _client() as ac:
            resp = await ac.delete(
                "/api/tenders/99999", headers=_admin_headers()
            )
        assert resp.status_code == 404


class TestTenderPublicAPI:
    """招标信息公共查询路由（需 Bearer API key）。"""

    async def _authed_client(self):
        uid = await _seed_user(email="tender_pub@test.com")
        raw_key = await _seed_api_key(uid)
        return auth_headers(raw_key)

    async def test_list_tenders_empty(self):
        """空库列表查询。"""
        headers = await self._authed_client()
        # 查询前显式清空 tender 表：全量运行时偶发有前序测试/后台任务的残留数据，
        # 显式清理保证本测试验证的确实是空库查询路径（测试间隔离）。
        async with AsyncSessionLocal() as db:
            await db.execute(delete(Tender))
            await db.commit()
        async with _client() as ac:
            resp = await ac.get("/api/tenders", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["items"] == []
        assert body["data"]["total"] == 0

    async def test_list_tenders_with_filters(self):
        """多维度筛选：platform + region + topic + notice_type + budget。"""
        await _seed_tender(
            project_name="医疗设备采购",
            source_platform="ccgp",
            location="北京",
            notice_type="tender",
            budget_amount=Decimal("500000"),
            source_url="http://example.gov.cn/filter/001",
        )
        await _seed_tender(
            project_name="IT设备采购",
            source_platform="chinabidding",
            location="上海",
            notice_type="award",
            budget_amount=Decimal("2000000"),
            source_url="http://example.gov.cn/filter/002",
        )
        headers = await self._authed_client()
        async with _client() as ac:
            # 按 platform 筛选
            resp = await ac.get(
                "/api/tenders",
                headers=headers,
                params={"platform": "ccgp"},
            )
        body = resp.json()
        assert body["data"]["total"] == 1
        assert body["data"]["items"][0]["source_platform"] == "ccgp"

        async with _client() as ac:
            # 按 topic 筛选
            resp2 = await ac.get(
                "/api/tenders",
                headers=headers,
                params={"topic": "医疗"},
            )
        assert resp2.json()["data"]["total"] == 1

        async with _client() as ac:
            # 按 min_budget 筛选
            resp3 = await ac.get(
                "/api/tenders",
                headers=headers,
                params={"min_budget": 1000000},
            )
        assert resp3.json()["data"]["total"] == 1

    async def test_list_tenders_pagination(self):
        """分页 limit + offset。"""
        for i in range(5):
            await _seed_tender(
                project_name=f"分页项目{i}",
                source_url=f"http://example.gov.cn/page/{i}",
            )
        headers = await self._authed_client()
        async with _client() as ac:
            resp = await ac.get(
                "/api/tenders",
                headers=headers,
                params={"limit": 2, "offset": 0},
            )
        body = resp.json()
        assert body["data"]["total"] == 5
        assert len(body["data"]["items"]) == 2
        assert body["data"]["limit"] == 2
        assert body["data"]["offset"] == 0

    async def test_tender_stats(self):
        """统计概览：total + by_platform + total_budget。"""
        await _seed_tender(
            source_platform="ccgp",
            notice_type="tender",
            budget_amount=Decimal("100000"),
            source_url="http://example.gov.cn/stats/001",
        )
        await _seed_tender(
            source_platform="ccgp",
            notice_type="award",
            budget_amount=Decimal("200000"),
            source_url="http://example.gov.cn/stats/002",
        )
        headers = await self._authed_client()
        async with _client() as ac:
            resp = await ac.get("/api/tenders/stats/overview", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["total"] == 2
        assert body["data"]["by_platform"].get("ccgp") == 2
        assert body["data"]["total_budget"] == 300000.0

    async def test_get_tender_detail(self):
        """查询单条详情。"""
        tid = await _seed_tender(source_url="http://example.gov.cn/detail/001")
        headers = await self._authed_client()
        async with _client() as ac:
            resp = await ac.get(f"/api/tenders/{tid}", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["id"] == tid

    async def test_get_tender_not_found(self):
        """查询不存在的 tender → 404。"""
        headers = await self._authed_client()
        async with _client() as ac:
            resp = await ac.get("/api/tenders/99999", headers=headers)
        assert resp.status_code == 404


# ========== subscribe.py 测试 ==========


class TestSubscribeAPI:
    """订阅 API 路由。"""

    @pytest.fixture(autouse=True)
    def _mock_parse_query(self, monkeypatch):
        """mock LLM parse_query 避免真实 API 调用。"""
        from app.llm.schemas import ParsedFilters
        from app.scheduler import subscription as sub_module

        async def _mock(query: str) -> ParsedFilters:
            return ParsedFilters(
                raw_query=query,
                topic=None,
                region=None,
                time_range="30d",
                frequency=None,
                trigger_type="immediate",
            )

        monkeypatch.setattr(sub_module, "parse_query", _mock)

    async def _authed_client(self):
        uid = await _seed_user(email="sub_user@test.com")
        raw_key = await _seed_api_key(uid)
        return uid, auth_headers(raw_key)

    async def test_create_subscription(self):
        """POST /api/subscriptions → 201。"""
        _uid, headers = await self._authed_client()
        async with _client() as ac:
            resp = await ac.post(
                "/api/subscriptions",
                headers=headers,
                json={
                    "raw_query": "北京医疗设备采购",
                    "platforms": ["ccgp"],
                    "push_channels": ["email"],
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["subscription_id"] > 0

    async def test_create_subscription_invalid_channel(self):
        """不支持的推送渠道 → 422。"""
        _uid, headers = await self._authed_client()
        async with _client() as ac:
            resp = await ac.post(
                "/api/subscriptions",
                headers=headers,
                json={
                    "raw_query": "北京医疗设备采购",
                    "push_channels": ["telegram"],
                },
            )
        assert resp.status_code == 422

    async def test_create_subscription_dedup_channels(self):
        """重复推送渠道去重。"""
        _uid, headers = await self._authed_client()
        async with _client() as ac:
            resp = await ac.post(
                "/api/subscriptions",
                headers=headers,
                json={
                    "raw_query": "北京医疗设备采购",
                    "push_channels": ["email", "email", "EMAIL"],
                },
            )
        assert resp.status_code == 200

    async def test_list_subscriptions(self):
        """GET /api/subscriptions → 列表 + 分页。"""
        uid, headers = await self._authed_client()
        await _seed_subscription(uid)
        await _seed_subscription(uid, raw_query="上海IT设备")
        async with _client() as ac:
            resp = await ac.get("/api/subscriptions", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["total"] == 2
        assert len(body["data"]["items"]) == 2

    async def test_list_subscriptions_pagination(self):
        """分页 limit + offset。"""
        uid, headers = await self._authed_client()
        for i in range(3):
            await _seed_subscription(uid, raw_query=f"查询{i}")
        async with _client() as ac:
            resp = await ac.get(
                "/api/subscriptions",
                headers=headers,
                params={"limit": 1, "offset": 0},
            )
        body = resp.json()
        assert body["data"]["total"] == 3
        assert len(body["data"]["items"]) == 1

    async def test_get_subscription(self):
        """GET /api/subscriptions/{id} → 单个订阅。"""
        uid, headers = await self._authed_client()
        sid = await _seed_subscription(uid)
        async with _client() as ac:
            resp = await ac.get(
                f"/api/subscriptions/{sid}", headers=headers
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["id"] == sid
        assert body["data"]["raw_query"] == "北京医疗设备采购"

    async def test_get_subscription_not_found(self):
        """查询不存在的订阅 → 404。"""
        _uid, headers = await self._authed_client()
        async with _client() as ac:
            resp = await ac.get(
                "/api/subscriptions/99999", headers=headers
            )
        assert resp.status_code == 404

    async def test_get_subscription_wrong_user(self):
        """查询他人的订阅 → 404（无权访问）。"""
        uid1, _ = await self._authed_client()
        sid = await _seed_subscription(uid1)
        # 创建第二个用户
        uid2 = await _seed_user(email="sub_user2@test.com")
        raw_key2 = await _seed_api_key(uid2)
        headers2 = auth_headers(raw_key2)
        async with _client() as ac:
            resp = await ac.get(
                f"/api/subscriptions/{sid}", headers=headers2
            )
        assert resp.status_code == 404

    async def test_trigger_subscription_no_new(self, monkeypatch):
        """触发订阅：无新招标信息 → status=no_new。"""
        from app.scheduler import subscription as sub_module

        async def _mock_collect(sub, filters):
            return {}

        monkeypatch.setattr(
            "app.scheduler.collector.collect_new_tenders", _mock_collect
        )

        uid, headers = await self._authed_client()
        sid = await _seed_subscription(uid)
        async with _client() as ac:
            resp = await ac.post(
                f"/api/subscriptions/{sid}/trigger", headers=headers
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["status"] in ("no_new", "skipped")

    async def test_trigger_subscription_not_found(self, monkeypatch):
        """触发不存在的订阅 → 200 + status=skipped（trigger_subscription 返回 skipped）。"""
        from app.scheduler import subscription as sub_module

        async def _mock_collect(sub, filters):
            return {}

        monkeypatch.setattr(
            "app.scheduler.collector.collect_new_tenders", _mock_collect
        )

        _uid, headers = await self._authed_client()
        async with _client() as ac:
            resp = await ac.post(
                "/api/subscriptions/99999/trigger", headers=headers
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "skipped"

    async def test_list_sub_tenders_empty(self):
        """查询订阅下的招标信息：空 → 200 + 空列表。"""
        uid, headers = await self._authed_client()
        sid = await _seed_subscription(uid)
        async with _client() as ac:
            resp = await ac.get(
                f"/api/subscriptions/{sid}/tenders", headers=headers
            )
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["data"], list)

    async def test_list_sub_tenders_all(self):
        """查询订阅下全部招标信息（only_unpushed=False）。"""
        uid, headers = await self._authed_client()
        sid = await _seed_subscription(uid)
        await _seed_tender(source_url="http://example.gov.cn/sub_tender/001")
        async with _client() as ac:
            resp = await ac.get(
                f"/api/subscriptions/{sid}/tenders",
                headers=headers,
                params={"only_unpushed": False},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) >= 1

    async def test_cancel_subscription(self):
        """DELETE /api/subscriptions/{id} → 取消订阅。"""
        uid, headers = await self._authed_client()
        sid = await _seed_subscription(uid)
        async with _client() as ac:
            resp = await ac.delete(
                f"/api/subscriptions/{sid}", headers=headers
            )
        assert resp.status_code == 200
        assert resp.json()["msg"] == "订阅已取消"
        # 验证 is_active=False
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select

            result = await db.execute(
                select(Subscription).where(Subscription.id == sid)
            )
            sub = result.scalar_one_or_none()
            assert sub is not None
            assert sub.is_active is False

    async def test_cancel_subscription_not_found(self):
        """取消不存在的订阅 → 404。"""
        _uid, headers = await self._authed_client()
        async with _client() as ac:
            resp = await ac.delete(
                "/api/subscriptions/99999", headers=headers
            )
        assert resp.status_code == 404