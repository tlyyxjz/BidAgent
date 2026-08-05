"""v4.1 第12节异步抽取任务、组织画像与质量统计端点.

提供端点：
- POST /api/extract/tasks 与 GET /api/extract/tasks/{task_id}（异步抽取任务）
- GET /api/organizations/search 与 GET /api/organizations/{org_id}（组织画像）
- GET /api/stats/quality（数据质量统计）

任务状态机：queued -> running -> partially_succeeded/succeeded/failed。
任务存储采用进程内字典（_EXTRACT_TASKS），由后台 asyncio task 驱动状态流转：
- create_extract_task 创建任务后立即返回 queued
- _run_extract_task 后台执行真实抽取流程并更新状态
- 优先复用 DB 中已存在的 Tender + ExtractedField（不依赖 LLM API key）
- DB 未命中时尝试 httpx 抓取 source_url + LLM 抽取（需配置 DEEPSEEK_API_KEY）
生产环境应替换为 Redis / DB 队列 + 独立 worker 进程。

本文件承接 v41_api.py 拆分出的端点，保证单文件 ≤300 行。
端点函数由 v41_api.py 集中注册到同一 router。

拆分说明（保证单文件 ≤300 行，公开接口不变）：
- 共享响应助手 _ok/_err → v41_common.py
- 抽取结果组装 _build_result_from_tender / _extraction_to_payload → v41_extract_worker.py
- 组织画像端点 → v41_organizations.py
- 质量统计端点 → v41_stats.py
- 本文件保留：抽取任务状态机（_EXTRACT_TASKS/_set_task_status）、
  create_extract_task / get_extract_task 端点、_run_extract_task / _do_extract 主流程，
  并 re-export 其余端点与辅助函数以保持 import 路径不变。
"""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import Body
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.config import settings
from app.models.database import AsyncSessionLocal
from app.models.tender import Tender
from app.utils.logger import get_logger
from app.utils.url_safety import is_safe_url_async

from app.api.v41_common import _err, _ok
from app.api.v41_extract_worker import _build_result_from_tender, _extraction_to_payload  # noqa: F401
from app.api.v41_organizations import (  # noqa: F401
    _collect_org_records,
    _resolve_org_name,
    get_organization,
    organizations_search,
)
from app.api.v41_stats import get_stats_quality  # noqa: F401

logger = get_logger("v41.extract")

# 异步抽取任务内存存储：task_id -> 任务详情
_EXTRACT_TASKS: dict[str, dict[str, Any]] = {}
# 任务状态变更锁（保证并发安全）
_EXTRACT_TASKS_LOCK = asyncio.Lock()

# 任务状态枚举（v4.1 第12节）
TASK_STATUSES = ("queued", "running", "partially_succeeded", "succeeded", "failed")

# 后台 worker 启动前等待时间（让 POST 响应先返回，避免轮询测试 race condition）
_WORKER_START_DELAY = 0.1
# httpx 抓取超时（秒）
_FETCH_TIMEOUT = 15.0

# BE-C3: 已完成任务 TTL（秒），超过后惰性清理
_TASK_TTL = 3600  # 1 小时


def _cleanup_old_tasks() -> None:
    """清理超过 TTL 的已完成任务（惰性清理，在创建新任务时调用）。

    仅删除 status 为 succeeded/partially_succeeded/failed 且 completed_at
    距今超过 _TASK_TTL 秒的任务，未完成或刚完成的任务不受影响。
    """
    now = time.time()
    expired = [
        tid for tid, task in _EXTRACT_TASKS.items()
        if task.get("status") in ("succeeded", "partially_succeeded", "failed")
        and task.get("completed_at")
        and now - task["completed_at"] > _TASK_TTL
    ]
    for tid in expired:
        _EXTRACT_TASKS.pop(tid, None)


async def _set_task_status(task_id: str, status: str, **extra: Any) -> None:
    """持锁更新任务状态与附加字段。"""
    async with _EXTRACT_TASKS_LOCK:
        task = _EXTRACT_TASKS.get(task_id)
        if task is None:
            return
        task["status"] = status
        task["updated_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        if status in ("succeeded", "partially_succeeded", "failed"):
            task["finished_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            # BE-C3: 记录完成时间戳（数值，供 TTL 清理使用）
            task["completed_at"] = time.time()
        elif status == "running":
            task["started_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        for k, v in extra.items():
            task[k] = v


# ==== 10. POST /api/extract/tasks ====

async def create_extract_task(payload: dict = Body(default={})) -> JSONResponse:
    """提交异步抽取任务（v4.1 第12节）。

    请求体（可选）:
        source_url: 待抽取来源 URL
        source_platform: 来源平台代码
        tender_id: 直接指定已存在的 Tender ID（优先于 source_url 查 DB）
        options: 其他抽取选项

    返回 task_id 与初始状态 queued。
    后台 asyncio task 异步执行 _run_extract_task 流转状态。
    """
    # BE-C3: 惰性清理过期已完成任务
    _cleanup_old_tasks()
    payload = payload or {}
    task_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    task = {
        "task_id": task_id,
        "status": "queued",
        "submitted_at": now,
        "started_at": None,
        "finished_at": None,
        "updated_at": now,
        "source_url": payload.get("source_url", ""),
        "source_platform": payload.get("source_platform", ""),
        "tender_id": payload.get("tender_id"),
        "options": payload.get("options", {}),
        "result": None,
        "error": None,
    }
    async with _EXTRACT_TASKS_LOCK:
        _EXTRACT_TASKS[task_id] = task
    # 启动后台 worker（不阻塞响应）
    asyncio.create_task(_run_extract_task(task_id))
    logger.info("extract task created task_id={} source_url={}", task_id, task["source_url"])
    return _ok(task)


# ==== 11. GET /api/extract/tasks/{task_id} ====

async def get_extract_task(task_id: str) -> JSONResponse:
    """查询任务状态（v4.1 第12节）。

    返回 queued/running/partially_succeeded/succeeded/failed 之一。
    """
    async with _EXTRACT_TASKS_LOCK:
        task = _EXTRACT_TASKS.get(task_id)
        if not task:
            return _err(f"任务 {task_id} 不存在")
        return _ok(dict(task))


# ==== 后台 worker：真实抽取流程 ====

async def _run_extract_task(task_id: str) -> None:
    """后台执行抽取任务，流转状态 queued -> running -> succeeded/failed。

    抽取优先级（做实不做虚）：
    1. tender_id 指定 → 直接从 DB 取 Tender
    2. source_url 命中 DB Tender → 复用其 core_content 与已抽取字段
    3. source_url 未命中且为 http(s) → SSRF 校验后 httpx 抓取
       - 抓到文本且配置 DEEPSEEK_API_KEY → 调用 LLM 抽取
       - 否则 failed（明确说明原因，不造假）
    """
    # 让 POST 响应先返回，避免轮询测试 race condition
    await asyncio.sleep(_WORKER_START_DELAY)

    async with _EXTRACT_TASKS_LOCK:
        task = _EXTRACT_TASKS.get(task_id)
        if task is None:
            return
        source_url = task.get("source_url", "") or ""
        source_platform = task.get("source_platform", "") or ""
        tender_id_raw = task.get("tender_id")

    await _set_task_status(task_id, "running")

    try:
        result = await _do_extract(source_url, source_platform, tender_id_raw)
        status = result.get("_status", "succeeded")
        await _set_task_status(
            task_id,
            status,
            result=result,
            error=result.get("_error"),
        )
        logger.info("extract task done task_id={} status={} fields={}",
                    task_id, status, len(result.get("fields", [])))
    except Exception as exc:  # noqa: BLE001
        logger.exception("extract task failed task_id={}", task_id)
        await _set_task_status(task_id, "failed", error=str(exc), result=None)


async def _do_extract(
    source_url: str, source_platform: str, tender_id_raw: Any
) -> dict[str, Any]:
    """实际抽取逻辑，返回结果 dict（含 _status / _error 元字段）。"""
    # 1. 优先按 tender_id 查 DB
    tender = None
    async with AsyncSessionLocal() as db:
        if tender_id_raw is not None:
            try:
                tid = int(tender_id_raw)
            except (TypeError, ValueError):
                tid = None
            if tid is not None:
                tender = (await db.execute(
                    select(Tender).where(Tender.id == tid)
                )).scalar_one_or_none()
        # 2. 按 source_url 查 DB
        if tender is None and source_url:
            tender = (await db.execute(
                select(Tender).where(Tender.source_url == source_url)
            )).scalar_one_or_none()

        # 3. 命中 DB：复用已抽取字段
        if tender is not None:
            return await _build_result_from_tender(tender, db)

    # 4. 未命中 DB：尝试抓取 source_url
    if not source_url:
        return {
            "_status": "failed",
            "_error": "未提供 source_url 或 tender_id，无法抽取",
            "fields": [],
        }
    if not (source_url.startswith("http://") or source_url.startswith("https://")):
        return {
            "_status": "failed",
            "_error": f"source_url 非法协议: {source_url}",
            "fields": [],
        }

    # SSRF 校验（v4.1 第5.3节）
    safe, reason = await is_safe_url_async(source_url)
    if not safe:
        return {
            "_status": "failed",
            "_error": f"source_url SSRF 校验失败: {reason}",
            "fields": [],
        }

    # 抓取页面
    try:
        async with httpx.AsyncClient(
            timeout=_FETCH_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "biaoxiaozhi-extract/1.0"},
        ) as client:
            resp = await client.get(source_url)
            resp.raise_for_status()
            raw_text = resp.text
    except Exception as exc:  # noqa: BLE001
        return {
            "_status": "failed",
            "_error": f"抓取失败: {exc}",
            "fields": [],
        }

    if not raw_text or not raw_text.strip():
        return {
            "_status": "failed",
            "_error": "抓取到的内容为空",
            "fields": [],
        }

    # 调用 LLM 抽取（需要 API key）
    if not settings.DEEPSEEK_API_KEY:
        return {
            "_status": "failed",
            "_error": "未配置 DEEPSEEK_API_KEY，无法对新 URL 调用 LLM 抽取",
            "fields": [],
            "fetched_text_length": len(raw_text),
        }

    try:
        from app.llm.extractor import call_extraction_llm
        extraction = await call_extraction_llm(raw_text)
    except Exception as exc:  # noqa: BLE001
        return {
            "_status": "failed",
            "_error": f"LLM 抽取失败: {exc}",
            "fields": [],
            "fetched_text_length": len(raw_text),
        }

    fields_payload = _extraction_to_payload(extraction)
    return {
        "_status": "succeeded" if fields_payload else "failed",
        "_error": None if fields_payload else "LLM 抽取返回空字段",
        "fields": fields_payload,
        "fetched_text_length": len(raw_text),
        "extraction_source": "llm",
    }
