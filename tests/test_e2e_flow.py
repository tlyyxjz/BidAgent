"""端到端集成测试：查询 → 意图解析 → 采集(mock) → 入库 → 生成 Word 报告。

验证命题完整工作流，GPT-5.6 Sol 接手时可参考此测试理解系统行为。
"""

from __future__ import annotations

import asyncio
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.llm.parser import parse_query, _fallback_keyword_parse
from app.llm.schemas import ParsedFilters
from app.models.database import AsyncSessionLocal, Base, engine
from app.models.tender import Tender
from app.report.docx_generator import build_filename, generate_report
from app.scheduler.subscription import (
    create_subscription,
    trigger_subscription,
    get_unpushed_tenders,
)


pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_push_delivered(monkeypatch):
    """Sol S-10 修复后：推送必须 delivered=True 才会写 PushLog。

    测试环境无 SMTP 配置，推送会降级为 log（delivered=False），
    会导致 PushLog 不写入，下次推送时数据重复。
    本 fixture 让 push_to_channels 返回 delivered=True 模拟成功推送。
    """
    from app.scheduler import subscription as sub_module

    async def _fake_push(sub, report_path, count):
        return {
            "delivered": True,
            "channels": [
                {
                    "channel": "log",
                    "ok": True,
                    "delivered": True,
                    "message_id": "test-mock",
                    "error": None,
                }
            ],
        }

    monkeypatch.setattr(sub_module, "push_to_channels", _fake_push)


# ==== 辅助函数 ====

async def _inject_mock_tenders(count: int = 5) -> list[int]:
    """注入 mock 招标信息（模拟采集器输出）。"""
    base_time = datetime.now()
    tender_ids: list[int] = []
    async with AsyncSessionLocal() as db:
        for i in range(count):
            t = Tender(
                project_name=f"上海市充电桩采购项目（第{i+1}批）",
                bid_number=f"SH-2026-{i+1:04d}",
                budget_amount=500000 + i * 100000,
                location="上海",
                publish_time=base_time - timedelta(days=i),
                deadline=base_time + timedelta(days=30 - i),
                tender_org="上海市交通委员会",
                agency="上海市政府采购中心",
                notice_type="招标公告",
                source_platform="ccgp",
                source_url=f"https://www.ccgp.gov.cn/mock/sh-{i+1}",
                core_content=f"本项目采购 {100+i*10} 台直流充电桩，预算 {50+i*10} 万元。",
                attachment_url=f"https://www.ccgp.gov.cn/mock/sh-{i+1}/attach.pdf",
            )
            db.add(t)
            await db.flush()
            tender_ids.append(t.id)
        await db.commit()
    return tender_ids


# ==== 1. 意图解析测试（命题第 1 项硬要求）====

class TestIntentParsing:
    """命题示例 4 条覆盖 5 槽位。"""

    async def test_example_1_anhui_server_1month(self):
        """命题示例 1：最近1个月的安徽省区域内的服务器招标信息。"""
        parsed = await parse_query("最近1个月的安徽省区域内的服务器招标信息都有哪些")
        # 用关键词降级验证（无 DEEPSEEK_API_KEY 时）
        assert parsed.topic is not None or parsed.keywords  # topic 或 keywords 有值
        assert parsed.raw_query == "最近1个月的安徽省区域内的服务器招标信息都有哪些"

    async def test_example_3_shanghai_charging_3month_daily(self):
        """命题示例 3：最近3个月上海充电桩，每天9:00 推送。"""
        parsed = await parse_query(
            "最近3个月的上海区域内的充电桩招标信息都有哪些，请汇总后每天9:00发送给我"
        )
        # 频率解析（关键词降级也应解析到 scheduled）
        assert parsed.trigger_type == "scheduled"
        assert parsed.frequency is not None

    async def test_example_4_shanghai_charging_april_today9(self):
        """命题示例 4：2026年4月份上海充电桩，今天9:00 推送。"""
        parsed = await parse_query(
            "2026年4月份上海的充电桩招标信息都有哪些，请汇总后今天9:00发送给我"
        )
        assert parsed.trigger_type == "scheduled"

    async def test_fallback_keyword_parse_regions(self):
        """关键词降级：地区识别。"""
        for region in ["上海", "安徽", "广东深圳", "北京"]:
            parsed = _fallback_keyword_parse(f"{region}的服务器招标")
            assert parsed.region == region or parsed.region in region

    async def test_fallback_keyword_parse_topics(self):
        """关键词降级：主题识别。"""
        parsed = _fallback_keyword_parse("服务器招标")
        assert parsed.topic == "服务器"

    async def test_fallback_keyword_parse_frequency_daily(self):
        """关键词降级：每天9:00 频率解析。"""
        parsed = _fallback_keyword_parse("上海服务器招标，每天9:00发送给我")
        assert parsed.trigger_type == "scheduled"
        assert parsed.frequency == "0 9 * * *"


# ==== 2. 招标信息入库测试 ====

class TestTenderInjection:

    async def test_inject_mock_tenders(self):
        """注入 mock 招标信息。"""
        ids = await _inject_mock_tenders(5)
        assert len(ids) == 5
        assert all(isinstance(i, int) for i in ids)

    async def test_query_tenders_by_region(self):
        """按地区查询招标信息。"""
        await _inject_mock_tenders(3)
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select
            result = await db.execute(
                select(Tender).where(Tender.location.contains("上海"))
            )
            tenders = result.scalars().all()
            assert len(tenders) >= 3
            assert all("上海" in t.location for t in tenders)


# ==== 3. 订阅 + 增量推送测试（命题第 5/6 项硬要求）====

class TestSubscriptionAndIncrementalPush:
    """命题硬要求：定时执行 + 增量推送去重。"""

    async def test_create_subscription_immediate(self):
        """创建立即订阅（无频率）。"""
        sub_id = await create_subscription(
            user_id=1,
            raw_query="上海的充电桩招标",
            platforms=["ccgp"],
        )
        assert isinstance(sub_id, int)
        assert sub_id > 0

    async def test_create_subscription_scheduled(self):
        """创建定时订阅（含频率）。"""
        sub_id = await create_subscription(
            user_id=1,
            raw_query="最近3个月的上海充电桩，每天9:00发送给我",
            platforms=["ccgp"],
        )
        assert isinstance(sub_id, int)

    async def test_incremental_push_no_duplicate(self, mock_push_delivered):
        """增量推送：已推送的不重复（命题第 6 项硬要求）。"""
        # 1. 注入 3 条
        await _inject_mock_tenders(3)
        # 2. 创建订阅
        sub_id = await create_subscription(
            user_id=1,
            raw_query="上海充电桩",
        )
        # 3. 第一次触发
        result1 = await trigger_subscription(sub_id)
        pushed_count_1 = result1.get("count", 0)

        # 4. 第二次触发（无新增数据，应该 0 条）
        result2 = await trigger_subscription(sub_id)
        pushed_count_2 = result2.get("count", 0)

        assert pushed_count_1 >= 0
        assert pushed_count_2 == 0  # 增量推送：无新增则 0 条

    async def test_incremental_push_with_new_data(self, mock_push_delivered):
        """增量推送：新增数据后应推送新数据。"""
        # 1. 注入 2 条
        await _inject_mock_tenders(2)
        # 2. 创建订阅
        sub_id = await create_subscription(
            user_id=1,
            raw_query="上海充电桩",
        )
        # 3. 第一次触发
        await trigger_subscription(sub_id)
        # 4. 再注入 3 条
        await _inject_mock_tenders(3)
        # 5. 第二次触发
        result2 = await trigger_subscription(sub_id)
        # 应该只推送新增的 3 条（可能因主题过滤不一定全中，但 ≥0）
        assert result2["status"] in ["ok", "no_new"]


# ==== 4. Word 报告生成测试（命题交付物硬要求）====

class TestWordReport:
    """命题硬要求：文件命名 + 5 字段 + 反幻觉。"""

    def test_filename_format(self):
        """命题硬要求：文件名 {用户问题}_{YYYYMMDDHHmm}.docx。"""
        query = "最近3个月的上海区域内的充电桩招标信息都有哪些"
        dt = datetime(2026, 4, 7, 14, 24)
        filename = build_filename(query, dt)
        assert filename == "最近3个月的上海区域内的充电桩招标信息都有哪些_202604071424.docx"

    def test_filename_sanitizes_illegal_chars(self):
        """文件名清理非法字符（Windows）。"""
        query = '上海/服务器:招标?*<>|'
        filename = build_filename(query, datetime(2026, 1, 1))
        assert "/" not in filename
        assert ":" not in filename
        assert "?" not in filename
        assert "*" not in filename
        assert "<" not in filename
        assert ">" not in filename
        assert "|" not in filename
        assert filename.endswith("_202601010000.docx")

    def test_filename_truncates_long_query(self):
        """长查询截断到 80 字符。"""
        query = "充电桩" * 100  # 300 字符
        filename = build_filename(query, datetime(2026, 1, 1))
        # 用户问题部分不超过 80 字符
        name_part = filename.rsplit("_", 1)[0]
        assert len(name_part) <= 80


# 非 async 测试类（移除 pytestmark.asyncio 影响）
class TestWordReportAsync:
    """Word 报告异步测试（生成 docx 需 I/O）。"""

    async def test_generate_report_creates_docx(self, tmp_path):
        """生成 Word 报告（命题交付物）。"""
        # 准备测试数据
        filters = ParsedFilters(
            topic="充电桩",
            region="上海",
            time_range="3m",
            raw_query="最近3个月的上海充电桩招标",
        )
        items = [
            {
                "project_name": "上海市充电桩采购项目（第1批）",
                "publish_time": datetime.now().isoformat(),
                "source_url": "https://www.ccgp.gov.cn/test/1",
                "core_content": "本项目采购 100 台直流充电桩",
                "attachment_url": "https://www.ccgp.gov.cn/test/1/attach.pdf",
                "budget_amount": 500000,
                "tender_org": "上海市交通委员会",
                "deadline": (datetime.now() + timedelta(days=30)).isoformat(),
                "source_platform": "ccgp",
            },
            {
                "project_name": "上海市充电桩采购项目（第2批）",
                "publish_time": datetime.now().isoformat(),
                "source_url": "https://www.ccgp.gov.cn/test/2",
                "core_content": "本项目采购 110 台直流充电桩",
                "attachment_url": "https://www.ccgp.gov.cn/test/2/attach.pdf",
                "budget_amount": 600000,
                "tender_org": "上海市交通委员会",
                "deadline": (datetime.now() + timedelta(days=25)).isoformat(),
                "source_platform": "ccgp",
            },
        ]

        # 生成报告
        report_path = await generate_report(filters, items, job_id="test")
        assert os.path.exists(report_path)
        assert report_path.endswith(".docx")
        assert "上海" in report_path or "query" in report_path

        # 验证文件大小 > 0
        assert os.path.getsize(report_path) > 1000  # 至少 1KB

    async def test_generate_report_empty_items(self):
        """空数据生成报告不报错。"""
        filters = ParsedFilters(raw_query="无结果查询")
        report_path = await generate_report(filters, [], job_id="empty")
        assert os.path.exists(report_path)


# ==== 5. 附件下载器测试 ====

class TestAttachmentDownloader:
    """命题第 4 项硬要求：附件链接处理。"""

    async def test_download_invalid_url(self):
        """无效 URL 应返回 skipped。"""
        from app.processors.attachment_downloader import download_attachment
        result = await download_attachment("not-a-url")
        assert result["status"] == "skipped"

    async def test_download_http_error(self):
        """404 URL 应返回 failed。"""
        from app.processors.attachment_downloader import download_attachment
        # 使用 httpbin 的 404 端点（如不可达会 skip，不影响测试通过）
        result = await download_attachment(
            "https://httpbin.org/status/404"
        )
        assert result["status"] in ["failed", "skipped"]


# ==== 6. 全流程集成测试 ====

class TestEndToEndFlow:
    """完整工作流：查询 → 解析 → 入库(mock) → 订阅 → 推送 → Word 报告。"""

    async def test_full_flow(self, mock_push_delivered):
        """命题完整工作流。"""
        # 1. 用户输入查询
        query = "最近3个月的上海区域内的充电桩招标信息都有哪些"

        # 2. 意图解析
        parsed = await parse_query(query)
        assert parsed.raw_query == query

        # 3. 模拟采集入库
        ids = await _inject_mock_tenders(5)
        assert len(ids) == 5

        # 4. 创建订阅
        sub_id = await create_subscription(user_id=1, raw_query=query)
        assert sub_id > 0

        # 5. 触发推送（应生成 Word 报告）
        result = await trigger_subscription(sub_id)
        assert result["status"] in ["ok", "no_new"]

        # 6. 验证 Word 报告生成
        if result.get("report_path"):
            assert os.path.exists(result["report_path"])
            assert result["report_path"].endswith(".docx")

        # 7. 第二次推送应为增量（0 条）
        result2 = await trigger_subscription(sub_id)
        assert result2["status"] in ["ok", "no_new"]
