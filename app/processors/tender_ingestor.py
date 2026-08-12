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
from app.processors.tender_utils import _build_tender, _classify_source_role, _infer_platform
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

    # P0 修复：过滤非公告数据（ccgp 的 /zcdt/、/gpsr/、/news/ 栏目是新闻/政策，不是招标公告）
    _BID_NOTICE_PATHS = ("/cggg/",)  # 只有公告栏目才入库
    filtered_items = []
    for _item in items:
        if not isinstance(_item, dict):
            continue
        _item_url = _item.get("detail_url") or _item.get("url") or ""
        # ccgp 平台：只允许 /cggg/ 路径的数据入库
        if "ccgp.gov.cn" in _item_url or "ccgp" in (source_platform or ""):
            if _item_url and not any(_p in _item_url for _p in _BID_NOTICE_PATHS):
                continue  # 非公告栏目，跳过
        filtered_items.append(_item)
    items = filtered_items

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
                    # 修复：无符号 64 位 simhash 超出 SQLite INTEGER 上限（2^63-1）
                    # 会触发 OverflowError 导致入库失败，统一归一到有符号表示
                    sh &= 0xFFFFFFFFFFFFFFFF
                    if sh >= 0x8000000000000000:
                        sh -= 0x10000000000000000
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

    # 阶段 3a：source_url 精确幂等查重（P0-0：防止同一公告重复入库）
    existing_urls: set[str] = set()
    incoming_urls: list[str | None] = []
    for item in items:
        if isinstance(item, dict):
            u = str(item.get("detail_url") or item.get("source_url") or item.get("url") or "").strip()
            incoming_urls.append(u if u else None)
        else:
            incoming_urls.append(None)
    urls_to_check = [u for u in incoming_urls if u]
    if urls_to_check:
        stmt_url = select(Tender.id, Tender.source_url).where(
            Tender.source_url.in_(urls_to_check)
        )
        result_url = await db.execute(stmt_url)
        existing_urls = {row[1] for row in result_url.all() if row[1]}
        if existing_urls:
            logger.info("source_url 精确幂等：已存在 %d 条", len(existing_urls))

    # 阶段 3b：Python 层批量比对 + db.add（M-4：循环内不 flush）
    to_insert: list[tuple[Tender, int | None]] = []
    for idx, item in enumerate(items):
        try:
            if not isinstance(item, dict):
                errors += 1
                continue

            simhash_value = simhash_values[idx]

            # P0-0：source_url 精确幂等——已存在的 URL 直接跳过
            item_url = incoming_urls[idx] if idx < len(incoming_urls) else None
            if item_url and item_url in existing_urls:
                duplicates += 1
                logger.info("跳过重复 source_url idx=%d url=%s", idx, item_url[:80])
                continue

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

            # W3 source_lineage 接入: 计算来源角色并更新 source_platform
            try:
                title = str(item.get("project_name") or item.get("title") or "")
                notice_type = item.get("notice_type")
                core_content = item.get("core_content") or item.get("content")
                role = _classify_source_role(
                    source_url=source_url,
                    title=title,
                    notice_type=notice_type,
                    core_content=core_content,
                )
                # 写入 source_platform 字段 (格式: "原平台:来源角色")
                if role and role != "unknown":
                    tender.source_platform = f"{source_platform}:{role}"
                    logger.info(
                        "source_lineage role=%s url=%s",
                        role, source_url[:80],
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("source_lineage 计算失败: %s", exc)

            # P0-1：确定性字段解析补全（ccgp 公告格式规整，正则先行）
            try:
                from app.processors.ccgp_field_parser import parse_fields
                title = str(item.get("project_name") or item.get("title") or tender.project_name or "")
                content = item.get("core_content") or item.get("content") or item.get("source_raw_text") or ""
                if content and ("ccgp" in (source_platform or "") or "ccgp" in (source_url or "")):
                    parsed = parse_fields(title, content)
                    # 只补全空字段，不覆盖已有值
                    if not tender.tender_org and parsed.get("tender_org"):
                        tender.tender_org = parsed["tender_org"][:300]
                    if not tender.location and parsed.get("location"):
                        tender.location = parsed["location"][:200]
                    if not tender.publish_time and parsed.get("publish_time"):
                        tender.publish_time = parsed["publish_time"]
                    if not tender.budget_amount and parsed.get("budget_amount"):
                        tender.budget_amount = parsed["budget_amount"]
                    if not tender.win_amount and parsed.get("win_amount"):
                        tender.win_amount = parsed["win_amount"]
                    if not tender.notice_type and parsed.get("notice_type"):
                        tender.notice_type = parsed["notice_type"]
                    if not tender.bid_number and parsed.get("bid_number"):
                        tender.bid_number = parsed["bid_number"][:100]
            except Exception as exc:
                logger.warning("ccgp字段解析失败 idx=%d err=%s", idx, exc)

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

        # P0-1：同步四层实体 + 组织/参与方关系（幂等，失败降级不阻塞）
        from app.processors.entity_sync_hook import sync_tender_entities

        await sync_tender_entities(db, [t for t, _ in to_insert])

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
