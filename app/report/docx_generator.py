"""Word 报告生成器（主逻辑）。

命题交付物硬要求：
- 文件命名：{用户问题}_{YYYYMMDDHHmm}.docx
- 必含 5 字段：标题/发布时间/来源链接/核心内容/附件链接
- 核心内容必须与原文事实一致（反幻觉）
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from app.config import settings
from app.llm.schemas import ParsedFilters
from app.report.docx_components import (
    add_analysis,
    add_anti_hallucination_section,
    add_detail_table,
    add_footer,
)
from app.report.utils import set_run_font as _set_run_font
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


def _set_default_font(doc: Document) -> None:
    """设置默认中文字体（宋体）。"""
    style = doc.styles["Normal"]
    style.font.name = "宋体"
    style.font.size = Pt(11)
    style._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")


def _add_cover(doc: Document, filters: ParsedFilters, total: int) -> None:
    """封面页。"""
    # 空行
    for _ in range(6):
        doc.add_paragraph()

    # 主标题
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("招投标信息聚合报告")
    _set_run_font(run, "黑体")
    run.font.size = Pt(28)
    run.font.bold = True
    run.font.color.rgb = RGBColor(0x1F, 0x49, 0x7D)

    doc.add_paragraph()

    # 副标题
    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = subtitle.add_run("ScrapeFlow · AI 驱动的招投标信息聚合工具")
    run.font.size = Pt(14)
    run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)

    for _ in range(4):
        doc.add_paragraph()

    # 查询条件
    info = doc.add_paragraph()
    info.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = info.add_run(f"查询条件：{filters.raw_query}")
    run.font.size = Pt(12)

    info2 = doc.add_paragraph()
    info2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = info2.add_run(
        f"主题：{filters.topic or '不限'}  |  "
        f"地区：{filters.region or '不限'}  |  "
        f"时间范围：{filters.time_range or filters.date_range or '30d'}"
    )
    run.font.size = Pt(11)
    run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)

    if filters.frequency:
        info3 = doc.add_paragraph()
        info3.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = info3.add_run(f"推送频率：{filters.frequency}")
        run.font.size = Pt(11)
        run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)

    info4 = doc.add_paragraph()
    info4.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = info4.add_run(f"采集结果：共 {total} 条")
    run.font.size = Pt(11)
    run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)

    for _ in range(4):
        doc.add_paragraph()

    gen_time = doc.add_paragraph()
    gen_time.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = gen_time.add_run(f"生成时间：{datetime.now().strftime('%Y年%m月%d日 %H:%M')}")
    run.font.size = Pt(11)

    doc.add_page_break()


def _add_summary(doc: Document, filters: ParsedFilters, items: list[dict[str, Any]]) -> None:
    """报告摘要。"""
    h = doc.add_heading("一、报告摘要", level=1)
    for run in h.runs:
        _set_run_font(run, "黑体")

    total = len(items)
    total_budget = sum(float(i.get("budget_amount") or 0) for i in items)
    avg_budget = total_budget / total if total else 0
    platforms = list({str(i.get("source_platform") or "unknown") for i in items})

    p = doc.add_paragraph()
    p.add_run(f"本报告基于查询条件「{filters.raw_query}」，").font.size = Pt(11)
    p.add_run(f"从 {len(platforms)} 个平台共采集到 {total} 条招投标信息。").font.size = Pt(11)

    p = doc.add_paragraph()
    p.add_run("涉及预算总金额约 ").font.size = Pt(11)
    run = p.add_run(f"{total_budget / 10000:.2f} 万元")
    run.font.bold = True
    run.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)
    p.add_run(f"，平均单项目预算 {avg_budget / 10000:.2f} 万元。").font.size = Pt(11)

    p = doc.add_paragraph()
    p.add_run("数据来源平台：").font.bold = True
    p.add_run("、".join(platforms))

    doc.add_paragraph()


async def generate_report(
    filters: ParsedFilters,
    items: list[dict[str, Any]],
    job_id: str | None = None,
    source_texts: dict[str, str] | None = None,
) -> str:
    """生成 Word 报告，返回文件路径。

    C-5 修复：同步 CPU + IO 密集操作移到线程池，避免阻塞事件循环。
    C-2 修复：新增 source_texts 参数，反幻觉校验真正生效。

    Args:
        filters: LLM 解析出的过滤条件
        items: 招标信息列表（dict 字段对齐 Tender 表）
        job_id: 可选的任务 ID（用于日志）
        source_texts: 可选，{source_url: 原文} 映射，反幻觉校验用

    Returns:
        生成的 Word 文件绝对路径
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, _generate_report_sync, filters, items, job_id, source_texts
    )


def _generate_report_sync(
    filters: ParsedFilters,
    items: list[dict[str, Any]],
    job_id: str | None = None,
    source_texts: dict[str, str] | None = None,
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

    # 5. 反幻觉校验报告（命题硬要求：core_content 与原文事实一致）
    # C-2 修复：传入 source_texts 让校验真正生效
    add_anti_hallucination_section(doc, items, source_texts=source_texts)

    # 6. 页脚
    add_footer(doc)

    doc.save(filepath)
    logger.info("report generated job_id={} path={}", job_id, filepath)
    return filepath
