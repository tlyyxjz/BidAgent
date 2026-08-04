"""六 Agent 协作测试。

覆盖：
1. AgentGraph 框架基础功能（add_agent / run / traces / condition）
2. 六个 Agent 的单独可调用性（mock 依赖）
3. pipeline.run_pipeline + get_session 异步流程
4. 六 Agent 协作端到端（mock 采集，验证状态流转）
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.coordinator import AgentGraph, AgentStep, ExecutionTrace


# ==== 1. AgentGraph 框架测试 ====

class TestAgentGraphFramework:
    """AgentGraph 框架基础功能测试。"""

    @pytest.mark.asyncio
    async def test_graph_runs_agents_in_order(self):
        """Agent 按注册顺序执行。"""
        calls: list[str] = []

        async def agent_a(state):
            calls.append("a")
            state["a"] = True
            return state

        async def agent_b(state):
            calls.append("b")
            state["b"] = True
            return state

        graph = AgentGraph()
        graph.add_agent("a", "Agent A", agent_a, next_agent="b", is_entry=True)
        graph.add_agent("b", "Agent B", agent_b)

        result = await graph.run({})

        assert calls == ["a", "b"]
        assert result["a"] is True
        assert result["b"] is True
        assert result["_agent_history"] == ["a", "b"]

    @pytest.mark.asyncio
    async def test_graph_records_traces(self):
        """Agent 执行后记录 trace。"""
        async def agent_a(state):
            return state

        graph = AgentGraph()
        graph.add_agent("a", "Agent A", agent_a)

        await graph.run({})

        assert len(graph.traces) == 1
        trace = graph.traces[0]
        assert trace.agent_name == "a"
        assert trace.success is True
        assert trace.duration_ms >= 0
        assert trace.error is None

    @pytest.mark.asyncio
    async def test_graph_stops_on_failure(self):
        """Agent 失败时终止整个图。"""
        calls: list[str] = []

        async def agent_a(state):
            calls.append("a")
            raise RuntimeError("agent a failed")

        async def agent_b(state):
            calls.append("b")  # 不应该执行
            return state

        graph = AgentGraph()
        graph.add_agent("a", "Agent A", agent_a, next_agent="b", is_entry=True)
        graph.add_agent("b", "Agent B", agent_b)

        result = await graph.run({})

        assert calls == ["a"]  # b 没执行
        assert len(graph.traces) == 1
        assert graph.traces[0].success is False
        assert "agent a failed" in graph.traces[0].error
        assert "_last_error" in result

    @pytest.mark.asyncio
    async def test_graph_skips_agent_when_condition_false(self):
        """条件不满足时跳过 Agent。"""
        calls: list[str] = []

        async def agent_a(state):
            calls.append("a")
            return state

        async def agent_b(state):
            calls.append("b")
            return state

        graph = AgentGraph()
        graph.add_agent("a", "Agent A", agent_a, next_agent="b", is_entry=True)
        graph.add_agent(
            "b", "Agent B", agent_b,
            condition=lambda state: state.get("skip_b") is not True,
        )

        await graph.run({"skip_b": True})

        assert calls == ["a"]  # b 被跳过

    @pytest.mark.asyncio
    async def test_graph_max_steps_prevents_infinite_loop(self):
        """max_steps 防止死循环。"""
        async def agent_a(state):
            return state

        # 构建一个自循环（a -> a）
        graph = AgentGraph()
        graph.add_agent("a", "Agent A", agent_a, next_agent="a", is_entry=True)

        await graph.run({})

        # 最多 20 步
        assert len(graph.traces) <= 20

    @pytest.mark.asyncio
    async def test_graph_raises_when_no_entry(self):
        """没有入口 Agent 时抛 RuntimeError。"""
        graph = AgentGraph()
        with pytest.raises(RuntimeError, match="no entry agent"):
            await graph.run({})

    @pytest.mark.asyncio
    async def test_graph_summary_contains_all_agents(self):
        """执行摘要包含所有 Agent 信息。"""
        async def agent_a(state):
            return state

        async def agent_b(state):
            return state

        graph = AgentGraph()
        graph.add_agent("a", "Agent A", agent_a, next_agent="b", is_entry=True)
        graph.add_agent("b", "Agent B", agent_b)

        result = await graph.run({})

        summary = result["_execution_summary"]
        assert summary["total_agents"] == 2
        assert summary["successful"] == 2
        assert summary["failed"] == 0
        assert len(summary["agents"]) == 2
        assert summary["agents"][0]["name"] == "a"
        assert summary["agents"][1]["name"] == "b"


# ==== 2. 单个 Agent 可调用性测试 ====

class TestIndividualAgents:
    """六个 Agent 的单独可调用性测试。"""

    @pytest.mark.asyncio
    async def test_intent_agent_parses_query(self):
        """意图解析 Agent 能解析查询。"""
        from app.agents.intent_agent import intent_agent

        mock_parsed = MagicMock()
        mock_parsed.topic = "充电桩"
        mock_parsed.region = "上海"
        mock_parsed.trigger_type = "immediate"

        with patch("app.llm.parser.parse_query", new_callable=AsyncMock) as mock_parse:
            mock_parse.return_value = mock_parsed
            state = await intent_agent({"query": "上海充电桩"})

        assert state["parsed_filters"] is mock_parsed
        assert state["topic"] == "充电桩"
        assert state["region"] == "上海"
        assert state["trigger_type"] == "immediate"
        assert "missing_slots" in state

    @pytest.mark.asyncio
    async def test_intent_agent_raises_on_empty_query(self):
        """空 query 抛 ValueError。"""
        from app.agents.intent_agent import intent_agent

        with pytest.raises(ValueError, match="query is required"):
            await intent_agent({"query": ""})

    @pytest.mark.asyncio
    async def test_processor_agent_classifies_category(self):
        """数据加工 Agent 能分类标注。"""
        from app.agents.processor_agent import _classify_category

        assert _classify_category("采购100台电脑") == "IT"
        assert _classify_category("网络设备采购") == "IT"
        assert _classify_category("医院装修工程") == "医疗"
        assert _classify_category("医疗器械采购") == "医疗"
        assert _classify_category("教学楼建设") == "教育"
        assert _classify_category("办公楼改造工程") == "工程"
        assert _classify_category("办公用品") == "其他"

    @pytest.mark.asyncio
    async def test_processor_agent_computes_relevance(self):
        """数据加工 Agent 能计算相关性评分。"""
        from app.agents.processor_agent import _compute_relevance

        # 完全匹配
        assert _compute_relevance("充电桩采购", "核心内容", "充电桩") == 1.0
        # 无主题
        assert _compute_relevance("任意项目", "内容", "") == 0.5

    @pytest.mark.asyncio
    async def test_finance_agent_returns_default_when_no_tenders(self):
        """金融分析 Agent 在无公告数据时返回空观察信号（v4.1 合规版）。"""
        from app.agents.finance_agent import finance_agent

        state = await finance_agent.run({
            "quality_summary": {},
            "subscription_id": 1,
            "collect_summary": {},
        })

        # v4.1: 只输出观察信号，不输出 BOQ/废标/信用评分
        assert "observation_signals" in state
        assert state["observation_signals"] == {}


# ==== 3. Pipeline session 管理测试 ====

class TestPipelineSession:
    """pipeline.run_pipeline + get_session 测试。"""

    @pytest.mark.asyncio
    async def test_run_pipeline_returns_session_id(self):
        """run_pipeline 返回 session_id。"""
        from app.agents import pipeline

        # mock 六个 Agent，避免真实执行
        async def mock_agent(state):
            return state

        with patch.object(pipeline, "_build_six_agent_graph") as mock_build:
            graph = AgentGraph()
            graph.add_agent("intent", "mock", mock_agent, is_entry=True)
            mock_build.return_value = graph

            session_id = await pipeline.run_pipeline({"query": "测试"})

        assert isinstance(session_id, str)
        assert len(session_id) > 0

        # session 应该已创建
        session = await pipeline.get_session(session_id)
        assert session is not None
        assert session["session_id"] == session_id

    @pytest.mark.asyncio
    async def test_get_session_returns_none_for_unknown_id(self):
        """未知 session_id 返回 None。"""
        from app.agents.pipeline import get_session

        result = await get_session("nonexistent-id-12345")
        assert result is None

    @pytest.mark.asyncio
    async def test_pipeline_session_has_required_fields(self):
        """session 字典包含必需字段。"""
        from app.agents import pipeline

        async def mock_agent(state):
            return state

        with patch.object(pipeline, "_build_six_agent_graph") as mock_build:
            graph = AgentGraph()
            graph.add_agent("intent", "mock", mock_agent, is_entry=True)
            mock_build.return_value = graph

            session_id = await pipeline.run_pipeline({"query": "测试"})

        session = await pipeline.get_session(session_id)
        # 必需字段
        assert "session_id" in session
        assert "stage" in session
        assert "progress" in session
        assert "message" in session
        assert "started_at" in session
        assert "updated_at" in session
        assert "finished_at" in session
        assert "error" in session
        assert "result" in session
        assert "stages" in session
        # 六阶段
        assert "intent" in session["stages"]
        assert "collecting" in session["stages"]
        assert "processing" in session["stages"]
        assert "quality" in session["stages"]
        assert "finance" in session["stages"]
        assert "done" in session["stages"]


# ==== 4. 六 Agent 协作端到端测试 ====

class TestSixAgentEndToEnd:
    """六 Agent 协作端到端测试（mock 采集）。"""

    @pytest.mark.asyncio
    async def test_six_agent_graph_structure(self):
        """六 Agent 图结构正确（六个节点 + 五条边）。"""
        from app.agents.pipeline import _build_six_agent_graph

        graph = _build_six_agent_graph()

        # 六个 Agent 全部注册
        assert "intent" in graph._agents
        assert "collect" in graph._agents
        assert "process" in graph._agents
        assert "quality" in graph._agents
        assert "finance" in graph._agents
        assert "delivery" in graph._agents

        # 入口是 intent
        assert graph._entry == "intent"

        # 边：intent -> collect -> process -> quality -> finance -> delivery
        assert graph._agents["intent"].next_agent == "collect"
        assert graph._agents["collect"].next_agent == "process"
        assert graph._agents["process"].next_agent == "quality"
        assert graph._agents["quality"].next_agent == "finance"
        assert graph._agents["finance"].next_agent == "delivery"
        assert graph._agents["delivery"].next_agent is None  # 终点

    @pytest.mark.asyncio
    async def test_six_agent_execution_order(self):
        """六 Agent 按顺序执行（mock 全部 Agent）。"""
        calls: list[str] = []

        async def make_agent(name: str):
            async def agent(state):
                calls.append(name)
                return state
            return agent

        graph = AgentGraph()
        graph.add_agent("intent", "意图", await make_agent("intent"), next_agent="collect", is_entry=True)
        graph.add_agent("collect", "采集", await make_agent("collect"), next_agent="process")
        graph.add_agent("process", "加工", await make_agent("process"), next_agent="quality")
        graph.add_agent("quality", "质检", await make_agent("quality"), next_agent="finance")
        graph.add_agent("finance", "金融", await make_agent("finance"), next_agent="delivery")
        graph.add_agent("delivery", "交付", await make_agent("delivery"))

        await graph.run({})

        assert calls == ["intent", "collect", "process", "quality", "finance", "delivery"]

    @pytest.mark.asyncio
    async def test_six_agent_failure_records_trace(self):
        """某个 Agent 失败时记录 trace 并终止。"""
        async def success_agent(state):
            return state

        async def fail_agent(state):
            raise RuntimeError("金融分析失败")

        graph = AgentGraph()
        graph.add_agent("intent", "意图", success_agent, next_agent="collect", is_entry=True)
        graph.add_agent("collect", "采集", success_agent, next_agent="process")
        graph.add_agent("process", "加工", success_agent, next_agent="quality")
        graph.add_agent("quality", "质检", success_agent, next_agent="finance")
        graph.add_agent("finance", "金融", fail_agent, next_agent="delivery")
        graph.add_agent("delivery", "交付", success_agent)

        result = await graph.run({})

        # 前四个成功，finance 失败，delivery 没执行
        assert len(graph.traces) == 5
        assert graph.traces[0].success is True  # intent
        assert graph.traces[3].success is True  # quality
        assert graph.traces[4].success is False  # finance 失败
        assert "金融分析失败" in graph.traces[4].error
        assert result["_last_error"]["agent"] == "finance"


# ==== 5. 兼容性测试 ====

class TestBackwardCompatibility:
    """向后兼容性测试（确保现有测试不破坏）。"""

    @pytest.mark.asyncio
    async def test_run_multi_agent_workflow_still_exists(self):
        """run_multi_agent_workflow 接口仍可用（供旧测试调用）。"""
        from app.agents.pipeline import run_multi_agent_workflow

        assert callable(run_multi_agent_workflow)

    def test_coordinator_exports_preserved(self):
        """coordinator.py 仍导出 AgentGraph / AgentStep / ExecutionTrace。"""
        from app.agents.coordinator import (
            AgentGraph,
            AgentStep,
            ExecutionTrace,
        )

        assert AgentGraph is not None
        assert AgentStep is not None
        assert ExecutionTrace is not None

    def test_agent_files_exist(self):
        """六个 Agent 文件全部存在。"""
        import importlib

        modules = [
            "app.agents.intent_agent",
            "app.agents.collector_agent",
            "app.agents.processor_agent",
            "app.agents.quality_agent",
            "app.agents.finance_agent",
            "app.agents.delivery_agent",
            "app.agents.pipeline",
        ]
        for mod_name in modules:
            mod = importlib.import_module(mod_name)
            assert mod is not None, f"{mod_name} 导入失败"
