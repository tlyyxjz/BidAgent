"""PDF 附件解析器。

命题第 4 项硬要求增强：附件链接不只是下载，还要解析内容。
- 用 pdfplumber 提取文本和表格
- 从文本中提取关键字段（项目名称/预算/截止时间/招标人等）
- 同步库通过 run_in_executor 包装为 async
- 解析失败不阻塞主流程，返回 None

工程规范：
- 单文件 ≤ 300 行
- 不依赖外部 OCR（扫描件 PDF 暂不处理）
- 字段提取正则复用 hallucination_checker

字段提取（正则 + 金额/日期归一化）拆分至 app.processors.pdf_fields。
"""
# pragma: no cover — PDF 解析需要真实 PDF 文件

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.processors.pdf_fields import (  # noqa: F401  re-export 保持向后兼容
    _extract_fields,
    _parse_datetime,
    _parse_decimal,
)
from app.utils.logger import get_logger

logger = get_logger("pdf_parser")

# 文件大小上限（10MB），避免解析超大 PDF 拖慢系统
_MAX_FILE_SIZE = 10 * 1024 * 1024


@dataclass
class ParsedPdf:
    """PDF 解析结果。"""

    text: str = ""                       # 全文文本
    tables: list[list[list[str]]] = field(default_factory=list)  # 表格数据
    page_count: int = 0                  # 页数
    fields: dict[str, Any] = field(default_factory=dict)  # 提取的关键字段
    parse_error: str | None = None       # 解析错误（None 表示成功）

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text[:5000],  # 截断防止过大
            "tables": self.tables[:5],  # 只保留前 5 个表格
            "page_count": self.page_count,
            "fields": self.fields,
            "parse_error": self.parse_error,
        }


def _parse_pdf_sync(file_path: str) -> ParsedPdf:
    """同步解析 PDF（在线程池中运行）。"""
    if not os.path.exists(file_path):
        return ParsedPdf(parse_error=f"file not found: {file_path}")

    file_size = os.path.getsize(file_path)
    if file_size > _MAX_FILE_SIZE:
        return ParsedPdf(
            parse_error=f"file too large: {file_size} bytes > {_MAX_FILE_SIZE}"
        )

    try:
        import pdfplumber  # type: ignore[import-not-found]
    except ImportError:
        return ParsedPdf(parse_error="pdfplumber not installed")

    try:
        all_text: list[str] = []
        all_tables: list[list[list[str]]] = []
        page_count = 0

        with pdfplumber.open(file_path) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages:
                text = page.extract_text() or ""
                all_text.append(text)
                tables = page.extract_tables() or []
                for table in tables:
                    if table:
                        all_tables.append(table)

        full_text = "\n".join(all_text)
        if not full_text.strip():
            # 文本提取为空，可能是扫描件 PDF
            return ParsedPdf(
                page_count=page_count,
                parse_error="empty text (scanned PDF requires OCR)",
            )

        fields = _extract_fields(full_text)
        return ParsedPdf(
            text=full_text,
            tables=all_tables,
            page_count=page_count,
            fields=fields,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("pdf parse failed path=%s", file_path)
        return ParsedPdf(parse_error=str(exc))


async def parse_pdf(file_path: str) -> dict[str, Any]:
    """异步解析 PDF（在线程池中运行同步 pdfplumber）。

    Args:
        file_path: PDF 文件绝对路径

    Returns:
        {
            "text": "...",           # 全文文本（截断 5000 字符）
            "tables": [[...], ...],  # 表格数据（前 5 个）
            "page_count": N,         # 页数
            "fields": {...},         # 提取的关键字段
            "parse_error": null|str  # 错误信息
        }
    """
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _parse_pdf_sync, file_path)
    return result.to_dict()


async def enrich_tender_from_pdf(
    tender_id: int,
    pdf_path: str,
) -> dict[str, Any]:
    """把 PDF 解析出的字段补充到 Tender.core_content。

    Returns:
        {"updated": bool, "fields_updated": [...], "error": str|None}
    """
    from app.models.database import AsyncSessionLocal
    from app.models.tender import Tender
    from sqlalchemy import select

    parse_result = await parse_pdf(pdf_path)
    if parse_result.get("parse_error"):
        return {
            "updated": False,
            "fields_updated": [],
            "error": parse_result["parse_error"],
        }

    fields = parse_result.get("fields") or {}
    if not fields:
        return {
            "updated": False,
            "fields_updated": [],
            "error": "no fields extracted from pdf",
        }

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Tender).where(Tender.id == tender_id))
        tender = result.scalar_one_or_none()
        if tender is None:
            return {
                "updated": False,
                "fields_updated": [],
                "error": f"tender {tender_id} not found",
            }

        updated: list[str] = []
        # 把 PDF 提取的字段补充到 core_content（不覆盖原值）
        pdf_summary = "\n\n[PDF 附件解析补充]\n"
        for k, v in fields.items():
            pdf_summary += f"{k}: {v}\n"
            updated.append(k)

        tender.core_content = (tender.core_content or "") + pdf_summary
        await db.commit()

    logger.info(
        "tender enriched from pdf tender_id={} fields={}",
        tender_id, updated,
    )
    return {
        "updated": True,
        "fields_updated": updated,
        "error": None,
    }


def is_pdf_file(file_path: str) -> bool:
    """判断文件是否为 PDF（扩展名 + magic number）。"""
    if not file_path.lower().endswith(".pdf"):
        return False
    try:
        with open(file_path, "rb") as f:
            return f.read(5) == b"%PDF-"
    except (OSError, IOError):
        return False
