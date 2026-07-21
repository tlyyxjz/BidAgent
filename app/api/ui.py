"""Web UI 页面路由（命题 Demo 视频用）。

S-4 拆分：HTML 字符串已迁移到 app/templates/html/，本文件只保留路由。

提供简单的 HTML 界面：
- /ui                    首页（API 概览 + 命题覆盖度）
- /ui/subscriptions      订阅管理（创建/列表/触发推送）
- /ui/tenders            招标信息查询
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.templates.html import (
    INDEX_HTML,
    SUBSCRIPTIONS_HTML,
    TENDERS_HTML,
)

router = APIRouter(prefix="/ui", tags=["ui"])


@router.get("", response_class=HTMLResponse)
async def ui_index() -> HTMLResponse:
    """Web UI 首页。"""
    return HTMLResponse(content=INDEX_HTML)


@router.get("/subscriptions", response_class=HTMLResponse)
async def ui_subscriptions() -> HTMLResponse:
    """订阅管理页面。"""
    return HTMLResponse(content=SUBSCRIPTIONS_HTML)


@router.get("/tenders", response_class=HTMLResponse)
async def ui_tenders() -> HTMLResponse:
    """招标信息查询页面。"""
    return HTMLResponse(content=TENDERS_HTML)
