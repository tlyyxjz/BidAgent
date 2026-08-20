"""实时采集面板 API（大件二·省级组网演示）.

挂到 real_demo.router（/api/real）下的子模块：
- GET  /api/real/realtime/sources  源清单+库内计数（零网络，离线可渲染）
- POST /api/real/realtime/collect  触发一次真实采集（collect_realtime →
  串行入库），逐源返回 status/fetched/ingested/elapsed_ms/error。

合规纪律全部复用既有机制：robots 先行 + 8s 域限流 + 403 熔断 +
单源失败降级（BaseSourceAdapter / collect_realtime 内硬编码），
本端点不新增任何绕过路径。入库串行执行（避免 SQLite 并发写锁）。
"""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import get_db
from app.models.tender import Tender
from app.services.realtime_sources import (
    DEFAULT_SOURCES,
    collect_realtime,
    resolve_adapters,
)
from app.utils.logger import get_logger

logger = get_logger("api.realtime_panel")

# router 与 _ok 由主模块注入（子模块注册模式，见 real_demo.py 底部）
from app.api.real_demo import _ok, router  # noqa: E402


async def _db_counts(db: AsyncSession) -> dict[str, dict]:
    """各 source_platform 的库内条数与最新入库时间。"""
    rows = (await db.execute(
        select(Tender.source_platform, func.count(Tender.id),
               func.max(Tender.created_at))
        .group_by(Tender.source_platform))).all()
    return {p: {"count": n, "last_created": lc.isoformat() if lc else None}
            for p, n, lc in rows}


@router.get("/realtime/sources")
async def realtime_sources(db: AsyncSession = Depends(get_db)):
    """源清单（离线安全）：注册表元数据 + 库内计数。"""
    counts = await _db_counts(db)
    adapters = resolve_adapters()
    sources = []
    for a in adapters:
        c = counts.get(a.source, {})
        sources.append({
            "source": a.source,
            "display_name": a.display_name,
            "domain": a.domain,
            "db_count": c.get("count", 0),
            "last_created": c.get("last_created"),
        })
    return _ok({
        "default_sources": list(DEFAULT_SOURCES),
        "sources": sources,
        "total_db": sum(s["db_count"] for s in sources),
    })


@router.post("/realtime/collect")
async def realtime_collect(limit: int = 3, db: AsyncSession = Depends(get_db)):
    """触发一次真实采集并串行入库。

    limit: 每源最多入库条数（默认 3，演示口径；上限 10 防止误用）。
    逐源返回结果，前端渲染状态灯（成功条数/耗时/失败原因明示）。
    """
    from app.core.robots_checker import robots_checker

    limit = max(1, min(int(limit), 10))
    adapters = resolve_adapters()
    outcome = await collect_realtime(adapters, limit=limit,
                                     robots_checker=robots_checker)

    # 串行入库（避免 SQLite 并发写锁），按 source_url 去重
    results: dict[str, dict] = {}
    for r in outcome["results"]:
        a = next((x for x in adapters if x.source == r.source), None)
        results[r.source] = {
            "source": r.source,
            "display_name": a.display_name if a else r.source,
            "status": r.status,
            "ok": r.ok,
            "fetched": r.fetched,
            "ingested": 0,
            "skipped": 0,
            "elapsed_ms": r.elapsed_ms,
            "error": r.error,
        }

    for r in outcome["results"]:
        if not r.payloads:
            continue
        added = skipped = 0
        for p in r.payloads:
            exists = (await db.execute(
                select(Tender.id).where(Tender.source_url == p["source_url"]))
            ).first()
            if exists:
                skipped += 1
                continue
            db.add(Tender(**p))
            added += 1
        await db.commit()
        results[r.source]["ingested"] = added
        results[r.source]["skipped"] = skipped

    counts = await _db_counts(db)
    logger.info("realtime collect done ok_count=%s total_payloads=%s",
                outcome["ok_count"], outcome["total_payloads"])
    return _ok({
        "ok_count": outcome["ok_count"],
        "total_payloads": outcome["total_payloads"],
        "results": list(results.values()),
        "total_db": sum(c["count"] for c in counts.values()),
    })
