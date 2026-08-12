"""订阅触发时的主动采集模块。

从 subscription.py 拆分出来，避免单文件超过 300 行硬约束。

职责：
- 基于订阅的平台和过滤条件构造 scraper 请求
- 调用 scraper 抓取 + tender_ingestor 入库
- 失败不阻塞推送流程（数据库里已有旧数据可推送）

M-6 修复：多平台并发采集（asyncio.gather + Semaphore），降低总耗时。
新-2 修复：并发抓取但串行入库，避免 SQLite 并发写锁冲突。
m-3 修复：ccgp 升级到 https。
m-8 修复：ggzy 不支持 URL 参数搜索，跳过关键词搜索（避免抓全站）。
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

from app.llm.schemas import ParsedFilters
from app.models.subscription import Subscription
from app.utils.logger import get_logger

logger = get_logger("scheduler.collector")


# 平台搜索 URL 模板（公开搜索页面，免登录）
# m-3 修复：ccgp 升级到 https
_PLATFORM_URLS: dict[str, str] = {
    "ccgp": "https://search.ccgp.gov.cn/bxsearch?searchtype=1&page_index=1",
    "chinabidding": "https://www.chinabidding.cn/search/searchzbgg",
    # m-8 修复：ggzy 不支持 URL 参数搜索，移除（关键词搜索会失效，等于抓全站）
    # "ggzy": "https://deal.ggzy.gov.cn/ds/deal/dealList.html",
}

# M-6 修复：并发上限（避免被封）
_MAX_CONCURRENT_PLATFORMS = 3


def build_scrape_request(
    platform: str, filters: ParsedFilters
) -> dict[str, Any] | None:
    """基于平台名和过滤条件构造 scraper 请求。

    P0 修复：搜索词必须包含 region（地区）+ topic（主题），
    否则"山东教育中标"只会用"教育"搜，丢掉地区和类型。
    公告类型通过 ccgp 的 bidType 参数过滤。
    """
    url = _PLATFORM_URLS.get(platform)
    if url is None:
        return None

    topic = filters.topic or filters.raw_query or ""
    region = filters.region or ""

    # P0 修复：搜索词只用 topic（纯主题词），不加 region
    # 原因：ccgp搜索引擎对"山东教育"这种组合词匹配率极低（0-1条），
    # 单独搜"教育"能有20+条结果。地区过滤由processor在DB层做。
    search_kw = topic or filters.raw_query or ""

    # P1 修复：当 topic 含时间词/空格（intent fallback 把整句 raw_query 当 topic 时），
    # 用 region 作为搜索词（如"北京 近7天"→topic="北京 近7天"→改用 region="北京"）
    _TIME_WORDS = ["最近", "近", "天", "个月", "月", "年"]
    if (not search_kw
        or " " in search_kw
        or any(w in search_kw for w in _TIME_WORDS)):
        if region and region != search_kw:
            search_kw = region
            logger.info("collector search_kw fallback to region={!r}", search_kw)

    # ccgp bidType 映射（支持中英文）
    # 中文关键词 → bidType 值
    _NT_KEYWORDS = [
        (["中标", "成交", "award"], 2),
        (["招标", "采购", "tender"], 1),
        (["更正", "变更", "correction"], 3),
        (["废标", "流标", "cancel"], 4),
    ]
    notice_types = filters.notice_types or []
    bid_type = 0  # 默认全部
    for nt in notice_types:
        nt_str = str(nt)
        for keywords, val in _NT_KEYWORDS:
            if any(kw in nt_str or kw in nt_str.lower() for kw in keywords):
                bid_type = val
                break
        if bid_type != 0:
            break

    if platform == "ccgp":
        url = f"{url}&bidSort=0&pinMu=0&bidType={bid_type}&kw={quote(search_kw)}&displayRent="
    elif platform == "chinabidding":
        url = f"{url}?keyword={quote(search_kw)}"

    logger.info(
        "build_scrape_request platform={} kw='{}' region='{}' topic='{}' bidType={} notice_types={}",
        platform, search_kw, region, topic, bid_type, notice_types,
    )

    import os as _os
    _max_pages = int(_os.environ.get("SCRAPER_MAX_PAGES", "3"))

    return {
        "url": url,
        "template": platform,
        "max_pages": _max_pages,
    }


async def _scrape_one_platform(
    platform: str,
    filters: ParsedFilters,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    """新-2 修复：只负责抓取，不入库（入库由 collect_new_tenders 串行执行）。

    Returns:
        {"platform": str, "result": scrape_result_dict} 或 {"platform": str, "error": str}
    """
    async with semaphore:
        from app.core.scraper import ScrapeError, scraper

        request = build_scrape_request(platform, filters)
        if request is None:
            return {"platform": platform, "error": "unsupported platform"}

        try:
            result = await scraper.scrape(request)
            return {"platform": platform, "result": result}
        except ScrapeError as exc:
            logger.warning("collect failed platform=%s err=%s", platform, exc)
            return {"platform": platform, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            logger.exception("collect unexpected error platform=%s", platform)
            return {"platform": platform, "error": str(exc)}


async def collect_new_tenders(
    sub: Subscription, filters: ParsedFilters
) -> dict[str, Any]:
    """主动采集新数据入库（命题硬要求：采集 → 入库 → 推送 完整链路）。

    M-6 修复：多平台并发抓取（asyncio.gather + Semaphore）。
    新-2 修复：抓取并发，入库串行（避免 SQLite database is locked）。
    M-7 修复：所有平台共用一个 session，统一事务边界（一损俱损一荣俱荣）。
    失败时只记录日志，不阻塞推送流程（数据库里已有旧数据可推送）。

    Returns:
        采集摘要 {"total": N, "inserted": N, "duplicates": N, "errors": N}
    """
    from app.models.database import AsyncSessionLocal
    from app.processors.simhash import compute_simhash
    from app.processors.tender_ingestor import ingest_scrape_result

    platforms = sub.platforms or ["ccgp"]
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_PLATFORMS)

    # 阶段 1：并发抓取所有平台
    tasks = [_scrape_one_platform(p, filters, semaphore) for p in platforms]
    scrape_results = await asyncio.gather(*tasks, return_exceptions=False)

    # 阶段 2：串行入库（M-7：共用一个 session，统一事务）
    total_collected = 0
    total_inserted = 0
    total_duplicates = 0
    total_errors = 0
    per_platform: list[dict[str, Any]] = []

    async with AsyncSessionLocal() as db:
        for r in scrape_results:
            platform = r["platform"]
            if "error" in r:
                per_platform.append({"platform": platform, "error": r["error"]})
                continue

            # M-1 修复（第五轮）：用 SAVEPOINT 实现部分成功语义
            # - ingest_scrape_result 内部 flush 失败会抛 IntegrityError，事务进入 poisoned 状态
            # - 如果直接 rollback 会清掉前面平台已 add 的数据（原 bug）
            # - 如果不 rollback 后续平台操作会全部失败
            # - SAVEPOINT（begin_nested）只回滚当前平台，保留外层事务
            try:
                async with db.begin_nested():
                    ingest = await ingest_scrape_result(
                        scrape_result=r["result"],
                        template=platform,
                        simhash_computer=compute_simhash,
                        db=db,
                    )
                total_collected += ingest["total"]
                total_inserted += ingest["inserted"]
                total_duplicates += ingest["duplicates"]
                total_errors += ingest["errors"]
                per_platform.append({
                    "platform": platform,
                    "collected": ingest["total"],
                    "inserted": ingest["inserted"],
                    "duplicates": ingest["duplicates"],
                })
            except Exception as exc:  # noqa: BLE001
                # begin_nested 异常时 savepoint 已自动回滚，外层事务仍可用
                logger.exception("ingest failed platform=%s", platform)
                per_platform.append({"platform": platform, "error": str(exc)})

        # M-7：所有平台统一 commit（部分成功语义：成功的平台数据落盘）
        await db.commit()

    return {
        "total": total_collected,
        "inserted": total_inserted,
        "duplicates": total_duplicates,
        "errors": total_errors,
        "per_platform": per_platform,
    }
