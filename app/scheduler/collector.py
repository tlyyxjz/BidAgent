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
S19 ccgp 加固：退避重试+熔断冷却+断点续采+降级编排（纯函数层 ccgp_resilience.py）。
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

# S19 加固：重试等待钩子（测试可 monkeypatch 为 no-op，避免真等）
_retry_sleep = asyncio.sleep


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
    """抓取单平台（S19 加固：退避重试 + 熔断冷却 + 断点续采）。

    新-2 修复：只负责抓取，不入库（入库由 collect_new_tenders 串行执行）。
    S19 加固纪律：冷却中的平台零请求（不锤墙）；retryable 错误指数退避
    重试（默认共 3 次）；403/被拦记熔断并零重试；fatal（robots/SSRF/
    不支持）立即失败且不计入熔断（政策不是故障）。

    Returns:
        {"platform": str, "result": scrape_result_dict} 或 {"platform": str, "error": str}
    """
    async with semaphore:
        from app.core.scraper import ScrapeError, scraper
        from app.scheduler import ccgp_resilience as res

        request = build_scrape_request(platform, filters)
        if request is None:
            return {"platform": platform, "error": "unsupported platform"}

        breaker = res.get_breaker()
        cooldown = breaker.cooldown_seconds(platform)
        if cooldown > 0:
            logger.info(
                "breaker cooldown platform=%s remain=%.0fs", platform, cooldown,
            )
            return {
                "platform": platform,
                "error": f"熔断冷却中（剩余 {cooldown:.0f}s）",
            }

        policy = res.RetryPolicy()
        delays = res.backoff_delays(policy)
        last_error: str | None = None
        record_as_failure = True  # fatal（政策拒绝）不计入熔断
        for attempt in range(policy.max_attempts):
            try:
                result = await scraper.scrape(request)
                breaker.record_success(platform)
                return {"platform": platform, "result": result}
            except ScrapeError as exc:
                last_error = str(exc)
                kind = res.classify_failure(last_error)
                if kind == "blocked":
                    logger.warning(
                        "collect blocked platform=%s err=%s（熔断，零重试）",
                        platform, exc,
                    )
                    break
                if kind == "fatal":
                    logger.warning("collect fatal platform=%s err=%s", platform, exc)
                    record_as_failure = False
                    break
                if attempt >= policy.max_attempts - 1:
                    break
                wait = res.add_jitter(
                    delays[min(attempt, len(delays) - 1)], policy.jitter,
                )
                logger.info(
                    "collect retry platform=%s attempt=%d/%d wait=%.1fs err=%s",
                    platform, attempt + 1, policy.max_attempts, wait, exc,
                )
                await _retry_sleep(wait)
            except Exception as exc:  # noqa: BLE001
                logger.exception("collect unexpected error platform=%s", platform)
                last_error = str(exc)
                break

        if record_as_failure:
            breaker.record_failure(platform, error=last_error)
        return {"platform": platform, "error": last_error or "unknown error"}


async def collect_new_tenders(
    sub: Subscription, filters: ParsedFilters
) -> dict[str, Any]:
    """主动采集新数据入库（命题硬要求：采集 → 入库 → 推送 完整链路）。

    M-6 修复：多平台并发抓取（asyncio.gather + Semaphore）。
    新-2 修复：抓取并发，入库串行（避免 SQLite database is locked）。
    M-7 修复：所有平台共用一个 session，统一事务边界（一损俱损一荣俱荣）。
    失败时只记录日志，不阻塞推送流程（数据库里已有旧数据可推送）。
    S19 加固：ccgp 主源失败后分层降级——省级实时源兜底采集，
    payload 按 source_url 去重入库（同 realtime_panel 纪律）。

    Returns:
        采集摘要 {"total": N, "inserted": N, "duplicates": N, "errors": N,
        "fallback_ingested": N}
    """
    from app.models.database import AsyncSessionLocal
    from app.processors.simhash import compute_simhash
    from app.processors.tender_ingestor import ingest_scrape_result
    from app.scheduler import ccgp_resilience as res

    platforms = sub.platforms or ["ccgp"]
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_PLATFORMS)

    # 阶段 1：并发抓取所有平台（内置退避重试与熔断冷却）
    tasks = [_scrape_one_platform(p, filters, semaphore) for p in platforms]
    scrape_results = await asyncio.gather(*tasks, return_exceptions=False)

    # S19 分层降级编排：ccgp 主源失败 → 省级实时源兜底
    fallback_payloads: list[dict[str, Any]] = []
    if any(r["platform"] == "ccgp" and "error" in r for r in scrape_results):
        logger.warning("ccgp 主源失败，触发省级实时源降级兜底采集")
        try:
            fallback_payloads = await res.run_fallback_collect(limit=3)
        except Exception as exc:  # noqa: BLE001 - 兜底失败不得阻塞推送
            logger.exception("fallback collect failed: %s", exc)

    # 阶段 2：串行入库（M-7：共用一个 session，统一事务）
    total_collected = total_inserted = total_duplicates = total_errors = 0
    fallback_ingested = 0
    fallback_skipped = 0
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

        # S19 加固：降级兜底 payload 入库（source_url 去重，同 realtime_panel 纪律）
        if fallback_payloads:
            from sqlalchemy import select

            from app.models.tender import Tender

            for p in fallback_payloads:
                exists = (await db.execute(
                    select(Tender.id).where(Tender.source_url == p["source_url"]))
                ).first()
                if exists:
                    fallback_skipped += 1
                    continue
                db.add(Tender(**p))
                fallback_ingested += 1
            logger.info(
                "fallback ingested=%d skipped=%d", fallback_ingested, fallback_skipped,
            )

        # M-7：所有平台统一 commit（部分成功语义：成功的平台数据落盘）
        await db.commit()

    return {
        "total": total_collected,
        "inserted": total_inserted,
        "duplicates": total_duplicates,
        "errors": total_errors,
        "per_platform": per_platform,
        "fallback_ingested": fallback_ingested,
        "fallback_skipped": fallback_skipped,
    }
