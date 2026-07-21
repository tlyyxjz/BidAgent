"""Agent 6: 报告交付 Agent。

职责：生成 Word 报告 + 邮件/Webhook 推送。

核心能力：
- Word 报告生成（含金融分析章节：BOQ 异常 + 废标风险 + 供应商信用）
- SMTP 邮件推送（已实测通过，163 邮箱实发成功）
- Webhook HMAC 签名推送
- at-least-once 推送 + content_hash 幂等去重（M-2 修复）

复用：
- app/report/docx_generator.py — Word 报告生成
- app/core/email_sender.py — SMTP 邮件
- app/core/webhook_sender.py — Webhook 推送
- app/scheduler/push.py — 推送通道
- app/scheduler/subscription.py — trigger_subscription
"""

from __future__ import annotations

from typing import Any

from app.utils.logger import get_logger

logger = get_logger("agent.delivery")


async def delivery_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Agent 6: 报告交付（Word 报告 + 邮件/Webhook 推送）。

    输入 state:
        - parsed_filters: ParsedFilters (必填)
        - subscription_id: int (必填)
        - finance_summary: dict — 金融分析结果（来自 finance_agent）
        - quality_summary: dict — 质检结果（来自 quality_agent）

    输出 state（新增）:
        - report_path: str | None — Word 报告路径
        - delivery_summary: dict — 交付结果
            - report_generated: bool — 报告是否生成
            - email_sent: bool — 邮件是否发送
            - webhook_sent: bool — Webhook 是否发送
            - delivered: bool — 是否至少一个通道送达
            - message_id: str | None — 邮件 message_id
    """
    from app.models.database import AsyncSessionLocal
    from app.report.docx_generator import generate_report
    from app.scheduler.subscription import get_unpushed_tenders

    parsed = state.get("parsed_filters")
    sub_id = state.get("subscription_id")
    if parsed is None or sub_id is None:
        raise ValueError("parsed_filters and subscription_id are required")

    logger.info("delivery_agent started sub_id={}", sub_id)

    # 查询未推送的 tenders
    async with AsyncSessionLocal() as db:
        unpushed = await get_unpushed_tenders(db, sub_id, parsed)
        items = [
            {
                "project_name": t.project_name,
                "publish_time": t.publish_time.isoformat() if t.publish_time else None,
                "source_url": t.source_url,
                "core_content": t.core_content,
                "attachment_url": t.attachment_url,
                "budget_amount": float(t.budget_amount) if t.budget_amount else None,
                "tender_org": t.tender_org,
                "deadline": t.deadline.isoformat() if t.deadline else None,
                "source_platform": t.source_platform,
            }
            for t in unpushed
        ]

    if not items:
        state["report_path"] = None
        state["delivery_summary"] = {
            "report_generated": False,
            "email_sent": False,
            "webhook_sent": False,
            "delivered": False,
            "message_id": None,
            "reason": "no new tenders",
        }
        logger.info("delivery_agent skipped (no new tenders)")
        return state

    # 生成 Word 报告
    source_texts = _build_source_texts(unpushed)
    report_path = await generate_report(
        parsed,
        items,
        job_id=f"agent_{sub_id}",
        source_texts=source_texts or None,
    )
    state["report_path"] = report_path

    # 触发推送（SMTP + Webhook，复用现有 trigger_subscription 逻辑）
    delivery_result = await _trigger_push(state, sub_id, unpushed)

    state["delivery_summary"] = {
        "report_generated": True,
        "report_path": report_path,
        "total_tenders": len(unpushed),
        "email_sent": delivery_result.get("email_sent", False),
        "webhook_sent": delivery_result.get("webhook_sent", False),
        "delivered": delivery_result.get("delivered", False),
        "message_id": delivery_result.get("message_id"),
    }

    logger.info(
        "delivery_agent completed report={} delivered={} message_id={}",
        report_path,
        delivery_result.get("delivered", False),
        delivery_result.get("message_id"),
    )
    return state


def _build_source_texts(tenders: list[Any]) -> dict[str, str]:
    """构建溯源引用文本（每条数据标注来源 URL + 提取片段）。"""
    source_texts: dict[str, str] = {}
    for t in tenders:
        if t.source_url and t.core_content:
            source_texts[t.source_url] = t.core_content[:500]
    return source_texts


async def _trigger_push(
    state: dict[str, Any],
    subscription_id: int,
    unpushed: list[Any],
) -> dict[str, Any]:
    """触发推送（SMTP + Webhook）。

    复用 app/scheduler/subscription.trigger_subscription 的推送逻辑，
    但跳过增量检查（已在 delivery_agent 中确认有 unpushed）。
    """
    from app.models.database import AsyncSessionLocal
    from app.models.subscription import Subscription
    from app.scheduler.push import push_to_channels
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Subscription).where(Subscription.id == subscription_id)
        )
        sub = result.scalar_one_or_none()
        if sub is None:
            logger.warning("subscription {} not found, skip push", subscription_id)
            return {"delivered": False, "reason": "subscription not found"}

        report_path = state.get("report_path", "")
        push_results = await push_to_channels(sub, report_path, len(unpushed))

    # 解析推送结果
    email_sent = any(
        r.get("channel") == "email" and r.get("delivered")
        for r in push_results
    )
    webhook_sent = any(
        r.get("channel") == "webhook" and r.get("delivered")
        for r in push_results
    )
    message_id = next(
        (r.get("message_id") for r in push_results if r.get("message_id")),
        None,
    )

    return {
        "email_sent": email_sent,
        "webhook_sent": webhook_sent,
        "delivered": email_sent or webhook_sent,
        "message_id": message_id,
    }
