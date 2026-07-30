"""Web UI 页面路由（命题 Demo 视频用）。

S-4 拆分：HTML 字符串已迁移到 app/templates/html/，本文件只保留路由。

提供简单的 HTML 界面：
- /ui                    首页（API 概览 + 命题覆盖度）
- /ui/subscriptions      订阅管理（创建/列表/触发推送）
- /ui/tenders            招标信息查询
- /ui/tenders/{id}       招标详情页（W2-06 字段高亮 Demo）
- /ui/chat               聊天 Demo 页（W2-06 6 Agent 协作）
- /ui/search             W3-08 查询页（供应商/项目搜索入口）
- /ui/notice-list        W3-08 公告列表页（按类型筛选）
- /ui/quality-dashboard  W3-08 数据质量评测 Dashboard
- /ui/version            版本历史页（复用 static/version_history.html）
- /ui/org                组织画像页（复用 static/org_profile.html）
- /ui/detail             公告详情页（复用 static/notice_detail.html）
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse

from app.templates.html import (
    CHAT_HTML,
    INDEX_HTML,
    SUBSCRIPTIONS_HTML,
    TENDER_DETAIL_HTML,
    TENDERS_HTML,
)

router = APIRouter(prefix="/ui", tags=["ui"])

_STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"

_GOLD_RAW_DIR = Path(
    r"C:\Users\Lenovo\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent"
    r"\work-mode-projects\6a57291a0778ce48bfe693d2\_w2_raw"
)
_GOLD_ANNOT_DIR = Path(
    r"C:\Users\Lenovo\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent"
    r"\work-mode-projects\6a57291a0778ce48bfe693d2\_w2_annotations"
)


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


@router.get("/tenders/{tender_id}", response_class=HTMLResponse)
async def ui_tender_detail(tender_id: int) -> HTMLResponse:
    """招标详情页（W2-06 字段高亮 Demo）。

    注：当前仅支持 Demo 模式，通过 ?doc= 参数指定金标文档 ID。
    真实 tender_id 暂未接入 evidence API，待 W2-11 补齐。
    """
    return HTMLResponse(content=TENDER_DETAIL_HTML)


@router.get("/chat", response_class=HTMLResponse)
async def ui_chat() -> HTMLResponse:
    """聊天 Demo 页（W2-06 6 Agent 协作 Demo）。"""
    return HTMLResponse(content=CHAT_HTML)


# ========== W3-08 新增 Web Demo 页面（从 static/ 静态文件加载） ==========


def _serve_static_html(filename: str) -> HTMLResponse:
    """从 static/ 目录读取 HTML 返回，文件不存在则 404."""
    path = _STATIC_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"找不到页面文件: {filename}")
    return HTMLResponse(content=path.read_text(encoding="utf-8"))


@router.get("/search", response_class=HTMLResponse)
async def ui_search() -> HTMLResponse:
    """查询页：供应商/项目名称搜索入口。"""
    return _serve_static_html("search.html")


@router.get("/notice-list", response_class=HTMLResponse)
async def ui_notice_list() -> HTMLResponse:
    """公告列表页：按类型(招标/中标/更正)筛选。"""
    return _serve_static_html("notice_list.html")


@router.get("/quality-dashboard", response_class=HTMLResponse)
async def ui_quality_dashboard() -> HTMLResponse:
    """数据质量评测页：抽取准确率/召回率/F1、证据质量、消融对比。"""
    return _serve_static_html("quality_dashboard.html")


@router.get("/version", response_class=HTMLResponse)
async def ui_version() -> HTMLResponse:
    """来源和版本历史页（复用 static/version_history.html）。"""
    return _serve_static_html("version_history.html")


@router.get("/org", response_class=HTMLResponse)
async def ui_org() -> HTMLResponse:
    """组织实体画像页（复用 static/org_profile.html）。"""
    return _serve_static_html("org_profile.html")


@router.get("/detail", response_class=HTMLResponse)
async def ui_detail() -> HTMLResponse:
    """公告详情与证据页（复用 static/notice_detail.html）。"""
    return _serve_static_html("notice_detail.html")


# ========== Demo 数据接口（读取金标数据，仅供 UI Demo 使用） ==========


def _find_raw_file(doc_id: str) -> Path | None:
    """根据 document_id 查找对应的 raw txt 文件。"""
    if not _GOLD_RAW_DIR.exists():
        return None
    for f in _GOLD_RAW_DIR.glob("*.txt"):
        stem = f.stem
        if doc_id == stem or doc_id.startswith(stem) or stem.startswith(doc_id):
            return f
    return None


def _find_annot_file(doc_id: str) -> Path | None:
    """根据 document_id 查找对应的 annotation json 文件。"""
    if not _GOLD_ANNOT_DIR.exists():
        return None
    for f in _GOLD_ANNOT_DIR.glob("annotation_*.json"):
        name = f.stem[len("annotation_"):]
        if doc_id == name or doc_id.startswith(name) or name.startswith(doc_id):
            return f
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("document_id") == doc_id:
                return f
        except Exception:
            continue
    return None


@router.get("/api/demo/raw")
async def demo_raw(doc: str = Query(..., description="金标文档 ID")) -> PlainTextResponse:
    """Demo 接口：返回金标原文文本（供详情页左侧展示）。"""
    f = _find_raw_file(doc)
    if f is None:
        raise HTTPException(status_code=404, detail=f"未找到金标原文: {doc}")
    text = f.read_text(encoding="utf-8")
    return PlainTextResponse(content=text, media_type="text/plain; charset=utf-8")


@router.get("/api/demo/annotation")
async def demo_annotation(doc: str = Query(..., description="金标文档 ID")) -> JSONResponse:
    """Demo 接口：返回金标标注 JSON（供详情页右侧字段和证据使用）。"""
    f = _find_annot_file(doc)
    if f is None:
        raise HTTPException(status_code=404, detail=f"未找到金标标注: {doc}")
    data = json.loads(f.read_text(encoding="utf-8"))
    return JSONResponse(content=data)


@router.get("/api/demo/doc-list")
async def demo_doc_list() -> JSONResponse:
    """Demo 接口：返回可用的金标文档列表。"""
    docs = []
    if _GOLD_RAW_DIR.exists() and _GOLD_ANNOT_DIR.exists():
        for raw_f in sorted(_GOLD_RAW_DIR.glob("*.txt")):
            doc_id = raw_f.stem
            annot_f = _find_annot_file(doc_id)
            if annot_f:
                try:
                    data = json.loads(annot_f.read_text(encoding="utf-8"))
                    notice_type = data.get("notice_type", "")
                    docs.append({
                        "doc_id": data.get("document_id", doc_id),
                        "label": raw_f.stem,
                        "notice_type": notice_type,
                    })
                except Exception:
                    docs.append({"doc_id": doc_id, "label": raw_f.stem})
    return JSONResponse(content={"code": 200, "data": docs, "msg": "ok"})
