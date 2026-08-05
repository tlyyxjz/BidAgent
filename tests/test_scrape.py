"""抓取端点测试 - POST /api/scrape, POST /api/scrape/batch, GET /api/scrape/{job_id}。

工程规范：
- mock Playwright 抓取逻辑（不启动真实浏览器）。
- 测试速率限制（免费 3 次/天，pro 无限）。
- 统一错误响应 {code, data, msg}。
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.core.rate_limit import reset_memory_counter
from app.models.database import AsyncSessionLocal
from app.models.job import JOB_COMPLETED, ScrapeJob
from app.models.user import utc_now
from tests.conftest import auth_headers


def _mock_scrape_result(url: str) -> dict[str, Any]:
    """构造一个假的抓取结果。"""
    return {
        "url": url,
        "data": [{"title": "Mock Title", "price": "$9.99"}],
        "pages_scraped": 1,
    }


@pytest.fixture
def mock_scraper_success():
    """mock scraper.scrape 返回成功结果。"""
    async def _fake_scrape(request: dict[str, Any]) -> dict[str, Any]:
        return _mock_scrape_result(request.get("url", ""))

    with patch("app.api.scrape.scraper") as mocked:
        mocked.scrape = AsyncMock(side_effect=_fake_scrape)
        yield mocked


class TestScrapeEndpoint:
    """POST /api/scrape 单次抓取。"""

    async def test_scrape_success(
        self,
        client: AsyncClient,
        free_user_and_key: tuple[int, str],
        mock_scraper_success: Any,
    ) -> None:
        _uid, raw_key = free_user_and_key
        resp = await client.post(
            "/api/scrape",
            headers=auth_headers(raw_key),
            json={
                "url": "https://example.com/product/1",
                "selectors": {"title": "h1", "price": ".price"},
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 200
        assert body["msg"] == "ok"
        assert body["data"]["url"] == "https://example.com/product/1"
        assert body["data"]["data"][0]["title"] == "Mock Title"

    async def test_scrape_invalid_url_validation(
        self,
        client: AsyncClient,
        free_user_and_key: tuple[int, str],
    ) -> None:
        """URL 不合法 → 422 参数校验失败。"""
        _uid, raw_key = free_user_and_key
        resp = await client.post(
            "/api/scrape",
            headers=auth_headers(raw_key),
            json={"url": "not-a-url"},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == 422

    async def test_scrape_missing_url(
        self,
        client: AsyncClient,
        free_user_and_key: tuple[int, str],
    ) -> None:
        """缺少 url → 422。"""
        _uid, raw_key = free_user_and_key
        resp = await client.post(
            "/api/scrape",
            headers=auth_headers(raw_key),
            json={"selectors": {"x": "y"}},
        )
        assert resp.status_code == 422

    async def test_scrape_failure_returns_502(
        self,
        client: AsyncClient,
        free_user_and_key: tuple[int, str],
    ) -> None:
        """抓取失败 → 502 + 统一错误格式（不泄露异常详情）。"""
        from app.core.scraper import ScrapeError

        _uid, raw_key = free_user_and_key
        with patch("app.api.scrape.scraper") as mocked:
            mocked.scrape = AsyncMock(side_effect=ScrapeError("boom"))
            resp = await client.post(
                "/api/scrape",
                headers=auth_headers(raw_key),
                json={"url": "https://example.com/fail"},
            )
        assert resp.status_code == 502
        body = resp.json()
        assert body["code"] == 502
        assert body["data"] is None
        assert body["msg"] == "抓取失败"
        # BE-H1: 异常详情不应泄露给客户端
        assert "boom" not in body["msg"]


class TestRateLimit:
    """免费套餐每日限额测试（conftest 中 FREE_TIER_DAILY_LIMIT=3）。"""

    async def test_free_tier_rate_limited_after_limit(
        self,
        client: AsyncClient,
        free_user_and_key: tuple[int, str],
        mock_scraper_success: Any,
    ) -> None:
        _uid, raw_key = free_user_and_key

        # 前 3 次成功
        for i in range(3):
            resp = await client.post(
                "/api/scrape",
                headers=auth_headers(raw_key),
                json={"url": f"https://example.com/{i}"},
            )
            assert resp.status_code == 200, f"call {i+1} should succeed"

        # 第 4 次 → 429
        resp = await client.post(
            "/api/scrape",
            headers=auth_headers(raw_key),
            json={"url": "https://example.com/over-limit"},
        )
        assert resp.status_code == 429
        body = resp.json()
        assert body["code"] == 429
        assert body["data"] is None

    async def test_pro_tier_unlimited(
        self,
        client: AsyncClient,
        pro_user_and_key: tuple[int, str],
        mock_scraper_success: Any,
    ) -> None:
        """Pro 套餐不限制速率。"""
        _uid, raw_key = pro_user_and_key
        # 调用 10 次都应成功
        for i in range(10):
            resp = await client.post(
                "/api/scrape",
                headers=auth_headers(raw_key),
                json={"url": f"https://example.com/pro/{i}"},
            )
            assert resp.status_code == 200, f"pro call {i+1} should succeed"


class TestBatchEndpoint:
    """POST /api/scrape/batch 批量抓取。"""

    async def test_batch_returns_job_ids(
        self,
        client: AsyncClient,
        free_user_and_key: tuple[int, str],
    ) -> None:
        """batch 端点入队后返回 job_ids（mock enqueue）。"""
        _uid, raw_key = free_user_and_key

        async def _fake_enqueue(user_id: int, request_data: dict[str, Any]) -> str:
            return f"mock-job-{user_id}-{request_data.get('url')}"

        with patch("app.api.scrape.enqueue_scrape_job", side_effect=_fake_enqueue):
            resp = await client.post(
                "/api/scrape/batch",
                headers=auth_headers(raw_key),
                json={
                    "items": [
                        {"url": "https://example.com/1"},
                        {"url": "https://example.com/2"},
                    ]
                },
            )
        assert resp.status_code == 202
        body = resp.json()
        assert body["code"] == 202
        assert body["data"]["total"] == 2
        assert len(body["data"]["job_ids"]) == 2

    async def test_batch_rejects_over_100(
        self,
        client: AsyncClient,
        free_user_and_key: tuple[int, str],
    ) -> None:
        """超过 100 个 URL → 422。"""
        _uid, raw_key = free_user_and_key
        items = [{"url": f"https://example.com/{i}"} for i in range(101)]
        resp = await client.post(
            "/api/scrape/batch",
            headers=auth_headers(raw_key),
            json={"items": items},
        )
        assert resp.status_code == 422

    async def test_batch_rejects_empty(
        self,
        client: AsyncClient,
        free_user_and_key: tuple[int, str],
    ) -> None:
        """空 items → 422。"""
        _uid, raw_key = free_user_and_key
        resp = await client.post(
            "/api/scrape/batch",
            headers=auth_headers(raw_key),
            json={"items": []},
        )
        assert resp.status_code == 422


class TestJobStatusEndpoint:
    """GET /api/scrape/{job_id} 任务状态查询。"""

    async def _create_completed_job(self, user_id: int) -> str:
        """直接在 DB 中创建一个 completed 任务。"""
        import uuid

        job_id = str(uuid.uuid4())
        async with AsyncSessionLocal() as session:
            job = ScrapeJob(
                id=job_id,
                user_id=user_id,
                url="https://example.com/test",
                status=JOB_COMPLETED,
                request_data=json.dumps({"url": "https://example.com/test"}),
                result_data=json.dumps(
                    {
                        "url": "https://example.com/test",
                        "data": [{"title": "Done"}],
                        "pages_scraped": 1,
                    }
                ),
                progress=100,
                completed_at=utc_now(),
            )
            session.add(job)
            await session.commit()
        return job_id

    async def test_get_existing_job(
        self,
        client: AsyncClient,
        free_user_and_key: tuple[int, str],
    ) -> None:
        uid, raw_key = free_user_and_key
        job_id = await self._create_completed_job(uid)

        resp = await client.get(
            f"/api/scrape/{job_id}",
            headers=auth_headers(raw_key),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 200
        assert body["data"]["job_id"] == job_id
        assert body["data"]["status"] == "completed"
        # body["data"]["data"] 是抓取结果对象 {"url": ..., "data": [...], ...}
        result_data = body["data"]["data"]
        assert result_data["data"][0]["title"] == "Done"

    async def test_get_nonexistent_job(
        self,
        client: AsyncClient,
        free_user_and_key: tuple[int, str],
    ) -> None:
        _uid, raw_key = free_user_and_key
        resp = await client.get(
            "/api/scrape/nonexistent-job-id",
            headers=auth_headers(raw_key),
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == 404


class TestTemplatesEndpoint:
    """GET /api/scrape/templates/list 模板列表。"""

    async def test_list_templates(
        self,
        client: AsyncClient,
        free_user_and_key: tuple[int, str],
    ) -> None:
        _uid, raw_key = free_user_and_key
        resp = await client.get(
            "/api/scrape/templates/list",
            headers=auth_headers(raw_key),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 200
        assert "amazon" in body["data"]["templates"]
        assert "reddit" in body["data"]["templates"]
        assert "news" in body["data"]["templates"]
