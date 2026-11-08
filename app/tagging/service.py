"""打标入库服务：pipeline 挂钩与回填脚本共用的入库逻辑。

设计约束（《AI协作执行标准》防御分支）：
- 打标失败绝不能上抛——LLM 限流/超时/网络故障只记日志并计入 errors；
- 单条写入失败不拖垮批次；
- 已打标的公告幂等跳过（重复运行不产生重复标签）。
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.models.notice_tag import NoticeTag
from app.models.tender import Tender
from app.tagging import tag_many_concurrent

logger = logging.getLogger(__name__)


async def _untagged_tender_ids(db, limit: int, tender_ids: list[int] | None) -> list[int]:
    """取尚未打标的 tender id。tender_ids 限定范围时只在该范围内查。"""
    tagged = select(NoticeTag.tender_id).distinct()
    q = select(Tender.id).where(Tender.id.not_in(tagged))
    if tender_ids:
        q = q.where(Tender.id.in_(tender_ids))
    q = q.order_by(Tender.id).limit(limit)
    rows = await db.execute(q)
    return [r[0] for r in rows.fetchall()]


async def tag_pending_tenders(
    db,
    limit: int = 50,
    concurrency: int = 5,
    tender_ids: list[int] | None = None,
) -> dict[str, int]:
    """给尚未打标的公告打标并入库。

    返回 {"tagged": int, "errors": int}。任何异常都只记日志，不上抛。
    """
    summary: dict[str, int] = {"tagged": 0, "errors": 0}
    try:
        ids = await _untagged_tender_ids(db, limit, tender_ids)
    except Exception as exc:  # noqa: BLE001
        logger.warning("untagged query failed: %s", exc)
        summary["errors"] += 1
        return summary
    if not ids:
        return summary

    try:
        rows = (
            await db.execute(
                select(Tender.id, Tender.project_name, Tender.core_content)
                .where(Tender.id.in_(ids))
                .order_by(Tender.id)
            )
        ).fetchall()
        notices: list[dict[str, Any]] = [
            {"tender_id": r[0], "title": r[1] or "", "content": r[2] or ""}
            for r in rows
        ]
        tagged_items = await tag_many_concurrent(notices, concurrency=concurrency)
    except Exception as exc:  # noqa: BLE001
        logger.warning("tag LLM batch failed: %s", exc)
        summary["errors"] += 1
        return summary

    for item in tagged_items:
        if not isinstance(item, dict) or "tender_id" not in item:
            logger.warning("skip malformed tag item: %r", item)
            summary["errors"] += 1
            continue
        try:
            db.add(NoticeTag(
                tender_id=item["tender_id"],
                industry=item.get("industry") or "其他",
                region=item.get("region") or "未知",
                notice_type=item.get("notice_type") or "其他",
            ))
            summary["tagged"] += 1
        except Exception as exc:  # noqa: BLE001 —— 单条失败不拖垮批次
            logger.warning(
                "insert tag failed tender_id=%s: %s", item.get("tender_id"), exc,
            )
            summary["errors"] += 1

    try:
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        logger.warning("tag commit failed: %s", exc)
        summary["tagged"] = 0
        summary["errors"] += 1
    return summary
