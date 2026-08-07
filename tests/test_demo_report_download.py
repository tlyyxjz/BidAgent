"""demo_report 新端点 /api/demo/report/download 测试（方案A 修复）。

覆盖目标：
- 成功下载 pipeline 生成的报告
- session 不存在 → 404
- session 无 report_path → 404
- 报告文件不存在 → 404

测试策略：mock app.agents.pipeline.get_session 返回不同 session 状态，
避免依赖真实 pipeline 执行（pipeline 单测在 test_agents.py 覆盖）。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ==== 辅助：构造 mock session ====

def _make_session(report_path: str | None, completed: bool = True) -> dict:
    """构造 pipeline session 字典。"""
    result = {}
    if report_path is not None:
        result["report_path"] = report_path
    return {
        "session_id": "test-sid",
        "stage": "done" if completed else "running",
        "result": result,
    }


def _extract_msg(resp) -> str:
    """从响应中提取错误信息（兼容 {msg} 和 {detail} 两种格式）。"""
    body = resp.json()
    return body.get("msg") or body.get("detail") or ""


class TestDemoReportDownload:
    """/api/demo/report/download 端点测试。"""

    @pytest.mark.asyncio
    async def test_download_success(self, tmp_path: Path) -> None:
        """session 有效 + report_path 有效 + 文件存在 → 200 返回文件。"""
        # 准备真实临时文件
        report_file = tmp_path / "测试报告_202608071200.docx"
        report_file.write_bytes(b"fake docx content")

        mock_session = _make_session(str(report_file))
        with patch(
            "app.agents.pipeline.get_session",
            return_value=mock_session,
        ):
            async with _client() as ac:
                resp = await ac.get(
                    "/api/demo/report/download",
                    params={"sid": "test-sid"},
                )

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument"
        )
        # Content-Disposition 应包含文件名
        cd = resp.headers.get("content-disposition", "")
        assert "测试报告" in cd or "report" in cd.lower() or ".docx" in cd
        assert resp.content == b"fake docx content"

    @pytest.mark.asyncio
    async def test_download_no_session(self) -> None:
        """session 不存在（已过期或无效 sid）→ 404。"""
        with patch(
            "app.agents.pipeline.get_session",
            return_value=None,
        ):
            async with _client() as ac:
                resp = await ac.get(
                    "/api/demo/report/download",
                    params={"sid": "invalid-sid"},
                )

        assert resp.status_code == 404
        msg = _extract_msg(resp)
        assert "session" in msg or "过期" in msg

    @pytest.mark.asyncio
    async def test_download_no_report_path(self) -> None:
        """session 存在但 report_path 为 None（pipeline 未采集到新数据）→ 404。"""
        mock_session = _make_session(report_path=None)
        with patch(
            "app.agents.pipeline.get_session",
            return_value=mock_session,
        ):
            async with _client() as ac:
                resp = await ac.get(
                    "/api/demo/report/download",
                    params={"sid": "test-sid"},
                )

        assert resp.status_code == 404
        msg = _extract_msg(resp)
        assert "报告" in msg or "未生成" in msg

    @pytest.mark.asyncio
    async def test_download_file_missing(self, tmp_path: Path) -> None:
        """report_path 有值但文件已被清理 → 404。"""
        # 指向不存在的文件
        missing_path = str(tmp_path / "deleted_report.docx")

        mock_session = _make_session(report_path=missing_path)
        with patch(
            "app.agents.pipeline.get_session",
            return_value=mock_session,
        ):
            async with _client() as ac:
                resp = await ac.get(
                    "/api/demo/report/download",
                    params={"sid": "test-sid"},
                )

        assert resp.status_code == 404
        msg = _extract_msg(resp)
        assert "不存在" in msg or "清理" in msg

    @pytest.mark.asyncio
    async def test_download_missing_sid_param(self) -> None:
        """缺少 sid 查询参数 → 422（FastAPI Query 必填校验）。"""
        async with _client() as ac:
            resp = await ac.get("/api/demo/report/download")

        assert resp.status_code == 422
