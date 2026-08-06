"""四层实体同步钩子（P0-1 接入入库管线）。

失败降级：实体同步失败只记日志，不影响 Tender 主表入库。
"""

from __future__ import annotations

from typing import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tender import Tender
from app.processors.entity_sync import upsert_tender_entities
from app.utils.logger import get_logger

logger = get_logger("entity_sync_hook")


async def sync_tender_entities(
    db: AsyncSession, tenders: Iterable[Tender]
) -> int:
    """批量把 Tender 同步到四层实体表（幂等，失败跳过单条）。

    只 flush 不 commit，事务边界由调用方控制。

    Returns:
        本次新建实体链的 Tender 条数
    """
    synced = 0
    for tender in tenders:
        try:
            if await upsert_tender_entities(db, tender):
                synced += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "实体同步失败（降级跳过） tender_id={} err={}",
                getattr(tender, "id", None), exc,
            )
    return synced
