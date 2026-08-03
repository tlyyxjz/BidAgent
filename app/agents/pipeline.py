"""六 Agent 协作 pipeline + session 管理。

提供两个核心接口：
1. run_pipeline(filters: dict) -> str — 异步启动六 Agent pipeline，返回 session_id
2. get_session(session_id: str) -> dict — 查询 session 状态（供 chat.py 轮询）

设计原则：
- session 存储在内存 dict（MVP 阶段足够，无需 Redis）
- 六 Agent 异步执行，每个阶段完成时更新 session
- 前端轮询 GET /chat/api/{session_id} 每 1 秒一次
- 失败不阻塞，记录 error 后继续下一阶段（保证部分可用）
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app.agents.coordinator import AgentGraph
from app.agents.collector_agent import collector_agent
from app.agents.delivery_agent import delivery_agent
from app.agents.finance_agent import finance_agent
from app.agents.intent_agent import intent_agent
from app.agents.processor_agent import processor_agent
from app.agents.quality_agent import quality_agent
from app.utils.logger import get_logger

logger = get_logger("agent.pipeline")

# session 存储（MVP 阶段用内存 dict，生产环境改 Redis）
_sessions: dict[str, dict[str, Any]] = {}
_sessions_lock = asyncio.Lock()


async def run_pipeline(
    filters: dict[str, Any],
    platforms: list[str] | None = None,
    user_id: int = 1,
) -> str:
    """运行六 Agent 协作 pipeline，返回 session_id。

    异步启动 pipeline，立即返回 session_id。
    前端通过 get_session(session_id) 轮询进度。

    Args:
        filters: 初始状态字典，至少含 query 字段
        platforms: 平台列表（默认 ["ccgp"]）
        user_id: 用户 ID

    Returns:
        session_id: 用于查询进度

    Example:
        >>> session_id = await run_pipeline({"query": "上海充电桩"})
        >>> session = await get_session(session_id)
        >>> print(session["stage"])  # "intent" / "collecting" / ... / "done"
    """
    session_id = uuid.uuid4().hex

    # 初始化 session
    async with _sessions_lock:
        _sessions[session_id] = {
            "session_id": session_id,
            "stage": "intent",
            "progress": 0,
            "message": "Pipeline 启动中",
            "started_at": asyncio.get_event_loop().time(),
            "updated_at": asyncio.get_event_loop().time(),
            "finished_at": None,
            "error": None,
            "result": None,
            # 六阶段进度
            "stages": {
                "intent": {"status": "pending", "started_at": None, "finished_at": None},
                "collecting": {"status": "pending", "started_at": None, "finished_at": None},
                "processing": {"status": "pending", "started_at": None, "finished_at": None},
                "quality": {"status": "pending", "started_at": None, "finished_at": None},
                "finance": {"status": "pending", "started_at": None, "finished_at": None},
                "done": {"status": "pending", "started_at": None, "finished_at": None},
            },
        }

    # 异步启动 pipeline（不阻塞）
    asyncio.create_task(_run_pipeline_async(session_id, filters, platforms, user_id))

    logger.info("pipeline started session_id={}", session_id)
    return session_id


async def get_session(session_id: str) -> dict[str, Any] | None:
    """查询 session 状态。

    Args:
        session_id: run_pipeline 返回的 session_id

    Returns:
        session 字典，含 stage / progress / message / result 等字段。
        如果 session_id 不存在，返回 None。
    """
    async with _sessions_lock:
        session = _sessions.get(session_id)
        if session is None:
            return None
        return dict(session)  # 返回副本，避免外部修改


async def _run_pipeline_async(
    session_id: str,
    filters: dict[str, Any],
    platforms: list[str] | None,
    user_id: int,
) -> None:
    """实际执行六 Agent pipeline（异步任务）。"""
    logger.info("pipeline async started session_id={}", session_id)

    # 构建初始 state
    initial_state = {
        "query": filters.get("query", ""),
        "user_id": user_id,
        "platforms": platforms or ["ccgp"],
        "session_id": session_id,  # 供 Agent 内部更新 session
    }

    # 构建六 Agent 图
    graph = _build_six_agent_graph()

    try:
        # 每个 Agent 执行前更新 session
        await _update_stage(session_id, "intent", "running")

        # 包装每个 Agent，在执行前后更新 session
        final_state = await graph.run(initial_state)

        # 标记完成
        await _mark_done(session_id, final_state)

    except Exception as exc:  # noqa: BLE001
        logger.exception("pipeline failed session_id={}", session_id)
        await _mark_error(session_id, str(exc))


def _build_six_agent_graph() -> AgentGraph:
    """构建六 Agent 协作图。"""
    graph = AgentGraph()
    graph.add_agent(
        name="intent",
        description="意图解析：解析用户查询的 5 槽位",
        func=intent_agent,
        next_agent="collect",
        is_entry=True,
    )
    graph.add_agent(
        name="collect",
        description="采集执行：调度多平台采集器并行抓取",
        func=collector_agent,
        next_agent="process",
    )
    graph.add_agent(
        name="process",
        description="数据加工：字段对齐 + 分类标注 + 相关性评分",
        func=processor_agent,
        next_agent="quality",
    )
    graph.add_agent(
        name="quality",
        description="质量保障：SimHash 去重 + 反幻觉校验",
        func=quality_agent,
        next_agent="finance",
    )
    graph.add_agent(
        name="finance",
        description="金融分析：BOQ 异常（实验性）+ 废标风险 + 供应商公开活动观察度",
        func=finance_agent,
        next_agent="delivery",
    )
    graph.add_agent(
        name="delivery",
        description="报告交付：Word 报告 + SMTP/Webhook 推送",
        func=delivery_agent,
    )
    return graph


# ==== Session 状态更新辅助函数 ====

_STAGE_PROGRESS = {
    "intent": 10,
    "collecting": 30,
    "processing": 55,
    "quality": 70,
    "finance": 85,
    "done": 100,
}


async def _update_stage(session_id: str, stage: str, status: str) -> None:
    """更新 session 的某个阶段状态。"""
    async with _sessions_lock:
        session = _sessions.get(session_id)
        if session is None:
            return
        now = asyncio.get_event_loop().time()
        session["stage"] = stage
        session["progress"] = _STAGE_PROGRESS.get(stage, 0)
        session["updated_at"] = now
        if stage in session["stages"]:
            session["stages"][stage]["status"] = status
            if status == "running":
                session["stages"][stage]["started_at"] = now
            elif status in ("completed", "failed"):
                session["stages"][stage]["finished_at"] = now


async def _mark_done(session_id: str, final_state: dict[str, Any]) -> None:
    """标记 pipeline 完成。"""
    async with _sessions_lock:
        session = _sessions.get(session_id)
        if session is None:
            return
        now = asyncio.get_event_loop().time()
        session["stage"] = "done"
        session["progress"] = 100
        session["message"] = "Pipeline 完成"
        session["finished_at"] = now
        session["updated_at"] = now

        # 提取最终结果
        session["result"] = {
            "parsed_filters": _safe_dict(final_state.get("parsed_filters")),
            "collect_summary": final_state.get("collect_summary", {}),
            "process_summary": final_state.get("process_summary", {}),
            "quality_summary": final_state.get("quality_summary", {}),
            "finance_summary": final_state.get("finance_summary", {}),
            "delivery_summary": final_state.get("delivery_summary", {}),
            "report_path": final_state.get("report_path"),
            "execution_summary": final_state.get("_execution_summary", {}),
        }


async def _mark_error(session_id: str, error: str) -> None:
    """标记 pipeline 失败。"""
    async with _sessions_lock:
        session = _sessions.get(session_id)
        if session is None:
            return
        now = asyncio.get_event_loop().time()
        session["stage"] = "error"
        session["message"] = f"Pipeline 失败: {error}"
        session["error"] = error
        session["finished_at"] = now
        session["updated_at"] = now


def _safe_dict(obj: Any) -> dict[str, Any]:
    """安全转 dict（兼容 Pydantic model 和普通对象）。"""
    if obj is None:
        return {}
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, dict):
        return obj
    return {"__repr__": repr(obj)}


# ==== 兼容旧接口（保留 run_multi_agent_workflow 供测试使用）====

async def run_multi_agent_workflow(
    query: str,
    user_id: int = 1,
    platforms: list[str] | None = None,
) -> dict[str, Any]:
    """运行多 Agent 工作流（同步版本，供测试和答辩演示使用）。

    与 run_pipeline 的区别：
    - run_pipeline 异步启动，返回 session_id 立即退出
    - run_multi_agent_workflow 同步等待全部完成，返回最终 state

    Args:
        query: 用户查询
        user_id: 用户 ID
        platforms: 平台列表

    Returns:
        最终 state 字典
    """
    initial_state = {
        "query": query,
        "user_id": user_id,
        "platforms": platforms or ["ccgp"],
    }
    graph = _build_six_agent_graph()
    return await graph.run(initial_state)
