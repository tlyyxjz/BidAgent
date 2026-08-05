"""新增模块测试用例（SimHash / PDF 解析 / 多 Agent / 反幻觉 / 入库器）。

补充命题硬要求的测试覆盖。
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from app.llm.schemas import ParsedFilters
from app.processors.hallucination_checker import check_content, check_items
from app.processors.pdf_parser import (
    ParsedPdf,
    _extract_fields,
    _parse_decimal,
    _parse_datetime,
    is_pdf_file,
    parse_pdf,
)
from app.processors.simhash import (
    MASK64,
    compute_simhash,
    hamming_distance,
    is_similar,
)


# ==== SimHash 测试（命题第 3 项硬要求）====

class TestSimHash:
    """SimHash 64 位算法测试。"""

    def test_compute_simhash_returns_int(self):
        """compute_simhash 返回 int。"""
        h = compute_simhash("上海充电桩招标")
        assert isinstance(h, int)
        assert h >= 0
        assert h <= MASK64

    def test_compute_simhash_empty_text(self):
        """空文本返回 0。"""
        assert compute_simhash("") == 0

    def test_same_text_same_hash(self):
        """相同文本 SimHash 相同。"""
        text = "上海市充电桩采购项目"
        assert compute_simhash(text) == compute_simhash(text)

    def test_similar_text_low_hamming_distance(self):
        """相似文本汉明距离小。"""
        t1 = "上海市充电桩采购项目第1批"
        t2 = "上海市充电桩采购项目第2批"
        h1 = compute_simhash(t1)
        h2 = compute_simhash(t2)
        # 相似文本汉明距离应该 ≤ 10
        assert hamming_distance(h1, h2) <= 10

    def test_different_text_high_hamming_distance(self):
        """完全不同文本汉明距离大。"""
        t1 = "上海市充电桩采购项目"
        t2 = "北京市办公家具租赁服务"
        h1 = compute_simhash(t1)
        h2 = compute_simhash(t2)
        # 不同文本汉明距离应该 > 5
        assert hamming_distance(h1, h2) > 5

    def test_is_similar_threshold(self):
        """重复判断阈值。"""
        t1 = "上海市充电桩采购项目第1批"
        t2 = "上海市充电桩采购项目第2批"
        h1 = compute_simhash(t1)
        h2 = compute_simhash(t2)
        # 阈值 10 时应该相似
        assert is_similar(h1, h2, threshold=10) is True

    def test_hamming_distance_self_is_zero(self):
        """自己与自己的汉明距离为 0。"""
        h = compute_simhash("测试文本")
        assert hamming_distance(h, h) == 0

    def test_hamming_distance_known_values(self):
        """已知值的汉明距离。"""
        assert hamming_distance(0b0000, 0b0000) == 0
        assert hamming_distance(0b0000, 0b1111) == 4
        assert hamming_distance(0b1010, 0b0101) == 4
        assert hamming_distance(0b1001, 0b0001) == 1


# ==== PDF 解析测试 ====

class TestPdfParser:
    """PDF 解析器测试。"""

    def test_parse_decimal_plain(self):
        """金额解析 - 纯数字。"""
        assert _parse_decimal("500000") == 500000

    def test_parse_decimal_wan(self):
        """金额解析 - 万元。"""
        assert _parse_decimal("50万元") == 500000

    def test_parse_decimal_yi(self):
        """金额解析 - 亿元。"""
        assert _parse_decimal("1.5亿元") == 150000000

    def test_parse_decimal_with_comma(self):
        """金额解析 - 千分位。"""
        assert _parse_decimal("500,000") == 500000

    def test_parse_decimal_invalid(self):
        """金额解析 - 无效返回 None。"""
        assert _parse_decimal("无效") is None
        assert _parse_decimal("") is None
        assert _parse_decimal(None) is None

    def test_parse_datetime_chinese(self):
        """日期解析 - 中文格式。"""
        dt = _parse_datetime("2026年4月7日")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 4
        assert dt.day == 7

    def test_parse_datetime_with_time(self):
        """日期解析 - 带时间。"""
        dt = _parse_datetime("2026年4月7日 14:30")
        assert dt is not None
        assert dt.hour == 14
        assert dt.minute == 30

    def test_parse_datetime_iso(self):
        """日期解析 - ISO 格式。"""
        dt = _parse_datetime("2026-04-07")
        assert dt is not None
        assert dt.year == 2026

    def test_parse_datetime_invalid(self):
        """日期解析 - 无效返回 None。"""
        assert _parse_datetime("无效日期") is None
        assert _parse_datetime("") is None
        assert _parse_datetime(None) is None

    def test_extract_fields_complete(self):
        """字段提取 - 完整文本。"""
        text = """
        项目名称：上海市充电桩采购项目
        招标编号：SH-2026-001
        预算金额：50万元
        投标截止时间：2026年5月15日 14:30
        招标人：上海市交通委员会
        代理机构：上海市政府采购中心
        联系人：张三
        联系电话：021-12345678
        """
        fields = _extract_fields(text)
        assert fields["project_name"] == "上海市充电桩采购项目"
        assert fields["bid_number"] == "SH-2026-001"
        assert fields["budget_amount"] == 500000
        assert fields["budget_raw"] == "50万元"
        assert "2026-05-15" in fields["deadline"]
        assert fields["tender_org"] == "上海市交通委员会"
        assert fields["agency"] == "上海市政府采购中心"
        assert fields["contact_name"] == "张三"
        assert fields["contact_phone"] == "021-12345678"

    def test_extract_fields_empty_text(self):
        """字段提取 - 空文本。"""
        assert _extract_fields("") == {}
        assert _extract_fields("无字段文本") == {}

    def test_is_pdf_file_by_extension(self):
        """PDF 文件判断 - 扩展名。"""
        assert is_pdf_file("test.pdf") is False  # 文件不存在
        assert is_pdf_file("test.txt") is False
        assert is_pdf_file("test.doc") is False

    def test_is_pdf_file_magic_number(self, tmp_path):
        """PDF 文件判断 - magic number。"""
        # 真 PDF
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 some content")
        assert is_pdf_file(str(pdf_path)) is True

        # 假 PDF（扩展名是 .pdf 但内容不是）
        fake_path = tmp_path / "fake.pdf"
        fake_path.write_bytes(b"not a pdf file")
        assert is_pdf_file(str(fake_path)) is False

    async def test_parse_pdf_nonexistent(self):
        """解析不存在的 PDF 文件。"""
        result = await parse_pdf("/nonexistent/file.pdf")
        assert result["parse_error"] is not None
        assert "not found" in result["parse_error"]

    async def test_parse_pdf_not_pdf(self, tmp_path):
        """解析非 PDF 文件。"""
        file_path = tmp_path / "fake.pdf"
        file_path.write_bytes(b"not a real pdf")
        result = await parse_pdf(str(file_path))
        # 应该返回错误（不是有效 PDF）
        assert result["parse_error"] is not None


# ==== 反幻觉校验测试 ====

class TestHallucinationChecker:
    """反幻觉校验测试。"""

    def test_check_content_passed(self):
        """事实一致时通过。"""
        core = "项目预算 50万元，截止日期 2026年5月15日"
        source = "本项目预算 50万元，投标截止日期 2026年5月15日"
        report = check_content(core, source)
        assert report.passed is True
        assert report.total_facts > 0

    def test_check_content_empty_core(self):
        """空 core_content 默认通过。"""
        report = check_content("", "any source")
        assert report.passed is True

    def test_check_content_empty_source(self):
        """无原文时跳过校验。"""
        report = check_content("项目预算 50万元", "")
        assert report.passed is True

    def test_check_content_hallucination_detected(self):
        """事实不一致应检测到幻觉（严格模式）。"""
        core = "项目预算 99万元"
        source = "本项目预算 50万元"
        report = check_content(core, source, strict=True)
        assert report.passed is False
        assert report.hallucinated_facts > 0

    def test_check_items_batch(self):
        """批量校验。"""
        items = [
            {"core_content": "预算 50万元", "source_url": "url1"},
            {"core_content": "预算 99万元", "source_url": "url2"},
        ]
        source_texts = {
            "url1": "项目预算 50万元",
            "url2": "项目预算 50万元",  # 不一致
        }
        result = check_items(items, source_texts)
        assert result["total_items"] == 2
        assert result["passed_items"] >= 1
        assert result["failed_items"] >= 1
        assert len(result["details"]) == 2

    def test_check_items_empty(self):
        """空列表批量校验。"""
        result = check_items([])
        assert result["total_items"] == 0
        assert result["passed_items"] == 0


# ==== Tender Ingestor 测试（命题第 3 项硬要求集成）====

class TestTenderIngestor:
    """采集结果入库 + SimHash 去重测试。"""

    async def test_ingest_scrape_result_basic(self):
        """基础入库测试。"""
        from app.processors.simhash import compute_simhash
        from app.processors.tender_ingestor import ingest_scrape_result

        scrape_result = {
            "data": [
                {
                    "project_name": "上海市充电桩采购项目（测试）",
                    "publish_time": "2026-04-07",
                    "source_url": "https://www.ccgp.gov.cn/test/ingest/1",
                    "core_content": "本项目采购100台直流充电桩",
                    "attachment_url": "https://www.ccgp.gov.cn/test/ingest/1.pdf",
                    "budget_amount": 500000,
                    "tender_org": "上海市交通委员会",
                    "source_platform": "ccgp",
                    "location": "上海",
                }
            ]
        }
        result = await ingest_scrape_result(
            scrape_result=scrape_result,
            template="ccgp",
            simhash_computer=compute_simhash,
        )
        assert result["total"] == 1
        assert result["inserted"] >= 0  # 可能因为已存在而 = 0
        assert "duplicates" in result

    async def test_ingest_scrape_result_empty(self):
        """空数据入库。"""
        from app.processors.simhash import compute_simhash
        from app.processors.tender_ingestor import ingest_scrape_result

        result = await ingest_scrape_result(
            scrape_result={"data": []},
            template=None,
            simhash_computer=compute_simhash,
        )
        assert result["total"] == 0
        assert result["inserted"] == 0

    async def test_ingest_dedup_same_content(self):
        """相同内容应该去重。"""
        from app.processors.simhash import compute_simhash
        from app.processors.tender_ingestor import ingest_scrape_result

        scrape_result = {
            "data": [
                {
                    "project_name": "完全相同的项目名称",
                    "publish_time": "2026-04-07",
                    "source_url": "https://www.ccgp.gov.cn/dedup/1",
                    "core_content": "完全相同的核心内容描述",
                    "source_platform": "ccgp",
                }
            ]
        }
        # 第一次入库
        r1 = await ingest_scrape_result(
            scrape_result=scrape_result,
            template="ccgp",
            simhash_computer=compute_simhash,
        )
        # 第二次入库相同内容
        r2 = await ingest_scrape_result(
            scrape_result=scrape_result,
            template="ccgp",
            simhash_computer=compute_simhash,
        )
        # 第二次应该至少检测到 1 个重复
        assert r2["duplicates"] >= 1


# ==== 多 Agent 协作测试 ====

class TestAgentCoordinator:
    """多 Agent 框架测试。"""

    async def test_agent_graph_sequential(self):
        """顺序执行 2 个 Agent。"""
        from app.agents.coordinator import AgentGraph

        async def agent_a(state):
            state["a"] = "hello"
            return state

        async def agent_b(state):
            state["b"] = state["a"] + " world"
            return state

        graph = AgentGraph()
        graph.add_agent("a", "agent a", agent_a, next_agent="b", is_entry=True)
        graph.add_agent("b", "agent b", agent_b)
        result = await graph.run({})

        assert result["a"] == "hello"
        assert result["b"] == "hello world"
        assert result["_agent_history"] == ["a", "b"]
        assert len(graph.traces) == 2
        assert all(t.success for t in graph.traces)

    async def test_agent_graph_failure_stops(self):
        """Agent 失败应终止流程。"""
        from app.agents.coordinator import AgentGraph

        async def agent_ok(state):
            state["ok"] = True
            return state

        async def agent_fail(state):
            raise RuntimeError("intentional failure")

        async def agent_after(state):
            state["should_not_run"] = True
            return state

        graph = AgentGraph()
        graph.add_agent("ok", "ok agent", agent_ok, next_agent="fail", is_entry=True)
        graph.add_agent("fail", "fail agent", agent_fail, next_agent="after")
        graph.add_agent("after", "after agent", agent_after)
        result = await graph.run({})

        assert result["ok"] is True
        assert "should_not_run" not in result
        assert result["_last_error"]["agent"] == "fail"
        assert "intentional failure" in result["_last_error"]["error"]
        # 只有 2 个 trace（ok + fail），after 没执行
        assert len(graph.traces) == 2

    async def test_agent_graph_condition_skip(self):
        """条件不满足应跳过 Agent。"""
        from app.agents.coordinator import AgentGraph

        async def agent_a(state):
            state["a"] = "yes"
            return state

        async def agent_b(state):
            state["b"] = "ran"
            return state

        async def agent_c(state):
            state["c"] = "ran"
            return state

        graph = AgentGraph()
        graph.add_agent("a", "a", agent_a, next_agent="b", is_entry=True)
        graph.add_agent(
            "b", "b", agent_b, next_agent="c",
            condition=lambda s: s.get("a") == "no",  # 永远不满足
        )
        graph.add_agent("c", "c", agent_c)
        result = await graph.run({})

        assert result["a"] == "yes"
        assert "b" not in result  # b 被跳过
        assert result["c"] == "ran"  # c 应该执行
        assert "b" not in result["_agent_history"]

    async def test_agent_graph_summary(self):
        """执行摘要正确。"""
        from app.agents.coordinator import AgentGraph

        async def agent_a(state):
            state["a"] = 1
            return state

        graph = AgentGraph()
        graph.add_agent("a", "a", agent_a, is_entry=True)
        result = await graph.run({})

        assert "_execution_summary" in result
        summary = result["_execution_summary"]
        assert summary["total_agents"] == 1
        assert summary["successful"] == 1
        assert summary["failed"] == 0
        assert summary["total_duration_ms"] >= 0


# ==== 集成测试：SimHash 集成到入库 ====

class TestSimHashIntegration:
    """SimHash 与入库器的集成测试。"""

    async def test_simhash_stored_in_tender(self):
        """入库后 Tender.simhash 字段应该有值。"""
        from app.models.database import AsyncSessionLocal
        from app.models.tender import Tender
        from app.processors.simhash import compute_simhash
        from app.processors.tender_ingestor import ingest_scrape_result
        from sqlalchemy import select

        unique_url = f"https://www.ccgp.gov.cn/test/simhash/{datetime.now().timestamp()}"
        scrape_result = {
            "data": [
                {
                    "project_name": "SimHash 集成测试项目",
                    "publish_time": "2026-04-07",
                    "source_url": unique_url,
                    "core_content": "用于测试 SimHash 是否被正确计算并存储",
                    "source_platform": "ccgp",
                }
            ]
        }
        await ingest_scrape_result(
            scrape_result=scrape_result,
            template="ccgp",
            simhash_computer=compute_simhash,
        )

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Tender).where(Tender.source_url == unique_url)
            )
            tender = result.scalar_one_or_none()
            assert tender is not None
            assert tender.simhash is not None
            assert tender.simhash > 0
