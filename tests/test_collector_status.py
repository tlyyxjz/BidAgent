"""采集进度端点 /api/demo/collector/status 集成测试。

验证工作台首页"采集进度卡片"接口的字段契约与平台状态合法性。
"""
from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.main import app


async def _get_status():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/api/demo/collector/status")


async def test_collector_status_returns_200():
    resp = await _get_status()
    assert resp.status_code == 200


async def test_collector_status_has_4_platforms():
    resp = await _get_status()
    data = resp.json()["data"]
    assert len(data["platforms"]) == 4


async def test_collector_status_no_mock_data():
    """平台状态只能是 idle/running/failed，不得出现 mock 类占位值。"""
    resp = await _get_status()
    data = resp.json()["data"]
    valid = {"idle", "running", "failed"}
    for p in data["platforms"]:
        assert p["status"] in valid, f"非法平台状态值: {p['status']}"


async def test_collector_status_has_required_fields():
    resp = await _get_status()
    data = resp.json()["data"]
    for field in (
        "active_jobs",
        "today_collected",
        "today_deduplicated",
        "today_failed",
        "platforms",
        "recent_batches",
    ):
        assert field in data, f"返回缺少必填字段: {field}"
