"""Word 报告公共工具函数（m-1 修复：消除跨文件重复定义）。"""

from __future__ import annotations

from docx.oxml.ns import qn


def set_run_font(run, font_name: str = "宋体") -> None:
    """设置 run 的中文字体（同时设西文 + 东亚字体）。

    python-docx 默认只设西文字体，中文需要额外设 w:eastAsia 属性。
    """
    run.font.name = font_name
    run._element.rPr.rFonts.set(qn("w:eastAsia"), font_name)
