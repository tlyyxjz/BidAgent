"""推送渠道实现（email / webhook）。

C-4 修复：从 subscription.py 拆分出来，让 subscription.py 控制在 300 行以内。

Sol S-10/S-15 升级：
- 接入 EmailSender 真实 SMTP 发送（带 Word 附件）
- 接入 WebhookSender HMAC-SHA256 签名推送
- 通道未配置/未交付时降级为 log，但 delivered=False（不假装成功）

工程规范：
- async/await，无阻塞 IO
- 失败不抛异常，记录错误返回（避免阻塞整个推送流程）
- 推送结果统一结构 {channel, ok, delivered, message_id, error}
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.email_sender import EmailSender
from app.core.webhook_sender import WebhookSender
from app.models.subscription import Subscription
from app.utils.logger import get_logger

logger = get_logger("scheduler.push")

# 模块级单例（EmailSender 内部从 settings 读取 SMTP 配置）
_email_sender = EmailSender()
_webhook_sender = WebhookSender()


async def push_to_channels(
    sub: Subscription, report_path: str, count: int
) -> dict[str, Any]:
    """推送到指定渠道。

    命题硬要求：生成可持续订阅的结果文档。

    Args:
        sub: 订阅对象
        report_path: Word 报告文件路径
        count: 推送的招标信息条数

    Returns:
        {
            "delivered": bool,  # 是否发生真实外部推送（至少一个通道 delivered=True）
            "channels": [
                {"channel": "email"/"webhook"/"log", "ok": bool, "delivered": bool,
                 "message_id": str|None, "error": str|None},
                ...
            ]
        }
    """
    channels = sub.push_channels or []
    attachment = Path(report_path)
    results: list[dict[str, Any]] = []
    any_delivered = False

    if not channels:
        # 没有配置通道，降级为 log（delivered=False）
        results.append({
            "channel": "log",
            "ok": True,
            "delivered": False,
            "message_id": None,
            "error": "未配置推送渠道",
        })
    else:
        for channel in channels:
            if channel == "email":
                result = await _push_email(sub, attachment, count)
            elif channel == "webhook":
                result = await _push_webhook(sub, attachment, count)
            else:
                result = {
                    "channel": channel,
                    "ok": False,
                    "delivered": False,
                    "message_id": None,
                    "error": f"未知渠道: {channel}",
                }
            if result.get("delivered"):
                any_delivered = True
            results.append(result)

    return {"delivered": any_delivered, "channels": results}


async def _push_email(
    sub: Subscription, attachment: Path, count: int
) -> dict[str, Any]:
    """邮件推送（命题第 6 项硬要求）。"""
    # 1. 必须配置 notify_email
    if not sub.notify_email:
        logger.warning(
            "email push skipped: notify_email not set sub_id=%s",
            sub.id,
        )
        return {
            "channel": "log",
            "ok": True,
            "delivered": False,
            "message_id": None,
            "error": "notify_email 未配置，降级为日志",
        }

    # 2. SMTP 必须配置完整
    if not _email_sender.is_configured():
        logger.warning(
            "email push skipped: SMTP not configured sub_id=%s",
            sub.id,
        )
        return {
            "channel": "log",
            "ok": True,
            "delivered": False,
            "message_id": None,
            "error": "SMTP 未配置，降级为日志",
        }

    # 3. 附件必须存在
    if not attachment.is_file():
        logger.error(
            "email push failed: report not found sub_id=%s path=%s",
            sub.id, attachment,
        )
        return {
            "channel": "email",
            "ok": False,
            "delivered": False,
            "message_id": None,
            "error": f"报告附件不存在: {attachment}",
        }

    # 4. 真实发送
    subject = f"【ScrapeFlow】订阅 #{sub.id} 新增 {count} 条招标信息"
    body = (
        f"您好，\n\n"
        f"您的订阅 #{sub.id} 新增 {count} 条招标信息。\n"
        f"详见附件报告：{attachment.name}\n\n"
        f"— ScrapeFlow 招标推送"
    )
    result = await _email_sender.send_with_attachment(
        to_addrs=[sub.notify_email],
        subject=subject,
        body=body,
        attachment_path=attachment,
    )
    return {
        "channel": "email",
        "ok": result["ok"],
        "delivered": result["ok"],
        "message_id": result.get("message_id"),
        "error": result.get("error"),
    }


async def _push_webhook(
    sub: Subscription, attachment: Path, count: int
) -> dict[str, Any]:
    """Webhook 推送（命题第 6 项加分项）。"""
    if not sub.webhook_url:
        logger.warning(
            "webhook push skipped: webhook_url not set sub_id=%s",
            sub.id,
        )
        return {
            "channel": "log",
            "ok": True,
            "delivered": False,
            "message_id": None,
            "error": "webhook_url 未配置，降级为日志",
        }

    payload = {
        "subscription_id": sub.id,
        "count": count,
        "report_filename": attachment.name,
        "report_path": str(attachment),
    }
    result = await _webhook_sender.send(
        url=sub.webhook_url,
        payload=payload,
    )
    return {
        "channel": "webhook",
        "ok": result["ok"],
        "delivered": result["ok"],
        "message_id": None,
        "error": result.get("error"),
    }
