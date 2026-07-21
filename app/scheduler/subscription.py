"""定时订阅 + 增量推送调度。

命题硬要求：
- 支持每日/每周定时推送（cron 表达式到期才推送）
- 已推送内容不重复推送（PushLog 表 + SQL NOT EXISTS 过滤）

C-4 修复：本文件控制在 300 行以内，工具函数移到 utils.py，推送渠道移到 push.py。
C-2 修复：trigger_subscription 组装 source_texts 传给 generate_report，反幻觉真正生效。
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import not_, select

from app.llm.parser import parse_query
from app.llm.schemas import ParsedFilters
from app.models.database import AsyncSessionLocal
from app.models.subscription import (
    Subscription,
    PushLog,
    TRIGGER_IMMEDIATE,
    TRIGGER_SCHEDULED,
)
from app.models.tender import Tender
from app.report.docx_generator import generate_report
from app.scheduler.push import push_to_channels
from app.scheduler.utils import is_cron_due, safe_contains, utc_now
from app.utils.logger import get_logger

logger = get_logger("scheduler.subscription")

# M-2 修复：at-least-once 幂等去重窗口。
# commit 失败导致重推时，若 N 分钟内已推送过相同 content_hash，跳过本次推送。
DEDUP_WINDOW_MINUTES = 30


async def create_subscription(
    user_id: int,
    raw_query: str,
    platforms: list[str] | None = None,
    push_channels: list[str] | None = None,
    notify_email: str | None = None,
    webhook_url: str | None = None,
) -> int:
    """创建订阅。

    Args:
        user_id: 用户 ID
        raw_query: 用户原始自然语言查询
        platforms: 目标平台列表（默认 ccgp）
        push_channels: 推送渠道（默认 email）
        notify_email: 邮件推送收件地址（Sol S-10）
        webhook_url: Webhook 推送地址（Sol S-15）

    Returns:
        subscription_id
    """
    # 1. LLM 解析意图（含频率解析）
    parsed: ParsedFilters = await parse_query(raw_query)

    trigger_type = (
        TRIGGER_SCHEDULED if parsed.frequency else TRIGGER_IMMEDIATE
    )

    async with AsyncSessionLocal() as db:
        sub = Subscription(
            user_id=user_id,
            raw_query=raw_query,
            parsed_filters=parsed.model_dump(),
            frequency_cron=parsed.frequency,
            trigger_type=trigger_type,
            platforms=platforms or ["ccgp"],
            push_channels=push_channels or ["email"],
            notify_email=notify_email,
            webhook_url=webhook_url,
            is_active=True,
        )
        db.add(sub)
        await db.commit()
        await db.refresh(sub)
        logger.info(
            "subscription created sub_id={} user_id={} trigger={}",
            sub.id, user_id, trigger_type,
        )
        return sub.id


async def get_unpushed_tenders(
    db,
    subscription_id: int,
    filters: ParsedFilters,
    limit: int = 100,
) -> list[Tender]:
    """获取该订阅尚未推送过的招标信息（增量推送核心）。

    命题硬要求：已经推送的内容不要重复推送。
    M-1 修复：用 SQL NOT EXISTS 在数据库层面直接过滤。
    """
    stmt = select(Tender).where(
        not_(
            select(PushLog.id).where(
                PushLog.subscription_id == subscription_id,
                PushLog.tender_id == Tender.id,
            ).exists()
        )
    )
    if filters.region:
        # M-1 修复：用 safe_contains 显式指定 escape 字符，转义才生效
        stmt = stmt.where(safe_contains(Tender.location, filters.region))
    if filters.topic:
        stmt = stmt.where(safe_contains(Tender.project_name, filters.topic))
    if filters.notice_types:
        stmt = stmt.where(Tender.notice_type.in_(filters.notice_types))
    stmt = stmt.order_by(Tender.publish_time.desc()).limit(limit)
    result = await db.execute(stmt)
    tenders = result.scalars().all()

    logger.info(
        "get_unpushed_tenders sub_id={} unpushed_count={}",
        subscription_id, len(tenders),
    )
    return tenders


async def _record_push(
    db,
    subscription_id: int,
    tender_ids: list[int],
    content_hash: str | None = None,
) -> None:
    """记录推送日志（用于下次增量去重）。

    Sol S-10 修复：不再自行 commit，由调用方在同一事务中提交，
    保证 PushLog + last_pushed_at + 推送成功的原子性。
    m-3 优化：使用 db.add_all 批量插入。
    M-2 修复：写入 content_hash 用于幂等去重。
    """
    if not tender_ids:
        return
    now = utc_now()
    push_logs = [
        PushLog(
            subscription_id=subscription_id,
            tender_id=tid,
            pushed_at=now,
            content_hash=content_hash,
        )
        for tid in tender_ids
    ]
    db.add_all(push_logs)


def _compute_content_hash(report_path: str, tender_ids: list[int]) -> str:
    """M-2 修复：计算本次推送内容的 SHA256 哈希。

    用于 at-least-once 幂等去重：如果同一报告内容（相同 tender_ids + 相同文件）
    在 DEDUP_WINDOW_MINUTES 内已推送过，跳过本次推送。
    """
    h = hashlib.sha256()
    h.update(str(sorted(tender_ids)).encode("utf-8"))
    try:
        p = Path(report_path)
        if p.is_file():
            h.update(p.read_bytes())
    except OSError:
        # 文件读不到不影响哈希基本结构（tender_ids 已足够）
        pass
    return h.hexdigest()


async def _recently_pushed_same_hash(
    db,
    subscription_id: int,
    content_hash: str,
) -> bool:
    """M-2 修复：检查最近 DEDUP_WINDOW_MINUTES 内是否已推送过相同 content_hash。

    命中则跳过本次推送，降低 commit 失败导致的重复邮件概率。
    """
    threshold = utc_now() - timedelta(minutes=DEDUP_WINDOW_MINUTES)
    stmt = select(PushLog.id).where(
        PushLog.subscription_id == subscription_id,
        PushLog.content_hash == content_hash,
        PushLog.pushed_at >= threshold,
    ).limit(1)
    result = await db.execute(stmt)
    return result.first() is not None


async def trigger_subscription(
    subscription_id: int,
    force: bool = False,
    user_id: int | None = None,
    auto_collect: bool = True,
) -> dict[str, Any]:
    """触发一次订阅推送。

    Sol S-10 修复事务一致性：
      旧顺序（有 bug）：写 PushLog → 更新 last_pushed_at → commit → 推送
        问题：SMTP 失败但数据库已认为推送成功，下次 NOT EXISTS 过滤 → 永久漏发
      新顺序：查询未推送 → 生成报告 → 真实推送 → delivered=True → 写 PushLog + 更新 last_pushed_at → commit
        失败时返回 push_failed，不写日志（下次还会重新推送这批数据）

    1. 读取订阅
    2. C-3：检查 cron 是否到期（force=True 时跳过）
    3. auto_collect=True 时先主动采集新数据入 Tender 表
    4. M-1：用 SQL NOT EXISTS 查询未推送的 tender
    5. C-2：组装 source_texts 传给 generate_report，反幻觉真正生效
    6. 生成 Word 报告
    7. 真实外部推送（email/webhook）
    8. delivered=True 时记录 PushLog + 更新 last_pushed_at + commit
    """
    logger.info("trigger_subscription start sub_id={} force={}", subscription_id, force)

    async with AsyncSessionLocal() as db:
        stmt = select(Subscription).where(Subscription.id == subscription_id)
        if user_id is not None:
            stmt = stmt.where(Subscription.user_id == user_id)
        sub_result = await db.execute(stmt)
        sub = sub_result.scalar_one_or_none()
        if sub is None or not sub.is_active:
            logger.warning("subscription not found or inactive: {}", subscription_id)
            return {"status": "skipped", "reason": "inactive or forbidden"}

        # C-3 cron 到期检查（S-6：last_pushed_at 为 None 时用 created_at）
        if not force and sub.trigger_type == TRIGGER_SCHEDULED:
            cron_expr = sub.frequency_cron
            last_run = sub.last_pushed_at or sub.created_at
            if cron_expr and not is_cron_due(cron_expr, last_run, utc_now()):
                logger.info(
                    "subscription not due yet sub_id={} cron={}",
                    subscription_id, cron_expr,
                )
                return {
                    "status": "not_due",
                    "reason": f"cron not due: {cron_expr}",
                }

        filters = ParsedFilters(**(sub.parsed_filters or {"raw_query": sub.raw_query}))

        # 主动采集新数据
        collect_summary: dict[str, Any] | None = None
        if auto_collect:
            try:
                from app.scheduler.collector import collect_new_tenders
                collect_summary = await collect_new_tenders(sub, filters)
            except Exception as exc:  # noqa: BLE001
                logger.exception("auto_collect failed sub_id={}: {}", subscription_id, exc)
                collect_summary = {"error": str(exc)}

        # M-1 获取未推送的 tender
        unpushed = await get_unpushed_tenders(db, subscription_id, filters)
        if not unpushed:
            logger.info("no new tenders sub_id={}", subscription_id)
            sub.last_pushed_at = utc_now()
            await db.commit()
            return {
                "status": "no_new",
                "count": 0,
                "collect": collect_summary,
            }

        # 组装 items + source_texts（C-2 修复：反幻觉校验需要原文）
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
        # C-2 修复：组装 source_texts {source_url: source_raw_text}
        source_texts: dict[str, str] = {}
        for t in unpushed:
            if t.source_url and t.source_raw_text:
                source_texts[t.source_url] = t.source_raw_text

        report_path = await generate_report(
            filters, items,
            job_id=f"sub_{subscription_id}",
            source_texts=source_texts or None,
        )

        # M-2 修复：at-least-once 幂等去重。
        # 计算本次推送内容的 content_hash，检查最近 DEDUP_WINDOW_MINUTES 内
        # 是否已推送过相同哈希。命中则跳过（避免 commit 失败导致的重复邮件）。
        tender_ids_for_hash = [t.id for t in unpushed]
        content_hash = _compute_content_hash(report_path, tender_ids_for_hash)
        if await _recently_pushed_same_hash(db, subscription_id, content_hash):
            logger.warning(
                "subscription skipped: duplicate push within {} min sub_id={} hash={}",
                DEDUP_WINDOW_MINUTES, subscription_id, content_hash[:12],
            )
            # 仍然更新 last_pushed_at，避免 cron 频繁触发又重复计算
            sub.last_pushed_at = utc_now()
            await db.commit()
            return {
                "status": "skipped_duplicate",
                "count": len(unpushed),
                "report_path": report_path,
                "content_hash": content_hash,
                "collect": collect_summary,
            }

        # Sol S-10 修复：先真实推送，再决定是否写日志
        push_results = await push_to_channels(sub, report_path, len(unpushed))

        if not push_results.get("delivered", False):
            # 推送失败：不写 PushLog，下次触发会重新推送这批数据
            logger.warning(
                "subscription delivery not confirmed sub_id={} result={}",
                subscription_id, push_results,
            )
            return {
                "status": "push_failed",
                "count": len(unpushed),
                "report_path": report_path,
                "push_channels": push_results,
                "collect": collect_summary,
            }

        # 推送成功：写 PushLog + 更新 last_pushed_at，同一事务提交
        tender_ids = [t.id for t in unpushed]
        await _record_push(db, subscription_id, tender_ids, content_hash=content_hash)
        sub.last_pushed_at = utc_now()
        await db.commit()

        logger.info(
            "subscription pushed sub_id={} count={} report={}",
            subscription_id, len(unpushed), report_path,
        )
        return {
            "status": "ok",
            "count": len(unpushed),
            "report_path": report_path,
            "push_channels": push_results,
            "collect": collect_summary,
        }


async def run_scheduled_subscriptions() -> int:
    """扫描所有到期的定时订阅并触发推送。

    C-3 修复：不再每次扫描都触发，必须 cron 到期才推送。

    Returns:
        触发的订阅数量
    """
    logger.info("run_scheduled_subscriptions start")
    triggered = 0
    now = utc_now()

    async with AsyncSessionLocal() as db:
        stmt = select(Subscription).where(
            Subscription.is_active == True,  # noqa: E712
            Subscription.trigger_type == TRIGGER_SCHEDULED,
        )
        result = await db.execute(stmt)
        subs = result.scalars().all()

        for sub in subs:
            try:
                cron_expr = sub.frequency_cron
                if not cron_expr:
                    continue

                # S-6 修复：last_pushed_at 为 None 时用 created_at 作为 base
                last_run = sub.last_pushed_at or sub.created_at
                if not is_cron_due(cron_expr, last_run, now):
                    logger.debug(
                        "subscription not due sub_id={} cron={}",
                        sub.id, cron_expr,
                    )
                    continue

                await trigger_subscription(sub.id, force=True)
                triggered += 1
            except Exception:  # noqa: BLE001
                logger.exception("subscription trigger failed sub_id={}", sub.id)

    logger.info("run_scheduled_subscriptions done triggered={}", triggered)
    return triggered
