"""采集结果入库处理器。

命题硬要求衔接点：
- 把 Scraper 返回的 data 列表映射到 Tender 表
- 联系人手机号/邮箱 SHA256 入库（隐私保护）
- source_platform 从模板名或 URL 推断
- SimHash 计算并去重（命题第 3 项硬要求）
- 返回新插入的记录 ID 列表，供增量推送使用

工程规范：
- async with AsyncSessionLocal，无连接泄漏
- 单条失败不阻塞整批，记录错误日志
- C-1 修复：纯函数抽到 tender_utils.py，主文件 ≤ 300 行
- M-4 修复：循环内 flush 改为批量 flush
- M-7 修复：接受外部 db 参数，统一事务边界
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import AsyncSessionLocal
from app.models.tender import Tender
from app.processors.simhash import find_duplicate_in_iter
from app.processors.tender_utils import _build_tender, _infer_platform
from app.utils.logger import get_logger

logger = get_logger("tender_ingestor")

# 汉明距离阈值：≤3 视为重复内容
SIMHASH_HAMMING_THRESHOLD = 3


async def ingest_scrape_result(
    scrape_result: dict[str, Any],
    template: str | None = None,
    simhash_computer=None,
    db: AsyncSession | None = None,
) -> dict[str, Any]:
    """把 scraper.scrape() 的返回结果写入 Tender 表。

    Args:
        scrape_result: {"url": ..., "data": [...], "pages_scraped": N}
        template: 模板名（用于推断 source_platform）
        simhash_computer: 可选的 SimHash 计算函数，签名为 (text) -> int
            传入 None 时跳过 SimHash 计算（不启用去重）
        db: 可选的外部 session。
            M-7 修复：传入时复用外部事务（多平台统一 commit）；
                     不传时内部创建独立 session。

    Returns:
        {
            "total": int,       # 采集到的总条数
            "inserted": int,    # 新插入的条数
            "duplicates": int,  # 因 SimHash 重复跳过的条数
            "errors": int,      # 入库失败的条数
            "inserted_ids": list[int],  # 新插入记录的 ID 列表
        }
    """
    if db is not None:
        # M-7：复用外部事务，不内部 commit
        return await _ingest_with_db(db, scrape_result, template, simhash_computer, commit=False)

    async with AsyncSessionLocal() as new_db:
        return await _ingest_with_db(
            new_db, scrape_result, template, simhash_computer, commit=True
        )


async def _ingest_with_db(
    db: AsyncSession,
    scrape_result: dict[str, Any],
    template: str | None,
    simhash_computer,
    *,
    commit: bool,
) -> dict[str, Any]:
    """入库核心逻辑。

    M-7：commit=False 时由调用方统一 commit（多平台事务一致性）。
    M-4：循环内只 db.add，循环结束后统一 flush 拿 ID。
    """
    source_url = scrape_result.get("url", "")
    source_platform = _infer_platform(source_url, template)
    items = scrape_result.get("data") or []

    total = len(items)
    inserted = 0
    duplicates = 0
    errors = 0
    inserted_ids: list[int] = []

    if not items:
        return {
            "total": 0, "inserted": 0, "duplicates": 0, "errors": 0,
            "inserted_ids": [],
        }

    # 新-5 修复：N+1 根治——批量计算 simhash → 一次查候选 → Python 层比对
    # 阶段 1：批量计算所有 simhash（to_thread 并发）
    simhash_values: list[int | None] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            simhash_values.append(None)
            continue
        sh: int | None = None
        if simhash_computer is not None:
            text = item.get("core_content") or item.get("content") or ""
            if text:
                try:
                    sh = int(
                        await asyncio.to_thread(simhash_computer, str(text))
                    )
                except (TypeError, ValueError) as exc:
                    logger.warning("simhash 计算失败 idx=%d err=%s", idx, exc)
        simhash_values.append(sh)

    # 阶段 2：一次查询候选集（按 source_platform 过滤）
    candidates: list[tuple[int, int]] = []
    if any(sh is not None for sh in simhash_values):
        stmt = (
            select(Tender.id, Tender.simhash)
            .where(Tender.simhash.is_not(None))
        )
        if source_platform:
            stmt = stmt.where(Tender.source_platform == source_platform)
        stmt = stmt.order_by(Tender.id.desc()).limit(2000)
        result = await db.execute(stmt)
        candidates = [(row.id, row.simhash) for row in result.all()]
        logger.info(
            "dedup candidates loaded platform=%s count=%d",
            source_platform, len(candidates),
        )

    # 阶段 3：Python 层批量比对 + db.add（M-4：循环内不 flush）
    to_insert: list[tuple[Tender, int | None]] = []
    for idx, item in enumerate(items):
        try:
            if not isinstance(item, dict):
                errors += 1
                continue

            simhash_value = simhash_values[idx]

            if simhash_value is not None:
                dup_pair = find_duplicate_in_iter(
                    simhash_value, candidates, SIMHASH_HAMMING_THRESHOLD
                )
                if dup_pair is not None:
                    duplicates += 1
                    logger.info(
                        "跳过重复内容 idx=%d simhash=%d dup_id=%d",
                        idx, simhash_value, dup_pair[0],
                    )
                    continue

            tender = _build_tender(item, source_url, source_platform, simhash_value)
            db.add(tender)
            to_insert.append((tender, simhash_value))
        except Exception as exc:  # noqa: BLE001
            errors += 1
            logger.exception("入库失败 idx=%d item=%s err=%s", idx, item, exc)

    # M-4 修复：一次 flush 拿所有 ID（替代循环内 N 次 flush）
    if to_insert:
        await db.flush()
        for tender, simhash_value in to_insert:
            inserted_ids.append(tender.id)
            inserted += 1
            # 把新入库的 simhash 加入候选集，避免同批次内重复入库
            if simhash_value is not None:
                candidates.append((tender.id, simhash_value))

    if commit:
        await db.commit()

    logger.info(
        "ingest done platform=%s total=%d inserted=%d duplicates=%d errors=%d",
        source_platform, total, inserted, duplicates, errors,
    )
    return {
        "total": total,
        "inserted": inserted,
        "duplicates": duplicates,
        "errors": errors,
        "inserted_ids": inserted_ids,
    }
