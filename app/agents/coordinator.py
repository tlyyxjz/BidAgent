"""轻量级多 Agent 协作框架（GOAI 大赛答辩差异化亮点）。

设计原则：
- 不依赖 langgraph（避免依赖膨胀 + 兼容性问题）
- 纯 Python 实现 Agent 图，状态通过 dict 传递
- 每个 Agent 是 async 函数，签名：async def agent(state: dict) -> dict
- 支持顺序执行 + 简单条件分支
- 完整的执行日志，便于答辩时展示协作流程

六 Agent 协同架构（BidAgent）：
- Agent 1: 意图解析（app/agents/intent_agent.py）
- Agent 2: 采集执行（app/agents/collector_agent.py）
- Agent 3: 数据加工（app/agents/processor_agent.py）
- Agent 4: 质量保障（app/agents/quality_agent.py）
- Agent 5: 金融分析（app/agents/finance_agent.py）⭐核心
- Agent 6: 报告交付（app/agents/delivery_agent.py）

编排入口：app/agents/pipeline.py 的 run_pipeline()
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.utils.logger import get_logger

logger = get_logger("agent_coordinator")

# Agent 函数签名
AgentFunc = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass
class AgentStep:
    """一个 Agent 执行步骤。"""

    name: str                          # Agent 名
    description: str                   # 职责描述
    func: AgentFunc                    # 执行函数
    next_agent: str | None = None      # 下一个 Agent 名（None 表示结束）
    condition: Callable[[dict[str, Any]], bool] | None = None  # 执行条件


@dataclass
class ExecutionTrace:
    """Agent 执行轨迹（用于答辩展示）。"""

    agent_name: str
    started_at: float
    finished_at: float
    duration_ms: float
    success: bool
    input_summary: dict[str, Any] = field(default_factory=dict)
    output_summary: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class AgentGraph:
    """轻量级 Agent 图：顺序执行 + 条件分支。

    用法：
        graph = AgentGraph()
        graph.add_agent("intent", "解析用户意图", intent_agent, next_agent="collect")
        graph.add_agent("collect", "采集数据", collector_agent, next_agent="process")
        graph.add_agent("process", "数据加工", processor_agent, next_agent="quality")
        graph.add_agent("quality", "质量保障", quality_agent, next_agent="finance")
        graph.add_agent("finance", "金融分析", finance_agent, next_agent="delivery")
        graph.add_agent("delivery", "报告交付", delivery_agent)
        result = await graph.run({"query": "上海充电桩"})
    """

    def __init__(self) -> None:
        self._agents: dict[str, AgentStep] = {}
        self._entry: str | None = None
        self._traces: list[ExecutionTrace] = []

    def add_agent(
        self,
        name: str,
        description: str,
        func: AgentFunc,
        next_agent: str | None = None,
        condition: Callable[[dict[str, Any]], bool] | None = None,
        is_entry: bool = False,
    ) -> "AgentGraph":
        """注册一个 Agent。"""
        self._agents[name] = AgentStep(
            name=name,
            description=description,
            func=func,
            next_agent=next_agent,
            condition=condition,
        )
        if is_entry or self._entry is None:
            self._entry = name
        return self

    @property
    def traces(self) -> list[ExecutionTrace]:
        """返回执行轨迹。"""
        return self._traces

    async def run(self, initial_state: dict[str, Any]) -> dict[str, Any]:
        """从入口 Agent 开始执行整个图。

        Args:
            initial_state: 初始状态字典

        Returns:
            最终状态字典（含所有 Agent 的输出累积）
        """
        if self._entry is None:
            raise RuntimeError("AgentGraph has no entry agent")

        state = dict(initial_state)
        state["_agent_history"] = []
        self._traces.clear()

        current = self._entry
        max_steps = 20  # 防止死循环
        steps = 0

        logger.info(
            "agent graph started entry={} initial_keys={}",
            current, list(initial_state.keys()),
        )

        while current is not None and steps < max_steps:
            step = self._agents.get(current)
            if step is None:
                logger.warning("agent not found: {}", current)
                break

            # 条件检查
            if step.condition is not None and not step.condition(state):
                logger.info("agent skipped (condition false): {}", current)
                current = step.next_agent
                steps += 1
                continue

            started = time.time()
            success = True
            error: str | None = None
            input_snapshot = _snapshot_state(state)

            try:
                logger.info("agent started: {} ({})", step.name, step.description)
                state = await step.func(state)
                state["_agent_history"].append(step.name)
                logger.info("agent completed: {}", step.name)
            except Exception as exc:  # noqa: BLE001
                success = False
                error = str(exc)
                logger.exception("agent failed: {}", step.name)
                state["_last_error"] = {
                    "agent": step.name,
                    "error": error,
                }

            # 无论成功失败都记录 trace（失败时也记录便于排障）
            finished = time.time()
            trace = ExecutionTrace(
                agent_name=step.name,
                started_at=started,
                finished_at=finished,
                duration_ms=(finished - started) * 1000,
                success=success,
                input_summary=input_snapshot,
                output_summary=_snapshot_state(state),
                error=error,
            )
            self._traces.append(trace)

            # 失败时终止整个图（在记录 trace 之后）
            if not success:
                break

            current = step.next_agent
            steps += 1

        state["_execution_summary"] = self.get_summary()
        logger.info("agent graph finished steps={}", steps)
        return state

    def get_summary(self) -> dict[str, Any]:
        """返回执行摘要（用于答辩展示）。"""
        return {
            "total_agents": len(self._traces),
            "successful": sum(1 for t in self._traces if t.success),
            "failed": sum(1 for t in self._traces if not t.success),
            "total_duration_ms": sum(t.duration_ms for t in self._traces),
            "agents": [
                {
                    "name": t.agent_name,
                    "duration_ms": round(t.duration_ms, 2),
                    "success": t.success,
                    "error": t.error,
                }
                for t in self._traces
            ],
        }


def _snapshot_state(state: dict[str, Any]) -> dict[str, Any]:
    """截取 state 的摘要（避免记录大对象）。"""
    snapshot: dict[str, Any] = {}
    for k, v in state.items():
        if k.startswith("_"):
            continue
        if isinstance(v, (str, int, float, bool, type(None))):
            snapshot[k] = v
        elif isinstance(v, list):
            snapshot[k] = f"list[{len(v)}]"
        elif isinstance(v, dict):
            snapshot[k] = f"dict[{len(v)} keys]"
        else:
            snapshot[k] = type(v).__name__
    return snapshot
