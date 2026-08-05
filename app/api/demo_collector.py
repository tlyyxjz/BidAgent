"""W3 Demo 采集进度聚合端点（任务1）。

提供：
- GET /api/demo/collector/status  返回采集进度聚合数据（工作台首页采集进度卡片）

实现策略（按优先级 fallback，**不 mock**）：
1. ScrapeJob 表统计活跃任务数（pending+running）、今日失败数
2. Tender 表统计今日采集量、按平台分布（按 source_platform 模糊匹配 4 个固定平台）
3. ScrapeJob 表查询最近 5 个完成批次，从 result_data.ingest 中提取 inserted/duplicates
4. 任一数据不可用时返回 0/idle，不返回 mock 数据

注：demo_collector_status 内的 DB/模型 import 保持函数内局部 import（与原 demo_api.py 一致）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import get_db
from app.models.tender import Tender

router = APIRouter(tags=["demo"])

# 4 个固定采集平台（与 collector_agent.py 保持一致）
_COLLECTOR_PLATFORMS: list[dict] = [
    {
        "code": "ccgp",
        "name": "中国政府采购网",
        "patterns": ["ccgp", "中国政府采购"],
    },
    {
        "code": "chinabidding",
        "name": "中国招标投标公共服务平台",
        "patterns": ["chinabidding", "cebpubservice", "招标投标公共服"],
    },
    {
        "code": "ggzy",
        "name": "全国公共资源交易平台",
        "patterns": ["ggzy", "公共资源交易"],
    },
    {
        "code": "qlm",
        "name": "千里马招标网",
        "patterns": ["千里马", "qlm", "qianlima"],
    },
]


def _match_platform_code(text: str | None) -> str | None:
    """根据 URL 或平台名称匹配 4 个固定平台之一，未匹配返回 None。"""
    if not text:
        return None
    s = str(text).lower()
    for p in _COLLECTOR_PLATFORMS:
        for pat in p["patterns"]:
            if pat.lower() in s:
                return p["code"]
    return None


def _safe_json_loads(raw: str | None) -> dict | None:
    """安全解析 JSON 字符串，失败返回 None。"""
    if not raw:
        return None
    try:
        import json as _json
        data = _json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


@router.get("/collector/status", summary="采集进度聚合数据（工作台首页采集进度卡片）")
async def demo_collector_status(db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """任务1：返回采集进度聚合数据。

    实现策略（按优先级 fallback，**不 mock**）：
    1. ScrapeJob 表统计活跃任务数（pending+running）、今日失败数
    2. Tender 表统计今日采集量、按平台分布（按 source_platform 模糊匹配 4 个固定平台）
    3. ScrapeJob 表查询最近 5 个完成批次，从 result_data.ingest 中提取 inserted/duplicates
    4. 任一数据不可用时返回 0/idle，不返回 mock 数据
    """
    from datetime import datetime as _dt
    from sqlalchemy import and_
    from app.models.job import (
        ScrapeJob,
        JOB_RUNNING,
        JOB_PENDING,
        JOB_FAILED,
        JOB_COMPLETED,
    )

    today_start = _dt.now().replace(hour=0, minute=0, second=0, microsecond=0)

    # ===== 1. 活跃任务数（pending + running）=====
    active_jobs = 0
    try:
        result = await db.execute(
            select(func.count(ScrapeJob.id)).where(
                ScrapeJob.status.in_([JOB_PENDING, JOB_RUNNING])
            )
        )
        active_jobs = int(result.scalar() or 0)
    except Exception:
        active_jobs = 0

    # ===== 2. 今日采集量（从 Tender 表 created_at >= 今日 0 点）=====
    today_collected = 0
    try:
        result = await db.execute(
            select(func.count(Tender.id)).where(Tender.created_at >= today_start)
        )
        today_collected = int(result.scalar() or 0)
    except Exception:
        today_collected = 0

    # ===== 3. 今日失败数（ScrapeJob.status=failed 且 created_at >= 今日 0 点）=====
    today_failed = 0
    try:
        result = await db.execute(
            select(func.count(ScrapeJob.id)).where(
                and_(
                    ScrapeJob.status == JOB_FAILED,
                    ScrapeJob.created_at >= today_start,
                )
            )
        )
        today_failed = int(result.scalar() or 0)
    except Exception:
        today_failed = 0

    # ===== 4. 今日去重数（从今日完成的 ScrapeJob.result_data.ingest.duplicates 汇总）=====
    today_deduplicated = 0
    try:
        result = await db.execute(
            select(ScrapeJob).where(
                and_(
                    ScrapeJob.status == JOB_COMPLETED,
                    ScrapeJob.completed_at >= today_start,
                )
            ).order_by(ScrapeJob.completed_at.desc()).limit(50)
        )
        today_completed_jobs = result.scalars().all()
        dup_sum = 0
        for job in today_completed_jobs:
            data = _safe_json_loads(job.result_data)
            if not data:
                continue
            ingest = data.get("ingest")
            if isinstance(ingest, dict):
                dup_sum += int(ingest.get("duplicates", 0) or 0)
            else:
                # 兼容 collect_summary 顶层字段
                dup_sum += int(data.get("duplicates", 0) or 0)
        today_deduplicated = dup_sum
    except Exception:
        today_deduplicated = 0

    # ===== 5. 4 个平台状态（默认 idle）=====
    platforms_status: dict[str, dict] = {
        p["code"]: {
            "name": p["name"],
            "code": p["code"],
            "status": "idle",
            "collected": 0,
            "last_fetch": None,
            "failed_count": 0,
        }
        for p in _COLLECTOR_PLATFORMS
    }

    # 5a. 从 Tender 表按 source_platform 聚合今日采集量
    try:
        result = await db.execute(
            select(
                Tender.source_platform,
                func.count(Tender.id),
                func.max(Tender.created_at),
            )
            .where(Tender.created_at >= today_start)
            .group_by(Tender.source_platform)
        )
        for row in result:
            sp_name, cnt, last_fetch = row
            code = _match_platform_code(sp_name or "")
            if not code:
                continue
            platforms_status[code]["collected"] += int(cnt or 0)
            lf_iso = last_fetch.isoformat() if last_fetch else None
            if lf_iso and (
                platforms_status[code]["last_fetch"] is None
                or lf_iso > platforms_status[code]["last_fetch"]
            ):
                platforms_status[code]["last_fetch"] = lf_iso
    except Exception:
        pass

    # 5b. 从今日 ScrapeJob 推断每个平台的 running/failed 状态、失败数、最后抓取时间
    try:
        result = await db.execute(
            select(ScrapeJob).where(ScrapeJob.created_at >= today_start)
        )
        today_jobs = result.scalars().all()
        for job in today_jobs:
            url = job.url or ""
            if not url and job.request_data:
                req = _safe_json_loads(job.request_data)
                if req:
                    url = req.get("url", "") or ""
            code = _match_platform_code(url)
            if not code:
                continue
            if job.status == JOB_FAILED:
                platforms_status[code]["failed_count"] += 1
                if platforms_status[code]["status"] != "running":
                    platforms_status[code]["status"] = "failed"
            elif job.status in (JOB_RUNNING, JOB_PENDING):
                platforms_status[code]["status"] = "running"
            elif job.status == JOB_COMPLETED and job.completed_at:
                lf_iso = job.completed_at.isoformat()
                if (
                    platforms_status[code]["last_fetch"] is None
                    or lf_iso > platforms_status[code]["last_fetch"]
                ):
                    platforms_status[code]["last_fetch"] = lf_iso
    except Exception:
        pass

    platforms = list(platforms_status.values())

    # ===== 6. 最近 5 个采集批次（已完成 ScrapeJob，按 completed_at 倒序）=====
    recent_batches: list[dict] = []
    try:
        result = await db.execute(
            select(ScrapeJob)
            .where(ScrapeJob.status == JOB_COMPLETED)
            .order_by(ScrapeJob.completed_at.desc())
            .limit(5)
        )
        for job in result.scalars():
            inserted = 0
            duplicates = 0
            platforms_count = 1
            data = _safe_json_loads(job.result_data)
            if data:
                ingest = data.get("ingest")
                if isinstance(ingest, dict):
                    inserted = int(ingest.get("inserted", 0) or 0)
                    duplicates = int(ingest.get("duplicates", 0) or 0)
                    pcs = ingest.get("platforms_collected") or []
                    if isinstance(pcs, list) and pcs:
                        platforms_count = len(pcs)
                # 兼容 collect_summary 顶层字段
                if not inserted and "inserted" in data:
                    inserted = int(data.get("inserted", 0) or 0)
                if not duplicates and "duplicates" in data:
                    duplicates = int(data.get("duplicates", 0) or 0)
                if not inserted and "total" in data:
                    # fallback：用 total - duplicates 估算 inserted
                    total_v = int(data.get("total", 0) or 0)
                    if total_v and not inserted:
                        inserted = max(0, total_v - duplicates)
            batch_time = (
                job.completed_at.isoformat() if job.completed_at
                else (job.created_at.isoformat() if job.created_at else None)
            )
            recent_batches.append({
                "batch_id": job.id,
                "time": batch_time,
                "inserted": max(0, inserted),
                "duplicates": max(0, duplicates),
                "platforms": platforms_count,
            })
    except Exception:
        recent_batches = []

    return JSONResponse(content={
        "code": 200,
        "data": {
            "active_jobs": active_jobs,
            "today_collected": today_collected,
            "today_deduplicated": today_deduplicated,
            "today_failed": today_failed,
            "platforms": platforms,
            "recent_batches": recent_batches,
        },
        "msg": "ok",
    })
