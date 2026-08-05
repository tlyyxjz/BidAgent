"""Word 报告生成器（主逻辑）。

命题交付物硬要求：
- 文件命名：{用户问题}_{YYYYMMDDHHmm}.docx
- 必含 5 字段：标题/发布时间/来源链接/核心内容/附件链接
- 核心内容必须与原文事实一致（反幻觉）

金融分析章节对齐 observation_signals.py 6 维度口径（中标活跃度/公开中标集中度/废标公告关联/明确投标否决/信息冲突观察/高频共现提示）。

章节渲染逻辑（封面/摘要/金融分析）拆分至 app.report.docx_sections。
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime
from typing import Any

from docx import Document

from app.config import settings
from app.llm.schemas import ParsedFilters
from app.report.docx_components import (
    add_analysis,
    add_anti_hallucination_section,
    add_detail_table,
    add_footer,
)
from app.report.docx_sections import (  # noqa: F401  re-export 保持向后兼容
    _add_cover,
    _add_finance_section,
    _add_summary,
    _set_default_font,
)
from app.utils.logger import get_logger

logger = get_logger("docx_generator")

# Windows 文件名非法字符
_ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def _sanitize_filename(query: str) -> str:
    """清理用户查询作为文件名（去除非法字符 + 截断）。"""
    # 去除非法字符
    cleaned = _ILLEGAL_CHARS.sub("", query).strip()
    # 去除首尾空格和点
    cleaned = cleaned.strip(". ")
    # 截断到 80 字符（避免文件名过长）
    if len(cleaned) > 80:
        cleaned = cleaned[:80]
    # 空字符串兜底
    return cleaned or "query"


def build_filename(query: str, dt: datetime | None = None) -> str:
    """构建命题要求的文件名：{用户问题}_{YYYYMMDDHHmm}.docx。

    示例：最近3个月的上海区域内的充电桩招标信息都有哪些_202604071424.docx
    """
    if dt is None:
        dt = datetime.now()
    safe_query = _sanitize_filename(query)
    time_str = dt.strftime("%Y%m%d%H%M")
    return f"{safe_query}_{time_str}.docx"


async def generate_report(
    filters: ParsedFilters,
    items: list[dict[str, Any]],
    job_id: str | None = None,
    source_texts: dict[str, str] | None = None,
    finance_summary: dict[str, Any] | None = None,
) -> str:
    """生成 Word 报告，返回文件路径。

    C-5 修复：同步 CPU + IO 密集操作移到线程池，避免阻塞事件循环。
    C-2 修复：新增 source_texts 参数，反幻觉校验真正生效。

    Args:
        filters: LLM 解析出的过滤条件
        items: 招标信息列表（dict 字段对齐 Tender 表）
        job_id: 可选的任务 ID（用于日志）
        source_texts: 可选，{source_url: 原文} 映射，反幻觉校验用
        finance_summary: 可选，金融分析结果（来自 finance_agent），生成金融分析章节

    Returns:
        生成的 Word 文件绝对路径
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, _generate_report_sync, filters, items, job_id, source_texts, finance_summary
    )


def _generate_report_sync(
    filters: ParsedFilters,
    items: list[dict[str, Any]],
    job_id: str | None = None,
    source_texts: dict[str, str] | None = None,
    finance_summary: dict[str, Any] | None = None,
) -> str:
    """Word 报告生成同步实现（在线程池中运行）。"""
    # 确保输出目录存在
    os.makedirs(settings.REPORT_OUTPUT_DIR, exist_ok=True)

    # 命题硬要求：文件命名 {用户问题}_{YYYYMMDDHHmm}.docx
    filename = build_filename(filters.raw_query)
    filepath = os.path.abspath(os.path.join(settings.REPORT_OUTPUT_DIR, filename))

    doc = Document()
    _set_default_font(doc)

    # 1. 封面
    _add_cover(doc, filters, len(items))

    # 2. 报告摘要
    _add_summary(doc, filters, items)

    # 3. 项目明细表（命题 5 字段）
    add_detail_table(doc, items)

    # 4. 分析建议
    add_analysis(doc, items)

    # 5. 金融分析（v4.1 6 维公开活动观察信号）
    _add_finance_section(doc, finance_summary)

    # 6. 反幻觉校验报告（命题硬要求：core_content 与原文事实一致）
    # C-2 修复：传入 source_texts 让校验真正生效
    add_anti_hallucination_section(doc, items, source_texts=source_texts)

    # 7. 页脚
    add_footer(doc)

    doc.save(filepath)
    logger.info("report generated job_id={} path={}", job_id, filepath)
    return filepath
