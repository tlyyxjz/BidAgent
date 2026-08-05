"""W3 Demo 6 Agent pipeline 端点（A1 修复）。

提供：
- POST /api/demo/pipeline/start   启动真实 6 Agent pipeline
- GET  /api/demo/pipeline/status   查询真实 pipeline 阶段进度

注：run_pipeline / get_session 保持函数内局部 import，以兼容测试中
monkeypatch.setattr("app.agents.pipeline.run_pipeline", ...) 用法。
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

router = APIRouter(tags=["demo"])


@router.post("/pipeline/start", summary="启动真实 6 Agent pipeline（A1 修复）")
async def demo_pipeline_start(query: str = Query(..., description="用户查询")):
    """A1 修复：聊天页 6 Agent 协作接真实 pipeline。
    调 app.agents.pipeline.run_pipeline 启动真实异步 pipeline，返回 session_id。
    前端通过 /api/demo/pipeline/status?sid=xxx 轮询真实进度。
    """
    from app.agents.pipeline import run_pipeline
    session_id = await run_pipeline({"query": query})
    return JSONResponse({"code": 200, "data": {"session_id": session_id}, "msg": "ok"})


@router.get("/pipeline/status", summary="查询真实 pipeline 阶段进度（A1 修复）")
async def demo_pipeline_status(sid: str = Query(..., description="session_id")):
    """查询真实 pipeline 进度。
    返回 stage / progress / stages 六阶段真实状态。
    """
    from app.agents.pipeline import get_session
    session = await get_session(sid)
    if not session:
        return JSONResponse({"code": 404, "data": None, "msg": "session not found"}, status_code=404)
    return JSONResponse({"code": 200, "data": session, "msg": "ok"})
