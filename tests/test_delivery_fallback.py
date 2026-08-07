"""delivery_agent Bug 17 修复测试：无 unpushed 时 fallback 到过滤查询。

覆盖场景：
- unpushed 为空 + fallback 查询有结果 → 生成报告，不推送
- unpushed 为空 + fallback 查询也无结果 → 不生成报告
- unpushed 有数据 → 正常路径，不走 fallback
- fallback 查询使用与 get_unpushed_tenders 相同的过滤条件
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.delivery_agent import delivery_agent, _query_tenders_with_filters
from app.llm.schemas import ParsedFilters


def _make_tender(
    project_name: str = "测试项目",
    source_url: str = "http://example.com/1",
    core_content: str = "核心内容测试",
    budget_amount=None,
    publish_time=None,
    location: str = "上海",
    notice_type: str = "tender",
):
    """构造 mock Tender 对象。"""
    t = MagicMock()
    t.project_name = project_name
    t.source_url = source_url
    t.core_content = core_content
    t.attachment_url = ""
    t.budget_amount = budget_amount
    t.publish_time = publish_time
    t.deadline = None
    t.tender_org = "测试甲方"
    t.source_platform = "ccgp"
    t.location = location
    t.notice_type = notice_type
    return t


def _make_state(parsed: ParsedFilters = None, sub_id: int = 7) -> dict:
    """构造 delivery_agent 输入 state。"""
    if parsed is None:
        parsed = ParsedFilters(raw_query="医疗设备", topic="医疗设备")
    return {
        "parsed_filters": parsed,
        "subscription_id": sub_id,
        "finance_summary": {"observation_signals": {}},
    }


class TestDeliveryFallback:
    """Bug 17：无 unpushed 时 fallback 到过滤查询。"""

    @pytest.mark.asyncio
    async def test_fallback_generates_report_when_unpushed_empty(self) -> None:
        """unpushed 为空 + fallback 有结果 → 生成报告，不推送。"""
        fallback_tenders = [_make_tender(project_name="医疗设备A")]
        state = _make_state()

        with patch(
            "app.scheduler.subscription.get_unpushed_tenders",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.agents.delivery_agent._query_tenders_with_filters",
            new_callable=AsyncMock,
            return_value=fallback_tenders,
        ), patch(
            "app.report.docx_generator.generate_report",
            new_callable=AsyncMock,
            return_value="/tmp/test_report.docx",
        ) as mock_gen, patch(
            "app.agents.delivery_agent._trigger_push",
            new_callable=AsyncMock,
            return_value={"delivered": False},
        ) as mock_push:
            result = await delivery_agent(state)

        # 应生成报告
        assert result["report_path"] == "/tmp/test_report.docx"
        assert result["delivery_summary"]["report_generated"] is True
        assert result["delivery_summary"]["fallback_query"] is True
        # fallback 场景不应触发推送
        mock_push.assert_not_called()
        # generate_report 应被调用，且传入 fallback 数据
        mock_gen.assert_called_once()
        call_items = mock_gen.call_args[0][1]
        assert len(call_items) == 1
        assert call_items[0]["project_name"] == "医疗设备A"
        assert call_items[0]["core_content"] == "核心内容测试"

    @pytest.mark.asyncio
    async def test_no_report_when_both_unpushed_and_fallback_empty(self) -> None:
        """unpushed 为空 + fallback 也为空 → 不生成报告。"""
        state = _make_state()

        with patch(
            "app.scheduler.subscription.get_unpushed_tenders",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.agents.delivery_agent._query_tenders_with_filters",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.report.docx_generator.generate_report",
            new_callable=AsyncMock,
        ) as mock_gen:
            result = await delivery_agent(state)

        # 不应生成报告
        assert result["report_path"] is None
        assert result["delivery_summary"]["report_generated"] is False
        assert result["delivery_summary"]["reason"] == "no tenders matched"
        mock_gen.assert_not_called()

    @pytest.mark.asyncio
    async def test_normal_path_when_unpushed_has_data(self) -> None:
        """unpushed 有数据 → 走正常路径，不调用 fallback 查询。"""
        unpushed_tenders = [_make_tender(project_name="新项目")]
        state = _make_state()

        with patch(
            "app.scheduler.subscription.get_unpushed_tenders",
            new_callable=AsyncMock,
            return_value=unpushed_tenders,
        ), patch(
            "app.agents.delivery_agent._query_tenders_with_filters",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_fallback, patch(
            "app.report.docx_generator.generate_report",
            new_callable=AsyncMock,
            return_value="/tmp/normal_report.docx",
        ), patch(
            "app.agents.delivery_agent._trigger_push",
            new_callable=AsyncMock,
            return_value={
                "delivered": True,
                "email_sent": True,
                "webhook_sent": False,
                "message_id": "msg-123",
            },
        ):
            result = await delivery_agent(state)

        # 应生成报告
        assert result["report_path"] == "/tmp/normal_report.docx"
        assert result["delivery_summary"]["report_generated"] is True
        # 不应标记为 fallback
        assert result["delivery_summary"].get("fallback_query") is not True
        # fallback 查询不应被调用
        mock_fallback.assert_not_called()
        # 应触发推送
        assert result["delivery_summary"]["email_sent"] is True
        assert result["delivery_summary"]["delivered"] is True


class TestQueryTendersWithFilters:
    """_query_tenders_with_filters 辅助函数测试。"""

    @pytest.mark.asyncio
    async def test_topic_filter_applied(self) -> None:
        """topic 过滤条件正确应用到查询。"""
        from sqlalchemy import select

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [_make_tender()]
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        filters = ParsedFilters(raw_query="医疗设备", topic="医疗设备")
        tenders = await _query_tenders_with_filters(mock_db, filters)

        # 应调用 db.execute
        mock_db.execute.assert_called_once()
        # 返回的 tenders 列表
        assert len(tenders) == 1

    @pytest.mark.asyncio
    async def test_region_filter_applied(self) -> None:
        """region 过滤条件正确应用到查询。"""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        filters = ParsedFilters(raw_query="上海充电桩", topic="充电桩", region="上海")
        tenders = await _query_tenders_with_filters(mock_db, filters)

        mock_db.execute.assert_called_once()
        assert tenders == []

    @pytest.mark.asyncio
    async def test_no_filters_returns_all(self) -> None:
        """无过滤条件 → 返回所有数据（最多 100 条）。"""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [_make_tender(), _make_tender()]
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        filters = ParsedFilters(raw_query="测试")
        tenders = await _query_tenders_with_filters(mock_db, filters)

        assert len(tenders) == 2
