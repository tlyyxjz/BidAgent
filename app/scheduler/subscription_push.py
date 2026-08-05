"""订阅推送辅助逻辑（从 subscription 拆分）：增量查询、去重哈希、推送日志。

本模块仅包含不被测试 monkeypatch 的辅助函数。
trigger_subscription / run_scheduled_subscriptions / create_subscription
保留在 subscription.py 中（因为它们引用的被 patch 的名字必须在同模块命名空间）。
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path

from sqlalchemy import not_, select

from app.llm.schemas import ParsedFilters
from app.models.subscription import PushLog
from app.models.tender import Tender
from app.scheduler.utils import safe_contains, utc_now
from app.utils.logger import get_logger

logger = get_logger("scheduler.subscription")

# M-2 修复：at-least-once 幂等去重窗口。
# commit 失败导致重推时，若 N 分钟内已推送过相同 content_hash，跳过本次推送。
DEDUP_WINDOW_MINUTES = 30


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
