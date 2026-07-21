"""附件下载器（命题第 4 项硬要求：附件链接）。

支持格式：PDF / DOC / DOCX / XLS / XLSX
存储路径：data/attachments/{tender_id}/{filename}

安全设计：
- 防路径遍历：basename 提取 + 最终路径校验
- 防 SSRF：拒绝内网/回环/链路本地地址（M-7 抽出到 app.utils.url_safety 复用）
- 异步 IO：aiofiles 避免阻塞事件循环
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiofiles
import httpx

from app.config import settings
from app.utils.logger import get_logger
from app.utils.url_safety import is_safe_url as _is_safe_url

logger = get_logger("attachment_downloader")

# 允许的附件扩展名
ALLOWED_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".txt", ".zip", ".rar", ".7z",
}

# 最大文件大小（50 MB）
MAX_FILE_SIZE = 50 * 1024 * 1024


def _sanitize_filename(name: str) -> str:
    """清理文件名（去除非法字符 + 防路径遍历）。

    安全要点：
    1. 去除 Windows 非法字符
    2. 移除路径穿越符 ..
    3. 使用 os.path.basename 确保只是文件名
    4. 截断到 100 字符
    """
    # 去除 Windows 非法字符
    cleaned = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
    # 移除路径穿越符
    cleaned = cleaned.replace("..", "")
    # 取 basename 确保只是文件名（防止 /etc/passwd 形式）
    cleaned = os.path.basename(cleaned)
    # 截断到 100 字符
    return cleaned[:100] if len(cleaned) > 100 else cleaned


def _extract_filename(url: str, content_disposition: str | None = None) -> str:
    """从 URL 或 Content-Disposition 提取文件名。"""
    if content_disposition:
        # 解析 attachment; filename="xxx.pdf"
        if "filename=" in content_disposition:
            name_part = content_disposition.split("filename=")[-1].strip('" ')
            if name_part:
                return _sanitize_filename(name_part)

    # 从 URL 提取
    parsed = urlparse(url)
    name = os.path.basename(parsed.path) or "attachment"
    return _sanitize_filename(name)


def _is_allowed_extension(filename: str) -> bool:
    """检查扩展名是否允许。"""
    ext = Path(filename).suffix.lower()
    return ext in ALLOWED_EXTENSIONS


def _is_path_safe(target_path: Path) -> tuple[bool, str]:
    """校验最终路径是否在允许目录内（防路径遍历）。"""
    try:
        attachment_root = Path(settings.ATTACHMENT_DIR).resolve()
        final_path = target_path.resolve()
        # 必须在 ATTACHMENT_DIR 之内
        if not str(final_path).startswith(str(attachment_root)):
            return False, f"path traversal detected: {final_path} not under {attachment_root}"
        return True, ""
    except Exception as exc:
        return False, f"path check error: {exc}"


async def download_attachment(
    url: str,
    tender_id: int | None = None,
    timeout: int = 30,
    parse_pdf_content: bool = False,
) -> dict[str, Any]:
    """下载单个附件。

    Args:
        url: 附件 URL
        tender_id: 关联的招标信息 ID（用于目录隔离）
        timeout: 下载超时秒数
        parse_pdf_content: 是否解析 PDF 内容（命题第 4 项硬要求增强）

    Returns:
        下载结果 {
            "status": "ok" | "skipped" | "failed",
            "url": 原始 URL,
            "local_path": 本地路径（成功时）,
            "filename": 文件名,
            "size_bytes": 文件大小,
            "reason": 失败原因（失败时）,
            "pdf_parsed": bool（PDF 是否解析成功）,
            "pdf_fields": dict（PDF 提取的字段）,
        }
    """
    if not url or not url.startswith(("http://", "https://")):
        return {"status": "skipped", "url": url, "reason": "invalid url"}

    # C-2 SSRF 防护
    is_safe, reason = _is_safe_url(url)
    if not is_safe:
        logger.warning("SSRF blocked url={} reason={}", url[:80], reason)
        return {"status": "skipped", "url": url, "reason": f"ssrf blocked: {reason}"}

    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True
        ) as client:
            # 先 HEAD 请求检查
            content_disposition: str | None = None
            try:
                head_resp = await client.head(url)
                content_disposition = head_resp.headers.get("content-disposition")
                content_length = head_resp.headers.get("content-length")
                if content_length and int(content_length) > MAX_FILE_SIZE:
                    return {
                        "status": "skipped",
                        "url": url,
                        "reason": f"file too large: {content_length}",
                    }
            except Exception:
                pass

            # 实际下载
            resp = await client.get(url)
            resp.raise_for_status()

            filename = _extract_filename(url, content_disposition)
            if not _is_allowed_extension(filename):
                logger.info("skip unsupported extension: %s", filename)
                return {
                    "status": "skipped",
                    "url": url,
                    "reason": f"unsupported extension: {filename}",
                }

            # 构建存储路径
            subdir = str(tender_id) if tender_id else "misc"
            target_dir = Path(settings.ATTACHMENT_DIR) / subdir
            target_dir.mkdir(parents=True, exist_ok=True)
            target_path = target_dir / filename

            # C-1 路径遍历防护：最终路径校验
            is_safe, reason = _is_path_safe(target_path)
            if not is_safe:
                logger.error("path traversal blocked: {}", reason)
                return {"status": "failed", "url": url, "reason": reason}

            content = resp.content
            if len(content) > MAX_FILE_SIZE:
                return {
                    "status": "skipped",
                    "url": url,
                    "reason": "file too large",
                }

            # M-8 异步文件写入
            async with aiofiles.open(target_path, "wb") as f:
                await f.write(content)

            logger.info(
                "attachment downloaded tender_id={} url={} → {} ({} bytes)",
                tender_id, url[:80], target_path, len(content),
            )

            # PDF 内容解析（命题硬要求增强）
            result: dict[str, Any] = {
                "status": "ok",
                "url": url,
                "local_path": str(target_path.absolute()),
                "filename": filename,
                "size_bytes": len(content),
            }
            if parse_pdf_content and filename.lower().endswith(".pdf"):
                try:
                    from app.processors.pdf_parser import parse_pdf
                    pdf_result = await parse_pdf(str(target_path.absolute()))
                    result["pdf_parsed"] = pdf_result.get("parse_error") is None
                    result["pdf_fields"] = pdf_result.get("fields") or {}
                    if pdf_result.get("parse_error"):
                        result["pdf_error"] = pdf_result["parse_error"]
                except Exception as exc:  # noqa: BLE001
                    logger.warning("pdf parse failed url={} err={}", url[:80], exc)
                    result["pdf_parsed"] = False
                    result["pdf_error"] = str(exc)
            return result

    except httpx.HTTPError as exc:
        logger.warning("download failed url={} err={}", url[:80], exc)
        return {"status": "failed", "url": url, "reason": str(exc)}
    except Exception as exc:
        logger.exception("download unknown error url={}", url[:80])
        return {"status": "failed", "url": url, "reason": str(exc)}


async def download_attachments_batch(
    urls: list[str], tender_id: int | None = None
) -> list[dict[str, Any]]:
    """批量下载附件。

    Args:
        urls: 附件 URL 列表
        tender_id: 关联的招标信息 ID

    Returns:
        每个附件的下载结果列表
    """
    results = []
    for url in urls:
        result = await download_attachment(url, tender_id)
        results.append(result)
    return results
