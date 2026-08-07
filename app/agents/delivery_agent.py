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

    # 查询未推送的 tenders（增量推送语义）
    async with AsyncSessionLocal() as db:
        unpushed = await get_unpushed_tenders(db, sub_id, parsed)

        # Bug 17 修复：用户主动查询场景下，即使无"新推送"数据，
        # 也应基于查询条件生成报告（避免前端被迫走残缺的 demo_report fallback）。
        # 回退到带过滤的全量查询（与 get_unpushed_tenders 相同的 region/topic/notice_types 过滤，
        # 但不带 NOT EXISTS 增量过滤），确保用户拿到与查询主题相关的完整报告。
        is_fallback = False
        if not unpushed:
            logger.info(
                "delivery_agent no unpushed tenders, fallback to filtered query sub_id={}",
                sub_id,
            )
            unpushed = await _query_tenders_with_filters(db, parsed)
            is_fallback = bool(unpushed)

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
            "reason": "no tenders matched",
        }
        logger.info("delivery_agent skipped (no tenders matched query)")
        return state

    # 生成 Word 报告（传入 finance_summary，生成金融分析章节）
    source_texts = _build_source_texts(unpushed)
    finance_summary = state.get("finance_summary")
    report_path = await generate_report(
        parsed,
        items,
        job_id=f"agent_{sub_id}",
        source_texts=source_texts or None,
        finance_summary=finance_summary,
    )
    state["report_path"] = report_path

    # 触发推送（SMTP + Webhook，复用现有 trigger_subscription 逻辑）
    # Bug 17 注：fallback 场景下不触发推送（避免对已推送数据重复推送）
    if is_fallback:
        state["delivery_summary"] = {
            "report_generated": True,
            "report_path": report_path,
            "total_tenders": len(unpushed),
            "email_sent": False,
            "webhook_sent": False,
            "delivered": False,
            "message_id": None,
            "fallback_query": True,
        }
        logger.info(
            "delivery_agent completed (fallback) report={} skipped push",
            report_path,
        )
        return state

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


async def _query_tenders_with_filters(db, filters) -> list[Any]:
    """带过滤条件的全量查询（Bug 17 修复：fallback 查询）。

    与 get_unpushed_tenders 相同的 region/topic/notice_types 过滤逻辑，
    但不带 NOT EXISTS 增量过滤，用于用户主动查询场景下数据已全部推送时的回退查询。

    Args:
        db: AsyncSession
        filters: ParsedFilters

    Returns:
        list[Tender]，最多 100 条，按 publish_time 降序
    """
    from sqlalchemy import select
    from app.scheduler.utils import safe_contains
    from app.models.tender import Tender

    stmt = select(Tender)
    if filters.region:
        stmt = stmt.where(safe_contains(Tender.location, filters.region))
    if filters.topic:
        stmt = stmt.where(safe_contains(Tender.project_name, filters.topic))
    if filters.notice_types:
        stmt = stmt.where(Tender.notice_type.in_(filters.notice_types))
    stmt = stmt.order_by(Tender.publish_time.desc()).limit(100)
    result = await db.execute(stmt)
    return list(result.scalars().all())


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

    # 解析推送结果（push_to_channels 返回 dict, channels 是列表）
    channels = push_results.get("channels", []) if isinstance(push_results, dict) else push_results
    email_sent = any(
        r.get("channel") == "email" and r.get("delivered")
        for r in channels
    )
    webhook_sent = any(
        r.get("channel") == "webhook" and r.get("delivered")
        for r in channels
    )
    message_id = next(
        (r.get("message_id") for r in channels if r.get("message_id")),
        None,
    )

    return {
        "email_sent": email_sent,
        "webhook_sent": webhook_sent,
        "delivered": email_sent or webhook_sent,
        "message_id": message_id,
    }
