"""app/api/ui.py 补充测试 — 覆盖 UI 页面路由 + Demo 数据接口.

被测端点（app/api/ui.py）：
- GET  /ui                          首页（workbench.html）
- GET  /ui/chat                     智能问答页
- GET  /ui/search                   查询页
- GET  /ui/notice-list              公告列表页
- GET  /ui/quality-dashboard        数据质量评测页
- GET  /ui/org                      组织画像页
- GET  /ui/version                  旧链接重定向 → /ui/versions（307）
- GET  /ui/versions                 版本历史页
- GET  /ui/detail                   公告详情页
- GET  /ui/api/demo/raw             金标原文文本
- GET  /ui/api/demo/annotation      金标标注 JSON
- GET  /ui/api/demo/doc-list        可用金标文档列表

参考 tests/test_demo_api_extra.py 的测试风格：httpx.AsyncClient + ASGITransport，
fixture 覆盖 conftest 同名 fixture 以避免同事务 drop+create 导致表丢失。
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


# ==== fixtures（覆盖 conftest 同名 fixture，避免同事务 drop+create 丢表）====


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ========== UI 页面 GET 请求返回 200 ==========


class TestUIPages:
    """各页面 GET 请求返回 200 + HTML 内容。"""

    async def test_ui_index_returns_workbench(self):
        """GET /ui → 200 + workbench.html 内容。"""
        async with _client() as ac:
            resp = await ac.get("/ui")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        body = resp.text
        assert len(body) > 0

    async def test_ui_chat(self):
        """GET /ui/chat → 200。"""
        async with _client() as ac:
            resp = await ac.get("/ui/chat")
        assert resp.status_code == 200
        assert len(resp.text) > 0

    async def test_ui_search(self):
        """GET /ui/search → 200。"""
        async with _client() as ac:
            resp = await ac.get("/ui/search")
        assert resp.status_code == 200
        assert len(resp.text) > 0

    async def test_ui_notice_list(self):
        """GET /ui/notice-list → 200。"""
        async with _client() as ac:
            resp = await ac.get("/ui/notice-list")
        assert resp.status_code == 200
        assert len(resp.text) > 0

    async def test_ui_quality_dashboard(self):
        """GET /ui/quality-dashboard → 200。"""
        async with _client() as ac:
            resp = await ac.get("/ui/quality-dashboard")
        assert resp.status_code == 200
        assert len(resp.text) > 0

    async def test_ui_org(self):
        """GET /ui/org → 200。"""
        async with _client() as ac:
            resp = await ac.get("/ui/org")
        assert resp.status_code == 200
        assert len(resp.text) > 0

    async def test_ui_versions(self):
        """GET /ui/versions → 200。"""
        async with _client() as ac:
            resp = await ac.get("/ui/versions")
        assert resp.status_code == 200
        assert len(resp.text) > 0

    async def test_ui_detail(self):
        """GET /ui/detail → 200。"""
        async with _client() as ac:
            resp = await ac.get("/ui/detail")
        assert resp.status_code == 200
        assert len(resp.text) > 0


class TestUIVersionRedirect:
    """/ui/version 旧链接重定向。"""

    async def test_version_redirects_to_versions(self):
        """GET /ui/version → 307 重定向到 /ui/versions。"""
        async with _client() as ac:
            resp = await ac.get("/ui/version", follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"] == "/ui/versions"

    async def test_version_redirect_follows(self):
        """GET /ui/version 跟随重定向 → 最终 200。"""
        async with _client() as ac:
            resp = await ac.get("/ui/version", follow_redirects=True)
        assert resp.status_code == 200
        assert len(resp.text) > 0


# ========== Demo 数据接口 ==========


class TestDemoRaw:
    """GET /ui/api/demo/raw — 金标原文文本。"""

    async def test_raw_existing_doc(self):
        """存在的 doc_id → 200 + text/plain。"""
        async with _client() as ac:
            resp = await ac.get(
                "/ui/api/demo/raw", params={"doc": "02_tender_002"}
            )
        assert resp.status_code == 200
        content_type = resp.headers.get("content-type", "")
        assert "text/plain" in content_type
        assert len(resp.text) > 0

    async def test_raw_nonexistent_doc_returns_404(self):
        """不存在的 doc_id → 404。"""
        async with _client() as ac:
            resp = await ac.get(
                "/ui/api/demo/raw", params={"doc": "nonexistent_doc_xyz"}
            )
        assert resp.status_code == 404

    async def test_raw_missing_doc_param_returns_422(self):
        """缺少 doc 参数 → 422 校验错误。"""
        async with _client() as ac:
            resp = await ac.get("/ui/api/demo/raw")
        assert resp.status_code == 422


class TestDemoAnnotation:
    """GET /ui/api/demo/annotation — 金标标注 JSON。"""

    async def test_annotation_existing_doc(self):
        """存在的 doc_id → 200 + JSON。"""
        async with _client() as ac:
            resp = await ac.get(
                "/ui/api/demo/annotation", params={"doc": "02_tender_002"}
            )
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, dict)

    async def test_annotation_nonexistent_doc_returns_404(self):
        """不存在的 doc_id → 404。"""
        async with _client() as ac:
            resp = await ac.get(
                "/ui/api/demo/annotation",
                params={"doc": "nonexistent_doc_xyz"},
            )
        assert resp.status_code == 404

    async def test_annotation_missing_doc_param_returns_422(self):
        """缺少 doc 参数 → 422 校验错误。"""
        async with _client() as ac:
            resp = await ac.get("/ui/api/demo/annotation")
        assert resp.status_code == 422


class TestDemoDocList:
    """GET /ui/api/demo/doc-list — 可用金标文档列表。"""

    async def test_doc_list_returns_200(self):
        """文档列表 → 200 + code=200 + data 为列表。"""
        async with _client() as ac:
            resp = await ac.get("/ui/api/demo/doc-list")
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 200
        assert isinstance(body["data"], list)

    async def test_doc_list_items_structure(self):
        """列表项包含 doc_id / label 字段。"""
        async with _client() as ac:
            resp = await ac.get("/ui/api/demo/doc-list")
        body = resp.json()
        if len(body["data"]) > 0:
            item = body["data"][0]
            assert "doc_id" in item
            assert "label" in item


# ========== 静态文件服务（_serve_static_html 内部函数）==========


class TestServeStaticHtml:
    """_serve_static_html 直接调用，覆盖文件读取路径。"""

    async def test_serve_existing_html(self):
        """直接调用 _serve_static_html 读取存在的文件。"""
        from app.api.ui import _serve_static_html

        resp = _serve_static_html("workbench.html")
        assert resp.status_code == 200
        assert len(resp.body) > 0

    async def test_serve_nonexistent_html_raises_404(self):
        """直接调用 _serve_static_html 读取不存在的文件 → HTTPException 404。"""
        from fastapi import HTTPException

        from app.api.ui import _serve_static_html

        with pytest.raises(HTTPException) as exc_info:
            _serve_static_html("nonexistent_page.html")
        assert exc_info.value.status_code == 404


# ========== 辅助函数直接调用 ==========


class TestFindRawFile:
    """_find_raw_file 直接调用，覆盖文件查找逻辑。"""

    def test_find_existing_raw_file(self):
        """存在的 doc_id → 返回 Path。"""
        from app.api.ui import _find_raw_file

        result = _find_raw_file("02_tender_002")
        assert result is not None
        assert result.exists()
        assert result.suffix == ".txt"

    def test_find_nonexistent_raw_file(self):
        """不存在的 doc_id → None。"""
        from app.api.ui import _find_raw_file

        result = _find_raw_file("nonexistent_doc_xyz_999")
        assert result is None

    def test_find_raw_file_partial_match(self):
        """部分匹配 stem → 返回 Path（startswith 逻辑）。"""
        from app.api.ui import _find_raw_file

        # 使用文件名前缀查找
        result = _find_raw_file("02_tender")
        assert result is not None


class TestFindAnnotFile:
    """_find_annot_file 直接调用，覆盖文件查找逻辑。"""

    def test_find_existing_annot_file(self):
        """存在的 doc_id → 返回 Path。"""
        from app.api.ui import _find_annot_file

        result = _find_annot_file("02_tender_002")
        assert result is not None
        assert result.exists()
        assert result.suffix == ".json"

    def test_find_nonexistent_annot_file(self):
        """不存在的 doc_id → None。"""
        from app.api.ui import _find_annot_file

        result = _find_annot_file("nonexistent_doc_xyz_999")
        assert result is None