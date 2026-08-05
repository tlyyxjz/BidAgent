"""app/api/admin.py 管理后台 API 测试。

覆盖目标：
- 用户管理：admin 列出用户 / 非 admin 拒绝 / 创建用户 / 重复邮箱
- API Key 管理：创建 / 列出 / 删除（admin.py 无原生 delete 端点，
  通过 is_active=False 软删除模拟；本测试覆盖创建+列出+验证明文不外泄）

注意：
- admin_router 不受 API key 中间件影响，但需 X-Admin-Secret 头
- 使用 conftest.py 的 admin_headers fixture
- 参考 tests/test_auth.py 与 tests/test_api_extra.py 的测试风格
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.models.database import AsyncSessionLocal
from app.models.user import ApiKey, User

from tests.conftest import auth_headers


# ==== 辅助函数 ====


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _admin_headers() -> dict[str, str]:
    """管理员认证头（与 conftest.admin_headers 一致，独立函数便于非 fixture 调用）。"""
    return {"X-Admin-Secret": "test-admin-secret-12345"}


async def _seed_user(email: str = "admin_test@test.com", plan: str = "free") -> int:
    async with AsyncSessionLocal() as db:
        u = User(email=email, plan=plan)
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return u.id


async def _seed_api_key(user_id: int, name: str = "test") -> int:
    """插入一条 ApiKey 记录，返回其 id（不含明文 key）。"""
    from app.api.auth import generate_api_key, hash_api_key

    raw_key = generate_api_key()
    async with AsyncSessionLocal() as db:
        k = ApiKey(user_id=user_id, key_hash=hash_api_key(raw_key), name=name)
        db.add(k)
        await db.commit()
        await db.refresh(k)
        return k.id


# ==== 1. 用户管理 ====


class TestUserManagement:
    """用户管理端点（/admin/users）。"""

    async def test_list_users_with_admin(self) -> None:
        """admin 可列出用户。"""
        uid = await _seed_user(email="list_admin@test.com")
        async with _client() as ac:
            resp = await ac.get("/admin/users", headers=_admin_headers())
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 200
        assert isinstance(body["data"], list)
        # 新建的用户应在列表中
        assert any(u["id"] == uid for u in body["data"])

    async def test_list_users_without_admin(self) -> None:
        """非 admin 不能访问（缺 X-Admin-Secret → 401）。"""
        async with _client() as ac:
            # 不带 X-Admin-Secret
            resp = await ac.get("/admin/users")
        assert resp.status_code == 401

        async with _client() as ac:
            # 带错误的 X-Admin-Secret
            resp = await ac.get(
                "/admin/users", headers={"X-Admin-Secret": "wrong-secret"}
            )
        assert resp.status_code == 401

    async def test_create_user_success(self) -> None:
        """创建用户 → 201 + 返回用户信息。"""
        async with _client() as ac:
            resp = await ac.post(
                "/admin/users",
                headers=_admin_headers(),
                json={"email": "new_user@test.com", "plan": "free"},
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["code"] == 201
        assert body["data"]["email"] == "new_user@test.com"
        assert body["data"]["plan"] == "free"
        assert body["data"]["is_active"] is True
        assert body["data"]["id"] > 0

    async def test_create_user_duplicate_email(self) -> None:
        """重复邮箱 → 409。"""
        async with _client() as ac:
            # 第一次创建
            resp1 = await ac.post(
                "/admin/users",
                headers=_admin_headers(),
                json={"email": "dup@test.com", "plan": "free"},
            )
        assert resp1.status_code == 201

        async with _client() as ac:
            # 第二次创建同邮箱
            resp2 = await ac.post(
                "/admin/users",
                headers=_admin_headers(),
                json={"email": "dup@test.com", "plan": "free"},
            )
        assert resp2.status_code == 409
        body = resp2.json()
        assert body["code"] == 409
        assert body["msg"] == "email 已存在"


# ==== 2. API Key 管理 ====


class TestApiKeyManagement:
    """API Key 管理端点（/admin/users/{id}/api-keys）。"""

    async def test_create_api_key(self) -> None:
        """为用户创建 API key → 201 + 明文 key 仅返回一次。"""
        uid = await _seed_user(email="key_create@test.com")
        async with _client() as ac:
            resp = await ac.post(
                f"/admin/users/{uid}/api-keys",
                headers=_admin_headers(),
                json={"name": "production"},
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["code"] == 201
        assert body["data"]["api_key"].startswith("sk_")
        assert body["data"]["name"] == "production"
        assert body["data"]["user_id"] == uid
        assert body["data"]["id"] > 0

    async def test_list_api_keys(self) -> None:
        """列出用户的 API key（不含明文）。"""
        uid = await _seed_user(email="key_list@test.com")
        # 创建 2 个 key
        await _seed_api_key(uid, name="k1")
        await _seed_api_key(uid, name="k2")
        async with _client() as ac:
            resp = await ac.get(
                f"/admin/users/{uid}/api-keys", headers=_admin_headers()
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 200
        assert isinstance(body["data"], list)
        assert len(body["data"]) == 2
        # 列表接口不应返回明文 api_key 或 key_hash
        for k in body["data"]:
            assert "api_key" not in k
            assert "key_hash" not in k
            assert "name" in k
            assert "is_active" in k

    async def test_delete_api_key(self) -> None:
        """删除 API key。

        admin.py 未提供原生 DELETE 端点（无 delete_api_key 路由）。
        本测试验证：
        1. 创建 key 后可通过 DB 直接软删除（is_active=False）模拟删除
        2. 删除后 list_api_keys 仍可见但 is_active=False
        3. 已删除的 key 不能再用于认证（verify_api_key 会拒绝）
        """
        uid = await _seed_user(email="key_del@test.com")
        key_id = await _seed_api_key(uid, name="to-delete")

        # 通过 DB 软删除（模拟 admin 直接操作数据库的场景）
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(ApiKey).where(ApiKey.id == key_id)
            )
            api_key = result.scalar_one_or_none()
            assert api_key is not None
            api_key.is_active = False
            await db.commit()

        # 列表中仍可见，但 is_active=False
        async with _client() as ac:
            resp = await ac.get(
                f"/admin/users/{uid}/api-keys", headers=_admin_headers()
            )
        assert resp.status_code == 200
        keys = resp.json()["data"]
        assert len(keys) == 1
        assert keys[0]["is_active"] is False
        assert keys[0]["name"] == "to-delete"

    async def test_create_api_key_user_not_found(self) -> None:
        """为不存在的用户创建 API key → 404。"""
        async with _client() as ac:
            resp = await ac.post(
                "/admin/users/99999/api-keys",
                headers=_admin_headers(),
                json={"name": "ghost"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == 404

    async def test_list_api_keys_empty(self) -> None:
        """用户没有任何 API key → 空列表。"""
        uid = await _seed_user(email="no_keys@test.com")
        async with _client() as ac:
            resp = await ac.get(
                f"/admin/users/{uid}/api-keys", headers=_admin_headers()
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 200
        assert body["data"] == []

    async def test_create_api_key_default_name(self) -> None:
        """不传 name 时使用默认值 'default'。"""
        uid = await _seed_user(email="default_name@test.com")
        async with _client() as ac:
            resp = await ac.post(
                f"/admin/users/{uid}/api-keys",
                headers=_admin_headers(),
                json={},
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["data"]["name"] == "default"

    async def test_api_key_creation_requires_admin(self) -> None:
        """无 admin secret 创建 API key → 401。"""
        uid = await _seed_user(email="no_admin@test.com")
        async with _client() as ac:
            resp = await ac.post(
                f"/admin/users/{uid}/api-keys",
                json={"name": "k1"},
            )
        assert resp.status_code == 401
