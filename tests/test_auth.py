"""认证 + 管理后台测试。"""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import auth_headers


class TestHealth:
    """健康检查（无需认证）。"""

    async def test_health(self, client: AsyncClient) -> None:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    async def test_root(self, client: AsyncClient) -> None:
        resp = await client.get("/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "标小智 API"

    async def test_root_html_redirects_to_ui(self, client: AsyncClient) -> None:
        """Browser Accept: text/html should redirect to /ui (307)."""
        resp = await client.get(
            "/", headers={"Accept": "text/html,application/xhtml+xml"}
        )
        assert resp.status_code == 307
        assert resp.headers["location"] == "/ui"


class TestAdminAuth:
    """Admin 路由不能被 API key 中间件拦截，但需要 X-Admin-Secret。"""

    async def test_admin_without_secret_rejected(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/users")
        assert resp.status_code == 401

    async def test_admin_with_wrong_secret_rejected(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/admin/users", headers={"X-Admin-Secret": "wrong"}
        )
        assert resp.status_code == 401

    async def test_admin_with_correct_secret_ok(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        resp = await client.get("/admin/users", headers=admin_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 200
        assert body["data"] == []


class TestUserManagement:
    """用户管理（admin 路由）。"""

    async def test_create_user(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        resp = await client.post(
            "/admin/users",
            headers=admin_headers,
            json={"email": "new@test.com", "plan": "free"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["code"] == 201
        assert body["data"]["email"] == "new@test.com"
        assert body["data"]["plan"] == "free"

    async def test_create_user_duplicate_email(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        await client.post(
            "/admin/users",
            headers=admin_headers,
            json={"email": "dup@test.com", "plan": "free"},
        )
        resp = await client.post(
            "/admin/users",
            headers=admin_headers,
            json={"email": "dup@test.com", "plan": "free"},
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == 409


class TestApiKeyCreation:
    """API key 创建与列出。"""

    async def test_create_api_key_returns_plaintext(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        # 先建用户
        user_resp = await client.post(
            "/admin/users",
            headers=admin_headers,
            json={"email": "keytest@test.com", "plan": "free"},
        )
        user_id = user_resp.json()["data"]["id"]

        # 建 API key
        resp = await client.post(
            f"/admin/users/{user_id}/api-keys",
            headers=admin_headers,
            json={"name": "production"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["data"]["api_key"].startswith("sk_")
        assert body["data"]["name"] == "production"

    async def test_list_api_keys_hides_plaintext(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        user_resp = await client.post(
            "/admin/users",
            headers=admin_headers,
            json={"email": "listkeys@test.com", "plan": "free"},
        )
        user_id = user_resp.json()["data"]["id"]
        await client.post(
            f"/admin/users/{user_id}/api-keys",
            headers=admin_headers,
            json={"name": "k1"},
        )

        resp = await client.get(
            f"/admin/users/{user_id}/api-keys", headers=admin_headers
        )
        assert resp.status_code == 200
        keys = resp.json()["data"]
        assert len(keys) == 1
        # 列表接口不应返回明文 api_key
        assert "api_key" not in keys[0]
        assert "key_hash" not in keys[0]


class TestBearerAuth:
    """Bearer token 认证。"""

    async def test_scrape_without_token_rejected(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/scrape",
            json={"url": "https://example.com"},
        )
        assert resp.status_code == 401

    async def test_scrape_with_invalid_token_rejected(
        self, client: AsyncClient
    ) -> None:
        resp = await client.post(
            "/api/scrape",
            headers=auth_headers("sk_invalid_key_xxx"),
            json={"url": "https://example.com"},
        )
        assert resp.status_code == 401

    async def test_scrape_with_valid_token_passes_auth(
        self,
        client: AsyncClient,
        free_user_and_key: tuple[int, str],
    ) -> None:
        """验证有效 API key 能通过认证（会到抓取阶段，可能 502）。"""
        _uid, raw_key = free_user_and_key
        resp = await client.post(
            "/api/scrape",
            headers=auth_headers(raw_key),
            json={"url": "https://nonexistent.invalid.example.com"},
        )
        # 通过认证 → 进入抓取 → 抓取失败返回 502（不是 401）
        assert resp.status_code != 401
