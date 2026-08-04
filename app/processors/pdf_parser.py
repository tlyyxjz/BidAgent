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
"""
# pragma: no cover — PDF 解析需要真实 PDF 文件

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

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


# 关键字段正则（复用 hallucination_checker 的模式）
_FIELD_PATTERNS: dict[str, re.Pattern[str]] = {
    "project_name": re.compile(
        r"(?:项目名称|项目名|工程名称|采购项目名)\s*[:：]\s*([^\n\r，。；]{2,100})"
    ),
    "bid_number": re.compile(
        r"(?:招标编号|项目编号|采购编号|标段编号)\s*[:：]\s*([A-Z0-9\-/]{6,40})"
    ),
    "budget_amount": re.compile(
        r"(?:预算金额|项目预算|采购预算|控制价)\s*[:：]\s*"
        r"(\d+(?:\.\d+)?\s*(?:万元|亿元|元|万|亿))"
    ),
    "deadline": re.compile(
        r"(?:投标截止时间|报名截止时间|截止时间|投标截止日期)\s*[:：]\s*"
        r"(\d{4}年\d{1,2}月\d{1,2}日(?:\s*\d{1,2}:\d{1,2})?)"
    ),
    "tender_org": re.compile(
        r"(?:招标人|采购人|招标单位|采购单位)\s*[:：]\s*([^\n\r，。；]{2,50})"
    ),
    "agency": re.compile(
        r"(?:代理机构|招标代理|采购代理)\s*[:：]\s*([^\n\r，。；]{2,50})"
    ),
    "contact_name": re.compile(
        r"(?:联系人|项目联系人)\s*[:：]\s*([^\n\r，。；]{2,30})"
    ),
    "contact_phone": re.compile(
        r"(?:联系电话|电话|联系方式)\s*[:：]\s*(\d{3,4}-?\d{7,8})"
    ),
}


def _parse_decimal(value: str | None) -> Decimal | None:
    """金额字符串转 Decimal。

    支持：
    - "500000" / "500,000" - 纯数字
    - "50万元" - 50 万 = 500000
    - "1.5亿元" - 1.5 亿 = 150000000
    - "50万" / "1.5亿" - 单位简写
    """
    if not value:
        return None
    cleaned = value.replace(",", "").strip()
    # 优先处理"亿元"（先于"万"）
    if "亿元" in cleaned:
        num_str = cleaned.replace("亿元", "").replace("亿", "").replace("元", "").strip()
        try:
            return Decimal(num_str) * Decimal("100000000")
        except (InvalidOperation, ValueError):
            return None
    if "万元" in cleaned:
        num_str = cleaned.replace("万元", "").replace("万", "").replace("元", "").strip()
        try:
            return Decimal(num_str) * Decimal("10000")
        except (InvalidOperation, ValueError):
            return None
    if "亿" in cleaned:
        num_str = cleaned.replace("亿", "").replace("元", "").strip()
        try:
            return Decimal(num_str) * Decimal("100000000")
        except (InvalidOperation, ValueError):
            return None
    if "万" in cleaned:
        num_str = cleaned.replace("万", "").replace("元", "").strip()
        try:
            return Decimal(num_str) * Decimal("10000")
        except (InvalidOperation, ValueError):
            return None
    cleaned = cleaned.replace("元", "").strip()
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def _parse_datetime(value: str | None) -> datetime | None:
    """日期字符串转 datetime。"""
    if not value:
        return None
    s = value.strip()
    for fmt in ("%Y年%m月%d日 %H:%M", "%Y年%m月%d日", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _extract_fields(text: str) -> dict[str, Any]:
    """从全文文本提取关键字段。"""
    fields: dict[str, Any] = {}
    for field_name, pattern in _FIELD_PATTERNS.items():
        match = pattern.search(text)
        if not match:
            continue
        raw_value = match.group(1).strip()
        if field_name == "budget_amount":
            dec = _parse_decimal(raw_value)
            if dec is not None:
                fields[field_name] = float(dec)
                fields["budget_raw"] = raw_value
        elif field_name == "deadline":
            dt = _parse_datetime(raw_value)
            if dt is not None:
                fields[field_name] = dt.isoformat()
        else:
            fields[field_name] = raw_value
    return fields


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
