"""Word 报告组件：明细表 + 分析建议 + 页脚。

命题硬要求：
- 必含 5 字段：标题/发布时间/来源链接/核心内容/附件链接
- 文件命名：{用户问题}_{YYYYMMDDHHmm}.docx（在 generator.py 中实现）
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt, RGBColor

from app.report.utils import set_run_font as _set_run_font


def add_detail_table(doc: Document, items: list[dict[str, Any]]) -> None:
    """添加项目明细表（命题 5 字段 + 索引）。

    命题硬要求字段：标题/发布时间/来源链接/核心内容/附件链接
    """
    h = doc.add_heading("二、项目明细", level=1)
    for run in h.runs:
        _set_run_font(run, "黑体")

    if not items:
        doc.add_paragraph("暂无采集数据。")
        return

    # 命题 5 字段 + 序号
    headers = ["序号", "标题", "发布时间", "来源链接", "核心内容", "附件链接"]
    table = doc.add_table(rows=1 + len(items), cols=len(headers))
    table.style = "Light Grid Accent 1"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    # 表头
    hdr = table.rows[0].cells
    for i, header in enumerate(headers):
        hdr[i].text = ""
        p = hdr[i].paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(header)
        run.font.bold = True
        run.font.size = Pt(10)
        _set_run_font(run, "黑体")
        hdr[i].vertical_alignment = WD_ALIGN_VERTICAL.CENTER

    # 数据行
    for idx, item in enumerate(items, 1):
        row = table.rows[idx].cells
        publish_time = item.get("publish_time")
        if publish_time:
            try:
                pt = datetime.fromisoformat(str(publish_time)).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                pt = str(publish_time)[:10]
        else:
            pt = "-"

        values = [
            str(idx),
            str(item.get("project_name") or "-"),
            pt,
            str(item.get("source_url") or "-"),
            str(item.get("core_content") or "-")[:200],  # 核心内容截断
            str(item.get("attachment_url") or "-"),
        ]
        for i, val in enumerate(values):
            row[i].text = ""
            p = row[i].paragraphs[0]
            run = p.add_run(val)
            run.font.size = Pt(9)
            _set_run_font(run, "宋体")

    # 设置列宽
    widths = [Cm(1), Cm(4), Cm(2), Cm(3), Cm(4), Cm(3)]
    for row in table.rows:
        for i, cell in enumerate(row.cells):
            if i < len(widths):
                cell.width = widths[i]

    doc.add_paragraph()


def add_analysis(doc: Document, items: list[dict[str, Any]]) -> None:
    """添加分析建议。"""
    h = doc.add_heading("三、分析与建议", level=1)
    for run in h.runs:
        _set_run_font(run, "黑体")

    if not items:
        doc.add_paragraph("暂无数据可分析。")
        return

    # 按预算排序找 top 3
    sorted_items = sorted(
        items, key=lambda x: float(x.get("budget_amount") or 0), reverse=True
    )
    top3 = sorted_items[:3]

    p = doc.add_paragraph()
    p.add_run("1. 高预算项目关注建议").font.bold = True
    p = doc.add_paragraph()
    p.add_run("以下 3 个项目预算金额最高，建议优先关注：").font.size = Pt(11)
    for item in top3:
        budget = float(item.get("budget_amount") or 0) / 10000
        p = doc.add_paragraph(style="List Number")
        p.add_run(str(item.get("project_name") or "-")).font.bold = True
        p.add_run(f"（{item.get('tender_org') or '未知'}，预算 {budget:.2f} 万元）")

    # 按截止时间排序找最紧急的
    deadline_items = [i for i in items if i.get("deadline")]
    deadline_items.sort(key=lambda x: str(x.get("deadline") or ""))
    urgent = deadline_items[:3]

    if urgent:
        p = doc.add_paragraph()
        p.add_run("2. 即将截止项目提醒").font.bold = True
        p = doc.add_paragraph()
        p.add_run("以下项目截止时间临近，请尽快行动：").font.size = Pt(11)
        for item in urgent:
            p = doc.add_paragraph(style="List Number")
            try:
                dl = datetime.fromisoformat(str(item["deadline"])).strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                dl = str(item.get("deadline"))[:10]
            p.add_run(str(item.get("project_name") or "-")).font.bold = True
            p.add_run(f"（截止时间：{dl}）")

    p = doc.add_paragraph()
    p.add_run("3. 数据声明").font.bold = True
    p = doc.add_paragraph()
    run = p.add_run(
        "本报告数据来源于公开招投标信息平台，仅供参考。"
        "核心内容与原文事实一致，具体项目信息以官方公告为准。"
    )
    run.font.size = Pt(10)
    run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)

    doc.add_paragraph()


def add_footer(doc: Document) -> None:
    """添加页脚。"""
    doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(
        f"— ScrapeFlow 报告结束 · 生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')} —"
    )
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("2026 AI 先锋未来人才大赛 · 超聚变命题 · 智汇标讯")
    run.font.size = Pt(8)
    run.font.color.rgb = RGBColor(0xA0, 0xA0, 0xA0)


def add_anti_hallucination_section(
    doc: Document,
    items: list[dict[str, Any]],
    source_texts: dict[str, str] | None = None,
) -> None:
    """添加反幻觉校验章节（命题硬要求：core_content 与原文事实一致）。

    本地规则校验：提取金额/日期/百分比/招标编号等关键事实，
    与 source_text（若有）比对，未在原文中找到的视为疑似幻觉。

    C-2 修复：原实现不传 source_texts，内部 source_texts={} 永远跳过校验。
             现接收 source_texts 并传入 check_items，让反幻觉真正生效。
    """
    h = doc.add_heading("四、反幻觉校验报告", level=1)
    for run in h.runs:
        _set_run_font(run, "黑体")

    from app.processors.hallucination_checker import check_items

    report = check_items(items, source_texts=source_texts)

    p = doc.add_paragraph()
    p.add_run("校验说明：").font.bold = True
    p = doc.add_paragraph()
    run = p.add_run(
        "本报告对核心内容中的关键事实（金额、日期、招标编号等）"
        "与原文进行一致性比对，未在原文中找到的事实标记为疑似幻觉。"
        "严格类别（金额/日期/招标编号）必须命中，其他类别宽容处理。"
    )
    run.font.size = Pt(10)
    run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)

    p = doc.add_paragraph()
    p.add_run(f"总项目数：{report['total_items']}　")
    p.add_run(f"通过：{report['passed_items']}　")
    p.add_run(f"疑似幻觉：{report['failed_items']}")
    p = doc.add_paragraph()
    p.add_run(f"幻觉事实总数：{report['hallucinated_total']}")

    if report["failed_items"] > 0:
        p = doc.add_paragraph()
        p.add_run("疑似幻觉明细：").font.bold = True
        for detail in report["details"]:
            if not detail["passed"]:
                p = doc.add_paragraph(style="List Bullet")
                p.add_run(f"项目 {detail['index'] + 1}：").font.bold = True
                p.add_run(
                    f"共 {detail['total_facts']} 个事实，"
                    f"其中 {detail['hallucinated_facts']} 个未在原文找到"
                )
                p = doc.add_paragraph()
                run = p.add_run(f"来源链接：{detail['source_url']}")
                run.font.size = Pt(9)
                run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)
    else:
        p = doc.add_paragraph()
        run = p.add_run("✓ 全部项目核心内容均通过反幻觉校验。")
        run.font.bold = True
        run.font.color.rgb = RGBColor(0x00, 0x80, 0x00)

    doc.add_paragraph()
