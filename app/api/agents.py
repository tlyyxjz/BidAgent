"""多 Agent 协作 API（答辩差异化亮点）。

POST /api/agents/run - 运行 4 Agent 工作流
GET  /api/agents/templates - 列出 Agent 模板

工作流：
1. intent  - 意图理解（解析用户查询）
2. collect - 采集规划（多平台抓取）
3. clean   - 数据清洗（SimHash 去重）
4. report  - 报告生成（Word + 反幻觉）
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.auth import verify_api_key
from app.models.user import User
from app.utils.logger import get_logger

logger = get_logger("agents_api")

router = APIRouter(prefix="/api/agents", tags=["agents"])


class AgentRunRequest(BaseModel):
    """多 Agent 工作流请求。"""

    query: str = Field(..., min_length=2, max_length=500, description="用户查询")
    platforms: list[str] = Field(
        default_factory=lambda: ["ccgp"],
        description="采集平台列表",
    )


@router.post("/run")
async def run_agents(
    payload: AgentRunRequest,
    auth: tuple[User, Any, str] = Depends(verify_api_key),
) -> JSONResponse:
    """POST /api/agents/run - 运行 4 Agent 工作流。

    返回完整的执行轨迹 + 最终报告路径，便于答辩展示 Agent 协作过程。
    """
    user, _api_key_obj, _raw_api_key = auth
    logger.info(
        "agent run started user_id={} query={} platforms={}",
        user.id, payload.query[:50], payload.platforms,
    )

    try:
        from app.agents.coordinator import run_multi_agent_workflow

        result = await run_multi_agent_workflow(
            query=payload.query,
            user_id=user.id,
            platforms=payload.platforms,
        )

        # 提取关键字段返回（避免序列化 ORM 对象）
        response_data = {
            "query": result.get("query"),
            "topic": result.get("topic"),
            "region": result.get("region"),
            "trigger_type": result.get("trigger_type"),
            "collect_summary": result.get("collect_summary"),
            "clean_summary": result.get("clean_summary"),
            "report_path": result.get("report_path"),
            "report_summary": result.get("report_summary"),
            "execution_summary": result.get("_execution_summary"),
            "agent_history": result.get("_agent_history"),
        }

        return JSONResponse(
            status_code=200,
            content={"code": 200, "data": response_data, "msg": "ok"},
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent run failed query=%s", payload.query[:50])
        return JSONResponse(
            status_code=500,
            content={"code": 500, "data": None, "msg": f"工作流执行失败: {exc}"},
        )


@router.get("/templates")
async def list_agent_templates(
    auth: tuple[User, Any, str] = Depends(verify_api_key),
) -> JSONResponse:
    """GET /api/agents/templates - 列出内置 Agent 模板。"""
    _user, _api_key_obj, _raw_api_key = auth
    templates = [
        {
            "name": "intent",
            "description": "意图理解：解析用户查询的 5 槽位（topic/region/time_range/frequency/trigger_type）",
            "implementation": "app.agents.coordinator.intent_agent",
        },
        {
            "name": "collect",
            "description": "采集规划：从多平台抓取招标信息（ccgp/chinabidding/ggzy/qianlima）",
            "implementation": "app.agents.coordinator.collect_agent",
        },
        {
            "name": "clean",
            "description": "数据清洗：SimHash 去重 + 字段校验",
            "implementation": "app.agents.coordinator.clean_agent",
        },
        {
            "name": "report",
            "description": "报告生成：Word 报告 + 反幻觉校验",
            "implementation": "app.agents.coordinator.report_agent",
        },
    ]
    return JSONResponse(
        status_code=200,
        content={"code": 200, "data": {"templates": templates}, "msg": "ok"},
    )
