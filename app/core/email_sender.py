"""异步 SMTP 邮件发送器。

支持：
- SMTP + STARTTLS
- SMTP over SSL
- 多收件人
- Word/任意二进制附件
- 首次发送 + 最多 3 次重试，退避 1/2/4 秒
- 同步 smtplib 通过 asyncio.to_thread 执行

按职责拆分：模板渲染移到 app.core.email_template，本模块保留 SMTP 连接与发送逻辑。
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.utils import make_msgid
from pathlib import Path
from typing import Any

from app.config import settings
from app.core.email_template import (
    _normalize_recipients,
    _sanitize_header,
    build_message,
)
from app.utils.logger import get_logger

logger = get_logger("email_sender")

MAX_RETRIES = 3
RETRY_DELAYS = (1, 2, 4)


@dataclass(frozen=True)
class SMTPConfig:
    host: str
    port: int
    user: str
    password: str
    use_tls: bool
    from_addr: str
    from_name: str
    timeout: int


class EmailSender:
    """SMTP 附件邮件发送器。"""

    @staticmethod
    def _config() -> SMTPConfig:
        return SMTPConfig(
            host=settings.SMTP_HOST.strip(),
            port=int(settings.SMTP_PORT),
            user=settings.SMTP_USER.strip(),
            password=settings.SMTP_PASSWORD,
            use_tls=bool(settings.SMTP_USE_TLS),
            from_addr=settings.SMTP_FROM_ADDR.strip(),
            from_name=(
                settings.SMTP_FROM_NAME.strip()
                or "ScrapeFlow 招标推送"
            ),
            timeout=int(settings.SMTP_TIMEOUT),
        )

    def is_configured(self) -> bool:
        config = self._config()
        return bool(
            config.host
            and 0 < config.port <= 65535
            and config.user
            and config.password
            and config.from_addr
            and config.timeout > 0
        )

    async def send_with_attachment(
        self,
        to_addrs: list[str],
        subject: str,
        body: str,
        attachment_path: Path,
    ) -> dict[str, Any]:
        """发送带附件邮件。

        最多尝试 4 次：首次发送 + 最多 3 次重试。
        """
        config = self._config()

        if not self.is_configured():
            return {
                "ok": False,
                "message_id": None,
                "error": "SMTP 配置不完整",
            }

        try:
            recipients = _normalize_recipients(to_addrs)
            if not recipients:
                raise ValueError("收件人不能为空")

            subject = _sanitize_header(subject, "Subject")
            from_name = _sanitize_header(
                config.from_name,
                "From name",
            )

            attachment_path = Path(attachment_path)
            if not attachment_path.is_file():
                raise FileNotFoundError(
                    f"附件不存在或不是文件: {attachment_path}"
                )

            message_id = make_msgid(
                domain=config.from_addr.rsplit("@", 1)[-1]
                if "@" in config.from_addr
                else None
            )
            message = build_message(
                config=config,
                from_name=from_name,
                recipients=recipients,
                subject=subject,
                body=body,
                attachment_path=attachment_path,
                message_id=message_id,
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "message_id": None,
                "error": str(exc),
            }

        last_error: Exception | None = None
        total_attempts = MAX_RETRIES + 1

        for attempt in range(total_attempts):
            try:
                await asyncio.to_thread(
                    self._send_sync,
                    config,
                    recipients,
                    message,
                )
                logger.info(
                    "email sent recipients={} message_id={} attempt={}",
                    len(recipients),
                    message_id,
                    attempt + 1,
                )
                return {
                    "ok": True,
                    "message_id": message_id,
                    "error": None,
                }
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(
                    "email send failed attempt={}/{} type={}",
                    attempt + 1,
                    total_attempts,
                    type(exc).__name__,
                )

                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAYS[attempt])

        return {
            "ok": False,
            "message_id": None,
            "error": (
                f"邮件发送失败，已重试 {MAX_RETRIES} 次: "
                f"{type(last_error).__name__}: {last_error}"
            ),
        }

    @classmethod
    def _send_sync(
        cls,
        config: SMTPConfig,
        recipients: list[str],
        message: MIMEMultipart,
    ) -> None:
        if config.use_tls:
            cls._send_via_starttls(
                config,
                recipients,
                message,
            )
        else:
            cls._send_via_ssl(
                config,
                recipients,
                message,
            )

    @staticmethod
    def _raise_if_refused(
        refused: dict[str, tuple[int, bytes]],
    ) -> None:
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)

    @classmethod
    def _send_via_starttls(
        cls,
        config: SMTPConfig,
        recipients: list[str],
        message: MIMEMultipart,
    ) -> None:
        context = ssl.create_default_context()

        with smtplib.SMTP(
            config.host,
            config.port,
            timeout=config.timeout,
        ) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(config.user, config.password)
            refused = server.sendmail(
                config.from_addr,
                recipients,
                message.as_string(),
            )
            cls._raise_if_refused(refused)

    @classmethod
    def _send_via_ssl(
        cls,
        config: SMTPConfig,
        recipients: list[str],
        message: MIMEMultipart,
    ) -> None:
        context = ssl.create_default_context()

        with smtplib.SMTP_SSL(
            config.host,
            config.port,
            timeout=config.timeout,
            context=context,
        ) as server:
            server.login(config.user, config.password)
            refused = server.sendmail(
                config.from_addr,
                recipients,
                message.as_string(),
            )
            cls._raise_if_refused(refused)
