"""邮件模板渲染（从 email_sender 拆分）：收件人校验、邮件构建。"""

from __future__ import annotations

import mimetypes
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, parseaddr
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.email_sender import SMTPConfig


def _sanitize_header(value: str, field_name: str) -> str:
    """拒绝 CR/LF 邮件头注入。"""
    value = str(value).strip()
    if "\r" in value or "\n" in value:
        raise ValueError(f"{field_name} 包含非法换行符")
    return value


def _normalize_recipients(to_addrs: list[str]) -> list[str]:
    """校验、规范化并去重收件人。"""
    result: list[str] = []
    seen: set[str] = set()

    for raw in to_addrs:
        candidate = _sanitize_header(raw, "收件人")
        if not candidate:
            continue

        display_name, address = parseaddr(candidate)
        _ = display_name

        # parseaddr 对部分垃圾输入比较宽松，因此要求地址具备基本结构。
        if (
            not address
            or "@" not in address
            or address.startswith("@")
            or address.endswith("@")
            or " " in address
        ):
            raise ValueError(f"无效收件地址: {candidate}")

        key = address.casefold()
        if key not in seen:
            seen.add(key)
            result.append(address)

    return result


def build_message(
    config: "SMTPConfig",
    from_name: str,
    recipients: list[str],
    subject: str,
    body: str,
    attachment_path: Path,
    message_id: str,
) -> MIMEMultipart:
    """构建带附件的 MIME 邮件。"""
    message = MIMEMultipart("mixed")
    message["Subject"] = subject
    message["From"] = formataddr(
        (from_name, config.from_addr)
    )
    message["To"] = ", ".join(recipients)
    message["Message-ID"] = message_id
    message.attach(MIMEText(body, "plain", "utf-8"))

    content_type, _ = mimetypes.guess_type(
        attachment_path.name
    )
    subtype = "octet-stream"
    if content_type and "/" in content_type:
        subtype = content_type.split("/", 1)[1]

    attachment = MIMEApplication(
        attachment_path.read_bytes(),
        _subtype=subtype,
    )
    attachment.add_header(
        "Content-Disposition",
        "attachment",
        filename=("utf-8", "", attachment_path.name),
    )
    message.attach(attachment)
    return message
