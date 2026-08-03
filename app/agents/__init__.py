"""多 Agent 协作模块（GOAI 大赛答辩差异化亮点）。

六 Agent 协同架构（标小智）：
- Agent 1: 意图解析（app/agents/intent_agent.py）
- Agent 2: 采集执行（app/agents/collector_agent.py）
- Agent 3: 数据加工（app/agents/processor_agent.py）
- Agent 4: 质量保障（app/agents/quality_agent.py）
- Agent 5: 金融分析（app/agents/finance_agent.py）⭐核心
- Agent 6: 报告交付（app/agents/delivery_agent.py）

编排入口：app/agents/pipeline.py 的 run_pipeline()
"""

from app.agents.coordinator import AgentGraph, AgentStep, ExecutionTrace
from app.agents.pipeline import get_session, run_multi_agent_workflow, run_pipeline

__all__ = [
    "AgentGraph",
    "AgentStep",
    "ExecutionTrace",
    "run_pipeline",
    "get_session",
    "run_multi_agent_workflow",
]
