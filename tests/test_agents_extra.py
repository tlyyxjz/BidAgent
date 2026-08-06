"""补充测试：processor_agent / quality_agent / pipeline 未覆盖路径。

覆盖目标：
- app/agents/processor_agent.py：run() 主流程、分类/评分分支、异常路径
- app/agents/quality_agent.py：run() 主流程、反幻觉校验、质量评分
- app/agents/pipeline.py：run_pipeline 异步执行、_mark_done/_mark_error、
  _update_stage 各状态、_safe_dict 各分支、run_multi_agent_workflow

依赖外部服务（DB / LLM）的部分用 unittest.mock 模拟。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.coordinator import AgentGraph


# ==== 公共辅助：模拟 AsyncSessionLocal ====

class _FakeSession:
    """模拟异步 DB session，execute 返回固定 tenders 列表。"""

    def __init__(self, tenders):
        self._tenders = tenders
        result = MagicMock()
        result.scalars.return_value.all.return_value = tenders
        self.execute = AsyncMock(return_value=result)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _make_db_factory(tenders):
    """构造 AsyncSessionLocal 替身：每次调用返回一个 _FakeSession 上下文管理器。"""
    return MagicMock(return_value=_FakeSession(tenders))


def _make_tender(**kwargs):
    """构造一条 mock Tender。"""
    t = MagicMock()
    t.id = kwargs.get("id", 1)
    t.project_name = kwargs.get("project_name", "")
    t.core_content = kwargs.get("core_content", "")
    t.source_url = kwargs.get("source_url", None)
    t.source_raw_text = kwargs.get("source_raw_text", None)
    t.source_platform = kwargs.get("source_platform", "ccgp")
    t.tender_org = kwargs.get("tender_org", "")
    return t


def _make_parsed(**kwargs):
    """构造 mock ParsedFilters 对象。"""
    parsed = MagicMock()
    parsed.topic = kwargs.get("topic", "")
    parsed.region = kwargs.get("region", "")
    parsed.keywords = kwargs.get("keywords", [])
    parsed.raw_query = kwargs.get("raw_query", "")
    return parsed


# ==== processor_agent 测试 ====

class TestProcessorAgent:
    """processor_agent 主流程测试。"""

    @pytest.mark.asyncio
    async def test_raises_when_subscription_id_missing(self):
        """缺少 subscription_id 时抛 ValueError。"""
        from app.agents.processor_agent import processor_agent

        with pytest.raises(ValueError, match="subscription_id is required"):
            await processor_agent({"collect_summary": {}})

    @pytest.mark.asyncio
    async def test_empty_tenders_returns_zero_processed(self):
        """无招标数据时返回 0 加工数。"""
        from app.agents.processor_agent import processor_agent

        factory = _make_db_factory([])
        with patch("app.models.database.AsyncSessionLocal", factory):
            state = await processor_agent({
                "collect_summary": {
                    "total": 0,
                    "platforms_collected": ["ccgp"],
                },
                "subscription_id": 1,
            })

        ps = state["process_summary"]
        assert ps["total_processed"] == 0
        assert ps["category_distribution"] == {}
        assert ps["avg_relevance_score"] == 0.0

    @pytest.mark.asyncio
    async def test_classifies_and_scores_with_topic(self):
        """有主题时正确分类并计算平均相关性。"""
        from app.agents.processor_agent import processor_agent

        tenders = [
            _make_tender(id=1, project_name="医院医疗设备采购", core_content="医疗"),
            _make_tender(id=2, project_name="教学楼建设", core_content="教学"),
            _make_tender(id=3, project_name="服务器采购", core_content="IT"),
        ]
        factory = _make_db_factory(tenders)
        parsed = _make_parsed(topic="医疗")
        with patch("app.models.database.AsyncSessionLocal", factory):
            state = await processor_agent({
                "collect_summary": {
                    "total": 3,
                    "platforms_collected": ["ccgp"],
                },
                "subscription_id": 1,
                "parsed_filters": parsed,
            })

        ps = state["process_summary"]
        assert ps["total_processed"] == 3
        assert ps["category_distribution"]["医疗"] == 1
        assert ps["category_distribution"]["教育"] == 1
        assert ps["category_distribution"]["IT"] == 1
        # t1: topic "医疗" in text → 1.0；t2/t3: 字符匹配 0 → 0.0
        assert ps["avg_relevance_score"] == round(1 / 3, 3)

    @pytest.mark.asyncio
    async def test_no_parsed_filters_defaults_to_empty_topic(self):
        """无 parsed_filters 时 topic/region/keywords 均为空。"""
        from app.agents.processor_agent import processor_agent

        tenders = [_make_tender(id=1, project_name="办公用品", core_content="")]
        factory = _make_db_factory(tenders)
        with patch("app.models.database.AsyncSessionLocal", factory):
            state = await processor_agent({
                "collect_summary": {"total": 1, "platforms_collected": ["ccgp"]},
                "subscription_id": 7,
            })

        ps = state["process_summary"]
        assert ps["total_processed"] == 1
        assert ps["category_distribution"]["其他"] == 1
        # 无 topic → _compute_relevance 返回 0.5
        assert ps["avg_relevance_score"] == 0.5

    @pytest.mark.asyncio
    async def test_raw_query_extracts_extra_keywords(self):
        """raw_query 中非地区词被加入 search_words。"""
        from app.agents.processor_agent import processor_agent

        tenders = [_make_tender(id=1, project_name="服务器采购", core_content="")]
        factory = _make_db_factory(tenders)
        # topic=充电桩，raw_query 含 "北京 服务器 招标"
        # "北京"/"招标"/"公告" 被过滤，"服务器" 作为额外关键词加入
        parsed = _make_parsed(topic="充电桩", raw_query="北京 服务器 招标")
        with patch("app.models.database.AsyncSessionLocal", factory):
            state = await processor_agent({
                "collect_summary": {"total": 1, "platforms_collected": ["ccgp"]},
                "subscription_id": 1,
                "parsed_filters": parsed,
            })

        assert state["process_summary"]["total_processed"] == 1

    @pytest.mark.asyncio
    async def test_region_and_keywords_filter_combined(self):
        """同时传入 region + keywords 时不报错并产出摘要。"""
        from app.agents.processor_agent import processor_agent

        tenders = [
            _make_tender(
                id=1,
                project_name="上海充电桩采购",
                core_content="上海 直流",
                tender_org="上海交通委",
            ),
        ]
        factory = _make_db_factory(tenders)
        parsed = _make_parsed(
            topic="充电桩", region="上海", keywords=["直流"],
        )
        with patch("app.models.database.AsyncSessionLocal", factory):
            state = await processor_agent({
                "collect_summary": {"total": 1, "platforms_collected": ["ccgp"]},
                "subscription_id": 1,
                "parsed_filters": parsed,
            })

        assert state["process_summary"]["total_processed"] == 1

    @pytest.mark.asyncio
    async def test_collect_summary_without_platforms_defaults_ccgp(self):
        """collect_summary 缺 platforms_collected 时默认 ccgp。"""
        from app.agents.processor_agent import processor_agent

        factory = _make_db_factory([])
        with patch("app.models.database.AsyncSessionLocal", factory):
            state = await processor_agent({
                "collect_summary": {},
                "subscription_id": 1,
            })

        assert state["process_summary"]["total_processed"] == 0


class TestProcessorAgentHelpers:
    """processor_agent 内部辅助函数测试。"""

    @pytest.mark.parametrize("name,expected", [
        ("药品采购项目", "医疗"),
        ("医院装修工程", "医疗"),
        ("器械采购", "医疗"),
        ("图书馆建设", "教育"),
        ("学校网络改造", "教育"),
        ("教学设备采购", "教育"),
        ("信息化系统建设", "IT"),
        ("软件采购项目", "IT"),
        ("电脑网络设备", "IT"),
        ("施工工程", "工程"),
        ("办公楼装修", "工程"),
        ("道路改造", "工程"),
        ("random stuff", "其他"),
        ("IT infrastructure", "IT"),
    ])
    def test_classify_category_all_branches(self, name, expected):
        """分类标注覆盖所有品类分支。"""
        from app.agents.processor_agent import _classify_category

        assert _classify_category(name) == expected

    def test_compute_relevance_no_topic_returns_mid(self):
        """无主题时返回中等评分 0.5。"""
        from app.agents.processor_agent import _compute_relevance

        assert _compute_relevance("proj", "content", "") == 0.5

    def test_compute_relevance_full_match_returns_one(self):
        """主题完全包含在文本中返回 1.0。"""
        from app.agents.processor_agent import _compute_relevance

        assert _compute_relevance("充电桩采购", "核心", "充电桩") == 1.0

    def test_compute_relevance_whitespace_topic_returns_mid(self):
        """主题仅含空白字符时返回 0.5（topic_words 为空分支）。"""
        from app.agents.processor_agent import _compute_relevance

        assert _compute_relevance("proj", "content", "   ") == 0.5

    def test_compute_relevance_partial_char_match(self):
        """主题不在文本中但部分字符匹配时返回 matched/total。"""
        from app.agents.processor_agent import _compute_relevance

        # topic="abx"，文本含 a/b 不含 x → 2/3
        assert _compute_relevance("a b", "content", "abx") == 2 / 3


# ==== quality_agent 测试 ====

class TestQualityAgent:
    """quality_agent 主流程测试。"""

    @pytest.mark.asyncio
    async def test_empty_tenders_full_quality_score(self):
        """无招标数据时质量评分为 1.0。"""
        from app.agents.quality_agent import quality_agent

        factory = _make_db_factory([])
        with patch("app.models.database.AsyncSessionLocal", factory):
            state = await quality_agent({
                "collect_summary": {
                    "total": 0,
                    "duplicates": 0,
                    "platforms_collected": ["ccgp"],
                },
                "process_summary": {"total_processed": 0},
                "subscription_id": 1,
            })

        qs = state["quality_summary"]
        assert qs["total_checked"] == 0
        assert qs["hallucination_flags"] == 0
        assert qs["quality_score"] == 1.0
        assert qs["dedup_rate"] == 1.0
        assert qs["hallucination_pass_rate"] == 1.0

    @pytest.mark.asyncio
    async def test_detects_hallucination_lowers_score(self):
        """检测到幻觉时 hallucination_flags 增加、评分下降。"""
        from app.agents.quality_agent import quality_agent

        tenders = [
            _make_tender(
                id=1, core_content="金额 100 万元", source_url="http://x",
                source_raw_text="原文：预算金额 100 万元",
            ),
        ]
        factory = _make_db_factory(tenders)
        fake_report = MagicMock()
        fake_report.passed = False
        fake_report.total_facts = 2
        fake_report.verified_facts = 0
        fake_report.hallucinated_facts = 2
        with patch("app.models.database.AsyncSessionLocal", factory), \
                patch(
                    "app.processors.hallucination_checker.check_content",
                    return_value=fake_report,
                ):
            state = await quality_agent({
                "collect_summary": {
                    "total": 5,
                    "duplicates": 0,
                    "platforms_collected": ["ccgp"],
                },
                "process_summary": {},
                "subscription_id": 1,
            })

        qs = state["quality_summary"]
        assert qs["hallucination_flags"] == 1
        assert qs["total_checked"] == 1
        assert qs["hallucination_pass_rate"] == 0.0
        # dedup_rate=1.0, hallucination_pass_rate=0.0 → 0.5
        assert qs["quality_score"] == 0.5

    @pytest.mark.asyncio
    async def test_passes_when_no_hallucination(self):
        """反幻觉校验通过时不计幻觉标记。"""
        from app.agents.quality_agent import quality_agent

        tenders = [
            _make_tender(
                id=1, core_content="正常内容", source_url="http://x",
                source_raw_text="原文：正常内容",
            ),
        ]
        factory = _make_db_factory(tenders)
        fake_report = MagicMock()
        fake_report.passed = True
        fake_report.total_facts = 1
        fake_report.verified_facts = 1
        fake_report.hallucinated_facts = 0
        with patch("app.models.database.AsyncSessionLocal", factory), \
                patch(
                    "app.processors.hallucination_checker.check_content",
                    return_value=fake_report,
                ):
            state = await quality_agent({
                "collect_summary": {
                    "total": 4,
                    "duplicates": 1,
                    "platforms_collected": ["ccgp"],
                },
                "process_summary": {},
                "subscription_id": 1,
            })

        qs = state["quality_summary"]
        assert qs["hallucination_flags"] == 0
        # duplicates=1, total=4 → dedup_rate = 1 - 1/4 = 0.75
        assert qs["dedup_rate"] == 0.75
        assert qs["hallucination_pass_rate"] == 1.0
        assert qs["quality_score"] == round((0.75 + 1.0) / 2, 3)

    @pytest.mark.asyncio
    async def test_skips_tenders_missing_content_or_url(self):
        """core_content 或 source_url 缺失的 tender 跳过反幻觉校验。"""
        from app.agents.quality_agent import quality_agent

        tenders = [
            _make_tender(id=1, core_content=None, source_url="http://x"),
            _make_tender(id=2, core_content="content", source_url=None),
            _make_tender(id=3, core_content="", source_url=""),
        ]
        factory = _make_db_factory(tenders)
        with patch("app.models.database.AsyncSessionLocal", factory), \
                patch(
                    "app.processors.hallucination_checker.check_content",
                ) as mock_check:
            state = await quality_agent({
                "collect_summary": {
                    "total": 3,
                    "duplicates": 0,
                    "platforms_collected": ["ccgp"],
                },
                "process_summary": {},
                "subscription_id": 1,
            })

        mock_check.assert_not_called()
        qs = state["quality_summary"]
        assert qs["hallucination_flags"] == 0
        assert qs["total_checked"] == 3

    @pytest.mark.asyncio
    async def test_missing_collect_summary_defaults(self):
        """collect_summary 缺失时用默认值计算。"""
        from app.agents.quality_agent import quality_agent

        factory = _make_db_factory([])
        with patch("app.models.database.AsyncSessionLocal", factory):
            state = await quality_agent({
                "process_summary": {},
                "subscription_id": 1,
            })

        qs = state["quality_summary"]
        # collect_summary={} → total=0, duplicates=0, platforms_collected=["ccgp"]
        assert qs["total_checked"] == 0
        assert qs["duplicates_removed"] == 0


# ==== pipeline 异步执行 + 辅助函数测试 ====

class TestPipelineAsyncExecution:
    """pipeline.run_pipeline 异步执行路径测试。"""

    @pytest.mark.asyncio
    async def test_run_pipeline_marks_done_on_success(self):
        """成功完成时 session 标记 done。"""
        from app.agents import pipeline

        async def mock_agent(state):
            state["collect_summary"] = {"total": 1}
            return state

        graph = AgentGraph()
        graph.add_agent("intent", "mock", mock_agent, is_entry=True)

        with patch.object(pipeline, "_build_six_agent_graph", return_value=graph):
            session_id = await pipeline.run_pipeline({"query": "测试"})
            # 须在 patch 上下文内等待，否则后台任务使用真实 graph
            session = await self._wait_for_stage(session_id, {"done"})
        assert session["stage"] == "done"
        assert session["progress"] == 100
        assert session["finished_at"] is not None
        assert session["error"] is None
        assert session["result"] is not None
        assert "collect_summary" in session["result"]

    @pytest.mark.asyncio
    async def test_run_pipeline_marks_error_on_failure(self):
        """graph.run 抛异常时 session 标记 error。"""
        from app.agents import pipeline

        graph = MagicMock()
        graph.run = AsyncMock(side_effect=RuntimeError("pipeline boom"))

        with patch.object(pipeline, "_build_six_agent_graph", return_value=graph):
            session_id = await pipeline.run_pipeline({"query": "测试"})
            session = await self._wait_for_stage(session_id, {"error"})
        assert session["stage"] == "error"
        assert "pipeline boom" in session["error"]
        assert session["finished_at"] is not None
        assert session["message"] == "Pipeline 失败: pipeline boom"

    @staticmethod
    async def _wait_for_stage(session_id, target_stages, timeout_s=2.0):
        """轮询 get_session 直到 stage 进入目标状态。"""
        from app.agents import pipeline

        elapsed = 0.0
        step = 0.02
        while elapsed < timeout_s:
            session = await pipeline.get_session(session_id)
            if session is None:
                raise RuntimeError("session disappeared")
            if session["stage"] in target_stages:
                return session
            await asyncio.sleep(step)
            elapsed += step
        raise AssertionError(
            f"pipeline did not reach {target_stages} within {timeout_s}s "
            f"(last stage={session and session['stage']})"
        )


class TestPipelineHelpers:
    """pipeline 内部辅助函数测试。"""

    @pytest.mark.asyncio
    async def test_update_stage_unknown_session_is_noop(self):
        """_update_stage 对未知 session 静默返回。"""
        from app.agents.pipeline import _update_stage, get_session

        await _update_stage("nonexistent-id", "intent", "running")
        assert await get_session("nonexistent-id") is None

    @pytest.mark.asyncio
    async def test_update_stage_completed_sets_finished_at(self):
        """_update_stage status=completed 设置 finished_at。"""
        from app.agents import pipeline

        async def mock_agent(state):
            return state

        graph = AgentGraph()
        graph.add_agent("intent", "mock", mock_agent, is_entry=True)
        with patch.object(pipeline, "_build_six_agent_graph", return_value=graph):
            session_id = await pipeline.run_pipeline({"query": "测试"})
            await asyncio.sleep(0.1)

        await pipeline._update_stage(session_id, "intent", "completed")
        session = await pipeline.get_session(session_id)
        assert session["stages"]["intent"]["status"] == "completed"
        assert session["stages"]["intent"]["finished_at"] is not None

    @pytest.mark.asyncio
    async def test_update_stage_failed_sets_finished_at(self):
        """_update_stage status=failed 设置 finished_at。"""
        from app.agents import pipeline

        async def mock_agent(state):
            return state

        graph = AgentGraph()
        graph.add_agent("intent", "mock", mock_agent, is_entry=True)
        with patch.object(pipeline, "_build_six_agent_graph", return_value=graph):
            session_id = await pipeline.run_pipeline({"query": "测试"})
            await asyncio.sleep(0.1)

        await pipeline._update_stage(session_id, "quality", "failed")
        session = await pipeline.get_session(session_id)
        assert session["stages"]["quality"]["status"] == "failed"
        assert session["stages"]["quality"]["finished_at"] is not None

    @pytest.mark.asyncio
    async def test_mark_done_unknown_session_is_noop(self):
        """_mark_done 对未知 session 静默返回。"""
        from app.agents.pipeline import _mark_done, get_session

        await _mark_done("nonexistent-id", {"collect_summary": {}})
        assert await get_session("nonexistent-id") is None

    @pytest.mark.asyncio
    async def test_mark_error_unknown_session_is_noop(self):
        """_mark_error 对未知 session 静默返回。"""
        from app.agents.pipeline import _mark_error, get_session

        await _mark_error("nonexistent-id", "some error")
        assert await get_session("nonexistent-id") is None


class TestSafeDict:
    """_safe_dict 各类型分支测试。"""

    def test_none_returns_empty_dict(self):
        from app.agents.pipeline import _safe_dict

        assert _safe_dict(None) == {}

    def test_dict_passthrough(self):
        from app.agents.pipeline import _safe_dict

        assert _safe_dict({"a": 1, "b": 2}) == {"a": 1, "b": 2}

    def test_pydantic_model_uses_model_dump(self):
        from app.agents.pipeline import _safe_dict
        from app.llm.schemas import ParsedFilters

        obj = ParsedFilters(raw_query="上海充电桩", topic="充电桩")
        d = _safe_dict(obj)
        assert d["raw_query"] == "上海充电桩"
        assert d["topic"] == "充电桩"

    def test_other_object_falls_back_to_repr(self):
        from app.agents.pipeline import _safe_dict

        class _Plain:
            pass

        obj = _Plain()
        d = _safe_dict(obj)
        assert "__repr__" in d
        assert repr(obj) in d["__repr__"]


class TestRunMultiAgentWorkflow:
    """run_multi_agent_workflow 同步入口测试。"""

    @pytest.mark.asyncio
    async def test_returns_final_state_with_defaults(self):
        """默认 platforms=['ccgp']、user_id=1，返回最终 state。"""
        from app.agents import pipeline

        async def mock_agent(state):
            state["done"] = True
            return state

        graph = AgentGraph()
        graph.add_agent("intent", "mock", mock_agent, is_entry=True)

        with patch.object(pipeline, "_build_six_agent_graph", return_value=graph):
            result = await pipeline.run_multi_agent_workflow("测试查询")

        assert result["query"] == "测试查询"
        assert result["user_id"] == 1
        assert result["platforms"] == ["ccgp"]
        assert result.get("done") is True

    @pytest.mark.asyncio
    async def test_custom_platforms_and_user_id(self):
        """自定义 platforms 和 user_id 透传到 state。"""
        from app.agents import pipeline

        async def mock_agent(state):
            return state

        graph = AgentGraph()
        graph.add_agent("intent", "mock", mock_agent, is_entry=True)

        with patch.object(pipeline, "_build_six_agent_graph", return_value=graph):
            result = await pipeline.run_multi_agent_workflow(
                "q", user_id=42, platforms=["ggzy"],
            )

        assert result["user_id"] == 42
        assert result["platforms"] == ["ggzy"]


    @pytest.mark.asyncio
    async def test_no_raw_text_counts_unverified(self):
        """P2-8：无 source_raw_text 时不调 check_content，不计入核验分母。"""
        from app.agents.quality_agent import quality_agent

        tenders = [
            _make_tender(id=1, core_content="内容", source_url="http://x"),
        ]
        factory = _make_db_factory(tenders)
        with patch("app.models.database.AsyncSessionLocal", factory), \
                patch(
                    "app.processors.hallucination_checker.check_content",
                ) as mock_check:
            state = await quality_agent({
                "collect_summary": {
                    "total": 1,
                    "duplicates": 0,
                    "platforms_collected": ["ccgp"],
                },
                "process_summary": {},
                "subscription_id": 1,
            })

        mock_check.assert_not_called()
        qs = state["quality_summary"]
        assert qs["total_checked"] == 1
        assert qs["total_verified"] == 0
        assert qs["hallucination_flags"] == 0
