"""Word 报告生成器（主逻辑）。

命题交付物硬要求：
- 文件命名：{用户问题}_{YYYYMMDDHHmm}.docx
- 必含 5 字段：标题/发布时间/来源链接/核心内容/附件链接
- 核心内容必须与原文事实一致（反幻觉）

金融分析章节对齐 supplier_risk.py 5 维度口径（集中度/金额/频率/地域/采购人）。
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime
from typing import Any

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
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


def _boq_risk_level(score: float) -> str:
    """BOQ 清单评分转风险等级（对齐 boq_engine.py 口径）。"""
    if score < 60:
        return "高风险"
    if score < 80:
        return "中风险"
    return "低风险"


def _boq_status_label(status: str) -> str:
    """BOQ 异常状态转中文标签。"""
    return {
        "underpriced": "低价异常（疑似漏项）",
        "overpriced": "高价异常（建议核查）",
        "normal": "正常",
    }.get(status, status)


def _risk_level_label(score: float) -> str:
    """废标风险评分转风险等级（对齐 risk_engine.py 口径）。"""
    if score >= 60:
        return "高风险"
    if score >= 30:
        return "中风险"
    return "低风险"


def _risk_item_level_label(level: str) -> str:
    """风险项等级（low/medium/high）转中文。"""
    return {
        "high": "高风险",
        "medium": "中风险",
        "low": "低风险",
    }.get(level, level or "未知")


# 供应商风险 5 维度名映射（对齐 supplier_risk.py 口径）
_SUPPLIER_DIM_LABELS = {
    "concentration": "集中度",
    "amount_anomaly": "金额",
    "frequency": "频率",
    "region": "地域",
    "purchaser": "采购人",
}
_SUPPLIER_DIM_ORDER = [
    "concentration", "amount_anomaly", "frequency", "region", "purchaser",
]


def _add_finance_section(doc: Document, finance_summary: dict[str, Any] | None) -> None:
    """添加金融分析章节（BOQ 异常 + 废标风险 + 供应商风险评分）。

    金融分析章节对齐 supplier_risk.py 5 维度口径：
    集中度 / 金额 / 频率 / 地域 / 采购人。
    """
    h = doc.add_heading("四、金融分析", level=1)
    for run in h.runs:
        _set_run_font(run, "黑体")

    if not finance_summary:
        doc.add_paragraph("本期无相关数据。")
        doc.add_paragraph()
        return

    # ===== 4.1 BOQ 报价异常检测 =====
    h2 = doc.add_heading("4.1 BOQ 报价异常检测", level=2)
    for run in h2.runs:
        _set_run_font(run, "黑体")

    boq_report = finance_summary.get("boq_report")
    if not boq_report:
        doc.add_paragraph("本期无相关数据。")
    else:
        reports = boq_report.get("reports") or []
        suspicious_count = finance_summary.get("boq_anomalies", 0)
        if reports:
            scores = [float(r.get("score", 100) or 100) for r in reports]
            avg_score = sum(scores) / len(scores) if scores else 100.0
            risk_level = _boq_risk_level(avg_score)
        else:
            avg_score = 100.0
            risk_level = "低风险"

        p = doc.add_paragraph()
        p.add_run(f"异常项总数：{suspicious_count}　")
        p.add_run(f"风险等级：{risk_level}　")
        p.add_run(f"清单评分：{avg_score:.1f}")

        all_items = boq_report.get("items") or []
        anomalies = [it for it in all_items if it.get("status") != "normal"]
        if anomalies:
            p = doc.add_paragraph()
            p.add_run("异常项明细（前 5 项）：").font.bold = True
            for item in anomalies[:5]:
                name = item.get("name", "-")
                qty = item.get("quantity", "-")
                unit = item.get("unit", "")
                price = float(item.get("unit_price") or 0)
                judgment = _boq_status_label(item.get("status", "normal"))
                p = doc.add_paragraph(style="List Bullet")
                p.add_run(f"{name}：").font.bold = True
                p.add_run(f"数量 {qty}{unit}，单价 ¥{price:,.0f}，判定：{judgment}")
        else:
            p = doc.add_paragraph()
            run = p.add_run("未检测到报价异常项。")
            run.font.color.rgb = RGBColor(0x00, 0x80, 0x00)

    doc.add_paragraph()

    # ===== 4.2 废标风险预警 =====
    h2 = doc.add_heading("4.2 废标风险预警", level=2)
    for run in h2.runs:
        _set_run_font(run, "黑体")

    risk_report = finance_summary.get("risk_report")
    if not risk_report:
        doc.add_paragraph("本期无相关数据。")
    else:
        reports = risk_report.get("reports") or []
        risk_items_count = finance_summary.get("risk_items", 0)
        if reports:
            scores = [float(r.get("risk_score", 0) or 0) for r in reports]
            avg_score = sum(scores) / len(scores) if scores else 0.0
            risk_level = _risk_level_label(avg_score)
        else:
            avg_score = 0.0
            risk_level = "低风险"

        p = doc.add_paragraph()
        p.add_run(f"风险评分：{avg_score:.1f}　")
        p.add_run(f"风险等级：{risk_level}　")
        p.add_run(f"风险条数：{risk_items_count}")

        all_items = risk_report.get("items") or []
        if all_items:
            p = doc.add_paragraph()
            p.add_run("风险项明细（前 5 条）：").font.bold = True
            for idx, item in enumerate(all_items[:5], 1):
                clause = item.get("clause", "-")
                level = _risk_item_level_label(item.get("risk_level", ""))
                law = item.get("law_ref", "") or "—"
                suggestion = item.get("suggestion", "-")
                p = doc.add_paragraph()
                run = p.add_run(f"{idx}. 规则：{clause}")
                run.font.bold = True
                p.add_run(f" ｜ 等级：{level} ｜ 法规引用：{law}")
                p = doc.add_paragraph()
                run = p.add_run(f"　　建议：{suggestion}")
                run.font.size = Pt(10)
        else:
            p = doc.add_paragraph()
            run = p.add_run("未检测到废标风险项。")
            run.font.color.rgb = RGBColor(0x00, 0x80, 0x00)

    doc.add_paragraph()

    # ===== 4.3 供应商风险评分 =====
    h2 = doc.add_heading("4.3 供应商风险评分", level=2)
    for run in h2.runs:
        _set_run_font(run, "黑体")

    supplier_scores = finance_summary.get("supplier_scores") or []
    avg_score = float(finance_summary.get("avg_supplier_score") or 0.0)

    p = doc.add_paragraph()
    p.add_run(f"平均供应商风险评分：{avg_score:.2f}")

    if not supplier_scores:
        p = doc.add_paragraph()
        run = p.add_run("本期无相关数据。")
        run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)
    else:
        headers = (
            ["供应商"]
            + [_SUPPLIER_DIM_LABELS[d] for d in _SUPPLIER_DIM_ORDER]
            + ["总分", "风险等级"]
        )
        table = doc.add_table(rows=1 + len(supplier_scores), cols=len(headers))
        table.style = "Light Grid Accent 1"
        table.alignment = WD_TABLE_ALIGNMENT.CENTER

        # 表头
        hdr = table.rows[0].cells
        for i, header in enumerate(headers):
            hdr[i].text = ""
            para = hdr[i].paragraphs[0]
            para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = para.add_run(header)
            run.font.bold = True
            run.font.size = Pt(10)
            _set_run_font(run, "黑体")
            hdr[i].vertical_alignment = WD_ALIGN_VERTICAL.CENTER

        # 数据行
        for idx, supplier in enumerate(supplier_scores, 1):
            row = table.rows[idx].cells
            name = (
                supplier.get("normalized_name")
                or supplier.get("organization_id")
                or "-"
            )
            dim_scores = {
                d.get("name"): d.get("score")
                for d in supplier.get("dimensions", [])
            }

            values = [str(name)]
            for dim_key in _SUPPLIER_DIM_ORDER:
                score = dim_scores.get(dim_key)
                values.append(f"{float(score):.1f}" if score is not None else "—")
            values.append(f"{float(supplier.get('total_score') or 0):.1f}")
            values.append(_risk_item_level_label(supplier.get("risk_level", "")))

            for i, val in enumerate(values):
                row[i].text = ""
                para = row[i].paragraphs[0]
                para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                run = para.add_run(val)
                run.font.size = Pt(9)
                _set_run_font(run, "宋体")

    doc.add_paragraph()


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

    # 5. 金融分析（BOQ 异常 + 废标风险 + 供应商风险评分）
    _add_finance_section(doc, finance_summary)

    # 6. 反幻觉校验报告（命题硬要求：core_content 与原文事实一致）
    # C-2 修复：传入 source_texts 让校验真正生效
    add_anti_hallucination_section(doc, items, source_texts=source_texts)

    # 7. 页脚
    add_footer(doc)

    doc.save(filepath)
    logger.info("report generated job_id={} path={}", job_id, filepath)
    return filepath
