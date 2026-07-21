"""RQ 任务队列（Redis Queue）异步任务。

工程规范：
- batch 抓取任务入队，立即返回 job_id 供轮询。
- Redis 不可用时 fallback 到线程池同步执行（保证 MVP/测试可运行）。
- job_id 用 UUID 字符串，与 ScrapeJob 表主键一致。
- 状态机: pending -> running -> completed | failed
- RQ 的 job 函数本身是同步的，内部用 asyncio.run() 调用 async 抓取逻辑。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy import select, update

from app.config import settings
from app.core.scraper import ScrapeError, scraper
from app.models.database import AsyncSessionLocal
from app.models.job import (
    JOB_COMPLETED,
    JOB_FAILED,
    JOB_PENDING,
    JOB_RUNNING,
    ScrapeJob,
)
from app.models.user import utc_now
from app.utils.logger import get_logger

logger = get_logger("queue")


# 单独的线程池：fallback 同步执行用
_executor: ThreadPoolExecutor | None = None


def _get_executor() -> ThreadPoolExecutor:
    """惰性创建线程池（fallback 模式用）。"""
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="scrapeflow")
    return _executor


async def create_job(
    user_id: int,
    url: str | None,
    request_data: dict[str, Any],
) -> str:
    """在数据库中创建 pending 任务，返回 job_id。"""
    job_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as session:
        job = ScrapeJob(
            id=job_id,
            user_id=user_id,
            url=url,
            status=JOB_PENDING,
            request_data=json.dumps(request_data, ensure_ascii=False),
        )
        session.add(job)
        await session.commit()
    logger.info("job created job_id=%s user_id=%d url=%s", job_id, user_id, url)
    return job_id


async def enqueue_scrape_job(
    user_id: int,
    request_data: dict[str, Any],
) -> str:
    """创建并入队一个抓取任务。

    优先用 RQ；Redis 不可用时 fallback 到线程池。

    Args:
        user_id: 用户 ID。
        request_data: 抓取请求字典（含 url / selectors / template 等）。

    Returns:
        job_id (UUID 字符串)。
    """
    url = request_data.get("url")
    job_id = await create_job(user_id, url, request_data)

    try:
        from rq import Queue  # type: ignore[import-not-found]

        conn = _get_redis_connection()
        if conn is not None:
            q = Queue("scrapeflow", connection=conn)
            q.enqueue(
                "app.core.queue.run_scrape_job_sync",
                job_id,
                request_data,
                job_timeout=600,
                result_ttl=86400,
            )
            logger.info("job enqueued to RQ job_id=%s", job_id)
            return job_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("RQ 入队失败，fallback 到线程池: %s", exc)

    # Fallback：在线程池里跑（不阻塞事件循环）
    loop = asyncio.get_running_loop()
    loop.run_in_executor(
        _get_executor(),
        run_scrape_job_sync,
        job_id,
        request_data,
    )
    logger.info("job scheduled in thread pool job_id=%s", job_id)
    return job_id


def _get_redis_connection() -> Any:
    """惰性创建 Redis 连接；失败返回 None。"""
    try:
        from redis import Redis  # type: ignore[import-not-found]

        client = Redis.from_url(
            settings.REDIS_URL, socket_connect_timeout=2
        )
        client.ping()
        return client
    except Exception as exc:  # noqa: BLE001
        logger.debug("Redis 不可用: %s", exc)
        return None


def run_scrape_job_sync(job_id: str, request_data: dict[str, Any]) -> None:
    """RQ 调用的同步入口；内部 asyncio.run 执行 async 抓取。

    注意：此函数会被 RQ worker 或线程池调用，不在事件循环中。
    """
    asyncio.run(_run_scrape_job_async(job_id, request_data))


async def _run_scrape_job_async(
    job_id: str,
    request_data: dict[str, Any]
) -> None:
    """实际执行抓取任务并更新数据库。"""
    logger.info("job started job_id=%s", job_id)

    async with AsyncSessionLocal() as session:
        # 标记 running
        await session.execute(
            update(ScrapeJob)
            .where(ScrapeJob.id == job_id)
            .values(status=JOB_RUNNING, started_at=utc_now())
        )
        await session.commit()

    try:
        result = await scraper.scrape(request_data)
        result_json = json.dumps(result, ensure_ascii=False, default=str)

        async with AsyncSessionLocal() as session:
            await session.execute(
                update(ScrapeJob)
                .where(ScrapeJob.id == job_id)
                .values(
                    status=JOB_COMPLETED,
                    result_data=result_json,
                    progress=100,
                    completed_at=utc_now(),
                )
            )
            await session.commit()
        logger.info("job completed job_id=%s", job_id)
    except ScrapeError as exc:
        await _mark_job_failed(job_id, str(exc))
        logger.warning("job failed (scrape) job_id=%s err=%s", job_id, exc)
    except Exception as exc:  # noqa: BLE001
        await _mark_job_failed(job_id, f"unexpected: {exc}")
        logger.exception("job failed (unexpected) job_id=%s", job_id)


async def _mark_job_failed(job_id: str, error_message: str) -> None:
    """标记任务失败。"""
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(ScrapeJob)
            .where(ScrapeJob.id == job_id)
            .values(
                status=JOB_FAILED,
                error_message=error_message,
                completed_at=utc_now(),
            )
        )
        await session.commit()


async def get_job_status(job_id: str) -> dict[str, Any] | None:
    """查询任务状态。

    Returns:
        任务状态字典；若 job_id 不存在返回 None。
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ScrapeJob).where(ScrapeJob.id == job_id)
        )
        job = result.scalar_one_or_none()
        if job is None:
            return None

        response: dict[str, Any] = {
            "job_id": job.id,
            "status": job.status,
            "url": job.url,
            "progress": job.progress,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "error": job.error_message,
        }
        if job.result_data:
            try:
                response["data"] = json.loads(job.result_data)
            except json.JSONDecodeError:
                response["data"] = None
        else:
            response["data"] = None
        return response
