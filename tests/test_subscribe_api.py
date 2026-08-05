"""app/api/subscribe.py 订阅 API 路由测试。

覆盖目标：
- 创建订阅：成功 / 无认证 / 无效平台 / 含邮件推送渠道
- 查询订阅：列表 / 详情 / 不存在 / 不能查看他人订阅
- 触发订阅：成功 / 不存在
- 取消订阅：成功 / 重复取消（已 inactive）
- 查询推送记录：订阅下的招标信息

参考 tests/test_api_extra.py 的测试风格：
- 直接用 AsyncSessionLocal 注入订阅/Tender 数据，绕过 LLM parse_query
- mock 掉 collect_new_tenders / push_to_channels 避免真实 HTTP / SMTP
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.models.database import AsyncSessionLocal
from app.models.subscription import Subscription
from app.models.tender import Tender
from app.models.user import ApiKey, User
from app.api.auth import generate_api_key, hash_api_key

from tests.conftest import auth_headers


# ==== fixtures ====


@pytest.fixture
def mock_parse_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """mock LLM parse_query 避免真实 API 调用。

    创建订阅走 POST /api/subscriptions → create_subscription → parse_query，
    必须 mock 否则无 DEEPSEEK_API_KEY 时会走降级路径（虽不报错但行为不稳定）。
    """
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


@pytest.fixture
def mock_collect_and_push(monkeypatch: pytest.MonkeyPatch) -> None:
    """mock 采集 + 推送，避免真实 HTTP/SMTP。

    - collect_new_tenders：返回空 dict（不采集新数据）
    - push_to_channels：返回 delivered=True（模拟推送成功）
    """
    from app.scheduler import subscription as sub_module

    async def _mock_collect(sub, filters):
        return {}

    async def _mock_push(sub, report_path, count):
        return {
            "delivered": True,
            "channels": [
                {
                    "channel": "log",
                    "ok": True,
                    "delivered": True,
                    "message_id": "test-mock",
                    "error": None,
                }
            ],
        }

    monkeypatch.setattr(
        "app.scheduler.collector.collect_new_tenders", _mock_collect
    )
    monkeypatch.setattr(sub_module, "push_to_channels", _mock_push)


# ==== 数据 seeding 辅助函数 ====


async def _seed_user(email: str = "sub_user@test.com", plan: str = "free") -> int:
    async with AsyncSessionLocal() as db:
        u = User(email=email, plan=plan)
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return u.id


async def _seed_api_key(user_id: int, name: str = "test") -> str:
    raw_key = generate_api_key()
    async with AsyncSessionLocal() as db:
        k = ApiKey(user_id=user_id, key_hash=hash_api_key(raw_key), name=name)
        db.add(k)
        await db.commit()
    return raw_key


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


async def _seed_tender(**kwargs) -> int:
    """插入一条 Tender 测试记录，返回其 id。"""
    defaults = dict(
        project_name="测试医疗设备采购项目",
        bid_number="TEST-2026-001",
        notice_type="tender",
        tender_org="北京大学第三医院",
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


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _authed_user(
    email: str = "sub_user@test.com",
) -> tuple[int, dict[str, str]]:
    """创建用户 + API key，返回 (user_id, auth_headers)。"""
    uid = await _seed_user(email=email)
    raw_key = await _seed_api_key(uid)
    return uid, auth_headers(raw_key)


# ==== 1. 创建订阅 ====


class TestCreateSubscription:
    """POST /api/subscriptions。"""

    async def test_create_subscription_success(self, mock_parse_query) -> None:
        """正常创建订阅 → 200 + subscription_id。"""
        _uid, headers = await _authed_user()
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
        assert body["code"] == 201
        assert body["data"]["subscription_id"] > 0
        assert body["msg"] == "订阅创建成功"

    async def test_create_subscription_without_auth(self) -> None:
        """无认证应返回 401。"""
        async with _client() as ac:
            resp = await ac.post(
                "/api/subscriptions",
                json={
                    "raw_query": "北京医疗设备采购",
                    "platforms": ["ccgp"],
                },
            )
        assert resp.status_code == 401

    async def test_create_subscription_invalid_platform(
        self, mock_parse_query
    ) -> None:
        """无效推送渠道：subscribe.py 仅对 push_channels 做白名单校验，
        不在白名单的渠道会触发 422。"""
        _uid, headers = await _authed_user()
        async with _client() as ac:
            resp = await ac.post(
                "/api/subscriptions",
                headers=headers,
                json={
                    "raw_query": "北京医疗设备采购",
                    "platforms": ["unknown_platform"],
                    "push_channels": ["telegram"],  # 不在白名单
                },
            )
        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == 422

    async def test_create_subscription_with_email_channel(
        self, mock_parse_query
    ) -> None:
        """包含邮件推送渠道 + notify_email → 201。"""
        _uid, headers = await _authed_user()
        async with _client() as ac:
            resp = await ac.post(
                "/api/subscriptions",
                headers=headers,
                json={
                    "raw_query": "上海充电桩招标",
                    "platforms": ["ccgp"],
                    "push_channels": ["email"],
                    "notify_email": "subscriber@test.com",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 201
        assert body["data"]["subscription_id"] > 0


# ==== 2. 查询订阅 ====


class TestQuerySubscription:
    """GET /api/subscriptions, GET /api/subscriptions/{id}。"""

    async def test_list_subscriptions(self) -> None:
        """列出用户的订阅。"""
        uid, headers = await _authed_user()
        await _seed_subscription(uid, raw_query="北京医疗设备")
        await _seed_subscription(uid, raw_query="上海IT设备")
        async with _client() as ac:
            resp = await ac.get("/api/subscriptions", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 200
        assert body["data"]["total"] == 2
        assert len(body["data"]["items"]) == 2

    async def test_get_subscription_detail(self) -> None:
        """获取订阅详情。"""
        uid, headers = await _authed_user()
        sid = await _seed_subscription(uid, raw_query="北京医疗设备采购")
        async with _client() as ac:
            resp = await ac.get(
                f"/api/subscriptions/{sid}", headers=headers
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 200
        assert body["data"]["id"] == sid
        assert body["data"]["raw_query"] == "北京医疗设备采购"

    async def test_get_subscription_not_found(self) -> None:
        """不存在的订阅ID → 404。"""
        _uid, headers = await _authed_user()
        async with _client() as ac:
            resp = await ac.get(
                "/api/subscriptions/99999", headers=headers
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == 404

    async def test_get_subscription_other_user(self) -> None:
        """不能查看其他用户的订阅（返回 404）。"""
        uid1, _ = await _authed_user(email="user1@test.com")
        sid = await _seed_subscription(uid1)
        # 创建第二个用户
        uid2 = await _seed_user(email="user2@test.com")
        raw_key2 = await _seed_api_key(uid2)
        headers2 = auth_headers(raw_key2)
        async with _client() as ac:
            resp = await ac.get(
                f"/api/subscriptions/{sid}", headers=headers2
            )
        assert resp.status_code == 404


# ==== 3. 触发订阅 ====


class TestTriggerSubscription:
    """POST /api/subscriptions/{id}/trigger。"""

    async def test_trigger_subscription_success(
        self, mock_collect_and_push
    ) -> None:
        """手动触发采集 → 200 + status in (ok, no_new, skipped)。"""
        uid, headers = await _authed_user()
        sid = await _seed_subscription(uid)
        async with _client() as ac:
            resp = await ac.post(
                f"/api/subscriptions/{sid}/trigger", headers=headers
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 200
        assert body["data"]["status"] in ("ok", "no_new", "skipped")

    async def test_trigger_subscription_not_found(
        self, mock_collect_and_push
    ) -> None:
        """触发不存在的订阅 → 200 + status=skipped。"""
        _uid, headers = await _authed_user()
        async with _client() as ac:
            resp = await ac.post(
                "/api/subscriptions/99999/trigger", headers=headers
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["status"] == "skipped"


# ==== 4. 取消订阅 ====


class TestCancelSubscription:
    """DELETE /api/subscriptions/{id}。"""

    async def test_cancel_subscription(self) -> None:
        """取消订阅 → 200 + is_active=False。"""
        uid, headers = await _authed_user()
        sid = await _seed_subscription(uid)
        async with _client() as ac:
            resp = await ac.delete(
                f"/api/subscriptions/{sid}", headers=headers
            )
        assert resp.status_code == 200
        assert resp.json()["msg"] == "订阅已取消"
        # 验证 DB 中 is_active=False
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Subscription).where(Subscription.id == sid)
            )
            sub = result.scalar_one_or_none()
            assert sub is not None
            assert sub.is_active is False

    async def test_cancel_subscription_already_inactive(self) -> None:
        """重复取消：订阅已 inactive，再次 DELETE 仍返回 200。"""
        uid, headers = await _authed_user()
        # 直接插入一条 inactive 订阅
        sid = await _seed_subscription(uid, is_active=False)
        async with _client() as ac:
            resp = await ac.delete(
                f"/api/subscriptions/{sid}", headers=headers
            )
        # 订阅存在且归属当前用户 → 200
        assert resp.status_code == 200
        assert resp.json()["msg"] == "订阅已取消"


# ==== 5. 查询推送记录 ====


class TestListSubTenders:
    """GET /api/subscriptions/{id}/tenders。"""

    async def test_list_sub_tenders(self) -> None:
        """查询订阅下的招标信息。"""
        uid, headers = await _authed_user()
        sid = await _seed_subscription(uid)
        # 注入一条 Tender
        await _seed_tender(
            project_name="上海充电桩采购项目",
            source_url="http://example.gov.cn/sub_tender/001",
        )
        async with _client() as ac:
            resp = await ac.get(
                f"/api/subscriptions/{sid}/tenders",
                headers=headers,
                params={"only_unpushed": False},  # 查全部
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 200
        assert isinstance(body["data"], list)
        assert len(body["data"]) >= 1

    async def test_list_sub_tenders_empty(self) -> None:
        """空库查询订阅下的招标信息 → 200 + 空列表。"""
        uid, headers = await _authed_user()
        sid = await _seed_subscription(uid)
        async with _client() as ac:
            resp = await ac.get(
                f"/api/subscriptions/{sid}/tenders", headers=headers
            )
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["data"], list)
        assert len(body["data"]) == 0
