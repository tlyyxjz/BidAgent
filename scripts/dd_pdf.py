# -*- coding: utf-8 -*-
"""尽调报告 PDF 导出（reportlab + 内置中文字体 STSong-Light）。"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))


def render_pdf(data: dict, out_path: str | Path) -> Path:
    """data = build_report_data 的输出。生成 PDF 尽调报告。"""
    s = data["stats"]
    out = Path(out_path)
    doc = SimpleDocTemplate(str(out), pagesize=A4,
                            leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=15 * mm, bottomMargin=15 * mm)
    title_style = ParagraphStyle("t", fontName="STSong-Light", fontSize=16,
                                 leading=22, spaceAfter=6)
    body_style = ParagraphStyle("b", fontName="STSong-Light", fontSize=9.5,
                                leading=15, spaceAfter=3)
    story = [
        Paragraph(f'尽调数据包 · {data["company"]}', title_style),
        Paragraph(f'生成时间 {data["generated_at"]} · 数据以官方公告原文为准', body_style),
        Spacer(1, 5 * mm),
    ]

    if data["empty"]:
        story.append(Paragraph("未找到该企业的中标记录，如实报告，不猜测补全。", body_style))
    else:
        story.append(Paragraph(
            f"中标 {s['total_wins']} 次 · 总金额 {s['total_amount']:,.0f} 元 · "
            f"Top1 客户占比 {s.get('top1_share_pct') or '-'}%", body_style))
        story.append(Spacer(1, 3 * mm))

        rows = [["项目", "金额(元)", "采购人", "日期"]]
        for w in data.get("wins") or []:
            rows.append([
                w.get("project_name") or "", f"{_to_yuan(w.get('amount')) or 0:,.0f}",
                w.get("purchaser") or "-", str(w.get("publish_time") or "")[:10],
            ])
        table = Table(rows, colWidths=[60 * mm, 25 * mm, 38 * mm, 22 * mm])
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "STSong-Light"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e2761")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(table)

        if data.get("risks"):
            story.append(Spacer(1, 4 * mm))
            story.append(Paragraph("客观信号（只列事实，不下结论）：", body_style))
            for r in data["risks"]:
                story.append(Paragraph(
                    f"· {r['signal']}：{r['fact']}", body_style))

    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph(
        "本报告仅整理官方公告库中已有信息，不构成投资建议。每条数字可回溯到公告原文字符位置。",
        body_style))
    doc.build(story)
    return out


def _to_yuan(v):
    from verify_award import _to_yuan as _f
    return _f(v)
