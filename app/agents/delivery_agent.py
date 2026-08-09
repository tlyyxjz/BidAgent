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

    # 优先复用 processor_agent 已查到的 tender_ids（保证 delivery 查到的
    # 数据与 processor 一致，避免 notice_types 等过滤差异导致查到 0 条）
    # P0 修复：区分"processor 查到0条"(tender_ids=[]) 和"未走 processor"(None)
    # processor 查0条时不应 fallback 出无关数据（如实返回空）
    tender_ids = state.get("tender_ids")
    _processor_ran = tender_ids is not None

    # 查询未推送的 tenders（增量推送语义），查不到 fallback 到全量过滤查询
    async with AsyncSessionLocal() as db:
        if tender_ids:
            # 复用 processor 的查询结果（用户主动查询场景）
            from sqlalchemy import select
            from app.models.tender import Tender as _T
            result = await db.execute(
                select(_T).where(_T.id.in_(tender_ids)).order_by(_T.id.desc())
            )
            unpushed = list(result.scalars().all())
            is_fallback = True
            logger.info(
                "delivery_agent reuse processor tender_ids count={}", len(tender_ids),
            )
        elif _processor_ran:
            # P0 修复：processor 已执行但查到0条，如实返回空（不再 fallback
            # 出无关数据，避免报告内容与查询条件不符）
            logger.info(
                "delivery_agent processor found 0 tenders, skip fallback sub_id={}",
                sub_id,
            )
            unpushed = []
            is_fallback = False
        else:
            unpushed = await get_unpushed_tenders(db, sub_id, parsed)

            # 订阅推送场景：无 unpushed 时 fallback 到全量过滤查询
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
                "bid_number": t.bid_number,
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
            # P2: 透传 region_relaxed，让前端/报告说明"已回退全国数据"
            "region_relaxed": (state.get("process_summary") or {}).get("region_relaxed", False),
        }
        logger.info("delivery_agent skipped (no tenders matched query)")
        return state

    # 生成 Word 报告（传入 finance_summary + quality_summary，生成金融分析+证据验证章节）
    source_texts = _build_source_texts(unpushed)
    finance_summary = state.get("finance_summary")
    quality_summary = state.get("quality_summary")
    report_path = await generate_report(
        parsed,
        items,
        job_id=f"agent_{sub_id}",
        source_texts=source_texts or None,
        finance_summary=finance_summary,
        quality_summary=quality_summary,
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
            # P2: 透传 region_relaxed
            "region_relaxed": (state.get("process_summary") or {}).get("region_relaxed", False),
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
        # P2: 透传 region_relaxed
        "region_relaxed": (state.get("process_summary") or {}).get("region_relaxed", False),
    }

    logger.info(
        "delivery_agent completed report={} delivered={} message_id={}",
        report_path,
        delivery_result.get("delivered", False),
        delivery_result.get("message_id"),
    )
    return state


async def _query_tenders_with_filters(db, filters) -> list[Any]:
    """带过滤条件的全量查询（用户主动查询 / 订阅 fallback）。

    宽松匹配策略：topic / region / keywords 任一命中 project_name /
    core_content / tender_org / location 即返回；全部查不到时返回空列表
    （不再 fallback 返回不相关数据，如实反映查询结果）。

    Args:
        db: AsyncSession
        filters: ParsedFilters

    Returns:
        list[Tender]，最多 100 条，按 publish_time 降序；无匹配返回 []
    """
    from sqlalchemy import or_, select
    from app.scheduler.utils import safe_contains
    from app.models.tender import Tender

    # 1. 宽松匹配：topic（拆词）/ keywords / region 任一命中即返回
    # P0 修复：与 processor_agent 一致，用 _split_topic 拆词，不用整句
    from app.agents.processor_agent import _split_topic
    conditions = []
    topic_words = _split_topic(filters.topic or "")
    for kw in topic_words:
        conditions.append(safe_contains(Tender.project_name, kw))
        conditions.append(safe_contains(Tender.core_content, kw))
    for kw in (getattr(filters, "keywords", []) or []):
        if kw:
            conditions.append(safe_contains(Tender.project_name, kw))
            conditions.append(safe_contains(Tender.tender_org, kw))
    if filters.region:
        # P0 修复：补上 project_name 和 core_content（很多公告 tender_org/location 为空）
        conditions.append(safe_contains(Tender.project_name, filters.region))
        conditions.append(safe_contains(Tender.location, filters.region))
        conditions.append(safe_contains(Tender.tender_org, filters.region))
        conditions.append(safe_contains(Tender.core_content, filters.region))

    if conditions:
        stmt = select(Tender).where(or_(*conditions))
        # P1 修复：notice_types中英文兼容映射（与processor_agent一致）
        if filters.notice_types:
            # B5 修复：与 processor_agent 一致，删除"采购"→tender 映射
            _nt_map = {"中标": "award", "成交": "award", "更正": "correction",
                       "变更": "correction", "招标": "tender"}
            _nt_vals: list[str] = []
            for nt in filters.notice_types:
                matched = False
                for k, v in _nt_map.items():
                    if k in str(nt) or v == str(nt):
                        _nt_vals.append(v)
                        matched = True
                        break
                if not matched:
                    _nt_vals.append(str(nt))
            stmt = stmt.where(Tender.notice_type.in_(_nt_vals))
        stmt = stmt.order_by(Tender.publish_time.desc()).limit(100)
        result = await db.execute(stmt)
        tenders = list(result.scalars().all())
        return tenders

    # 无匹配条件或匹配为空时返回空列表（不再 fallback 返回不相关数据）
    return []


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
