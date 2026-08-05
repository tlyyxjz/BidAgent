"""金融分析 Agent（v4.1 合规版）。

v4.1 边界：
- 只输出公开招投标活动观察信号
- 不输出信用评分
- 不实施 BOQ 异常检测
- 不实施废标风险预警
"""
from __future__ import annotations

import logging
from typing import Any

from app.processors.observation_signals import analyze_observation_signals

logger = logging.getLogger("finance_agent")


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """金融分析 Agent 主入口：生成 6 维公开活动观察信号。

    严格不输出信用评分，不调用 BOQ/废标引擎。
    """
    tenders = state.get("quality_tenders") or state.get("processed_tenders") or []

    if not tenders:
        logger.warning("finance_agent: 无可用公告数据")
        state.setdefault("finance_summary", {})
        return state

    try:
        signals = analyze_observation_signals(tenders)
        state["observation_signals"] = signals
        state["finance_summary"] = {"observation_signals": signals}
        logger.info("finance_agent: 6 维观察信号生成完成")
    except Exception as exc:
        logger.exception("finance_agent: 观察信号生成失败")
        state.setdefault("finance_summary", {})

    # AgentGraph 约定：Agent 返回完整 state（含 _agent_history 与前序输出）
    return state


async def main(state: dict[str, Any]) -> dict[str, Any]:
    """兼容旧入口，等价于 run。"""
    return await run(state)


class _FinanceAgentCompat:
    """兼容 pipeline.py 中 finance_agent = FinanceAgent() 的调用。"""
    async def run(self, state):
        return await run(state)

    async def __call__(self, state):
        # coordinator.AgentGraph 以 await func(state) 方式驱动各 Agent
        return await run(state)


finance_agent = _FinanceAgentCompat()
