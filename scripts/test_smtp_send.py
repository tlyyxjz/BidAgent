"""SMTP 实发邮件联调脚本（命题 6 真实联调）。

读取 .env 中的 SMTP 配置，发送一封带 Word 附件的测试邮件到 SMTP_USER 自身。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 确保能 import app.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.email_sender import EmailSender  # noqa: E402
from app.utils.logger import get_logger, setup_logging  # noqa: E402

setup_logging()
logger = get_logger("smtp_test")


async def main() -> int:
    sender = EmailSender()

    if not sender.is_configured():
        logger.error("SMTP 配置不完整，请检查 .env")
        return 1

    # 找一个最新的 Word 报告作为附件
    reports_dir = Path("data/reports")
    docx_files = sorted(
        reports_dir.glob("*.docx"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not docx_files:
        logger.error("data/reports/ 下没有 docx 文件作为附件")
        return 2

    attachment = docx_files[0]
    logger.info("使用附件: %s (%.1f KB)", attachment.name, attachment.stat().st_size / 1024)

    # 收件人 = 发件人（自己发给自己）
    from app.config import settings
    to_addr = settings.SMTP_FROM_ADDR or settings.SMTP_USER
    if not to_addr:
        logger.error("SMTP_FROM_ADDR / SMTP_USER 都为空")
        return 3

    subject = "[ScrapeFlow 联调测试] 命题 6 增量推送 - SMTP 实发验证"
    body = f"""ScrapeFlow 命题 6 真实联调测试邮件

发件人: {settings.SMTP_FROM_ADDR}
收件人: {to_addr}
SMTP 服务器: {settings.SMTP_HOST}:{settings.SMTP_PORT} ({"STARTTLS" if settings.SMTP_USE_TLS else "SMTP_SSL"})
附件: {attachment.name}

这是一封由 ScrapeFlow 系统自动发送的测试邮件，用于验证命题 6（增量推送）的 SMTP 链路是否真实可用。

如果你收到了这封邮件且附件可以正常打开，说明：
1. SMTP 配置正确（host/port/user/password/use_tls）
2. 邮件头注入防护通过
3. 附件 MIME 编码正确
4. asyncio.to_thread 包装的同步 smtplib 调用正常
5. 命题 6 增量推送链路代码完成 → 真实联调通过

—— ScrapeFlow 智汇标讯
"""

    logger.info("开始发送邮件到 %s ...", to_addr)
    result = await sender.send_with_attachment(
        to_addrs=[to_addr],
        subject=subject,
        body=body,
        attachment_path=attachment,
    )

    logger.info("发送结果: %s", result)

    if result.get("ok"):
        logger.info("✅ 邮件发送成功！message_id=%s", result.get("message_id"))
        return 0
    else:
        logger.error("❌ 邮件发送失败: %s", result.get("error"))
        return 4


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
