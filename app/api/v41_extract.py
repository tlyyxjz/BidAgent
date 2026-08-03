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
"""
from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import datetime
from typing import Any

import httpx
from fastapi import Body, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.database import AsyncSessionLocal, get_db
from app.models.evidence import Evidence, ExtractedField, FieldEvidenceLink
from app.models.tender import Tender
from app.utils.logger import get_logger
from app.utils.url_safety import is_safe_url_async

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


def _ok(data: Any) -> JSONResponse:
    """统一成功响应。"""
    return JSONResponse({"code": 0, "data": data, "msg": "ok"})


def _err(msg: str, code: int = 404) -> JSONResponse:
    """统一错误响应。"""
    return JSONResponse({"code": code, "data": None, "msg": msg}, status_code=code)


async def _set_task_status(task_id: str, status: str, **extra: Any) -> None:
    """持锁更新任务状态与附加字段。"""
    async with _EXTRACT_TASKS_LOCK:
        task = _EXTRACT_TASKS.get(task_id)
        if task is None:
            return
        task["status"] = status
        task["updated_at"] = datetime.utcnow().isoformat()
        if status in ("succeeded", "partially_succeeded", "failed"):
            task["finished_at"] = datetime.utcnow().isoformat()
        elif status == "running":
            task["started_at"] = datetime.utcnow().isoformat()
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
    payload = payload or {}
    task_id = uuid.uuid4().hex
    now = datetime.utcnow().isoformat()
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


async def _build_result_from_tender(tender: Tender, db: AsyncSession) -> dict[str, Any]:
    """从 DB 中已存在的 Tender + ExtractedField + Evidence 组装结果。"""
    fields_rows = (await db.execute(
        select(ExtractedField).where(ExtractedField.tender_id == tender.id)
        .order_by(ExtractedField.id)
    )).scalars().all()

    fields_payload: list[dict[str, Any]] = []
    evidences_count = 0
    for f in fields_rows:
        links = (await db.execute(
            select(FieldEvidenceLink, Evidence)
            .join(Evidence, FieldEvidenceLink.evidence_id == Evidence.id)
            .where(FieldEvidenceLink.field_id == f.id)
            .order_by(FieldEvidenceLink.sequence)
        )).all()
        evidences = [{
            "evidence_id": f"{f.id}_{link.sequence}",
            "text": ev.evidence_text,
            "raw_start": ev.raw_start,
            "raw_end": ev.raw_end,
            "role": link.evidence_role,
            "match_method": ev.match_method,
            "confidence": ev.confidence,
            "verified": ev.verified,
        } for link, ev in links]
        evidences_count += len(evidences)
        fields_payload.append({
            "field_name": f.field_name,
            "field_status": f.field_status,
            "raw_value": f.raw_value,
            "normalized_value": f.normalized_value,
            "amount_type": f.amount_type,
            "support_level": f.support_level,
            "evidences": evidences,
        })

    # 即便无字段也算 succeeded（DB 命中但未抽取过字段，是真实状态）
    return {
        "_status": "succeeded",
        "_error": None,
        "tender_id": tender.id,
        "project_name": tender.project_name,
        "source_platform": tender.source_platform,
        "fields": fields_payload,
        "fields_count": len(fields_payload),
        "evidences_count": evidences_count,
        "extraction_source": "db_cached",
    }


def _extraction_to_payload(extraction: Any) -> list[dict[str, Any]]:
    """把 ExtractionResult 转为字段 payload（容错处理）。"""
    if extraction is None:
        return []
    fields = getattr(extraction, "fields", None)
    if fields is None and isinstance(extraction, dict):
        fields = extraction.get("fields")
    if not fields:
        return []
    payload = []
    for f in fields:
        if hasattr(f, "model_dump"):
            f = f.model_dump()
        if not isinstance(f, dict):
            continue
        payload.append({
            "field_name": f.get("field_name") or f.get("name") or "",
            "field_status": f.get("field_status") or "present",
            "raw_value": f.get("raw_value") or f.get("value"),
            "normalized_value": f.get("normalized_value"),
            "amount_type": f.get("amount_type"),
            "support_level": f.get("support_level") or "unsupported",
            "evidences": f.get("candidate_evidences") or f.get("evidences") or [],
        })
    return payload


# ==== 8. GET /api/organizations/search ====

async def organizations_search(
    keyword: str | None = Query(None, description="组织名关键词"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """搜索组织实体（v4.1 第12节）。聚合 purchaser_name/winner_name 及 Tender 三字段。"""
    org_count: dict[str, int] = {}
    # 从 ExtractedField 聚合
    rows = (await db.execute(
        select(ExtractedField.raw_value, ExtractedField.field_name,
               func.count(ExtractedField.id).label("cnt"))
        .where(ExtractedField.field_name.in_(["purchaser_name", "winner_name"]))
        .group_by(ExtractedField.raw_value, ExtractedField.field_name)
    )).all()
    for r in rows:
        if r.raw_value:
            org_count[r.raw_value] = org_count.get(r.raw_value, 0) + r.cnt
    # 从 Tender 表三字段聚合
    trows = (await db.execute(
        select(Tender.tender_org, Tender.win_company, Tender.agency)
    )).all()
    for tr in trows:
        for name in (tr.tender_org, tr.win_company, tr.agency):
            if name:
                org_count[name] = org_count.get(name, 0) + 1
    items = []
    for name, cnt in org_count.items():
        if keyword and keyword not in name:
            continue
        items.append({
            "org_id": f"org_{hashlib.sha1(name.encode('utf-8')).hexdigest()[:12]}",
            "org_name": name,
            "occurrence_count": cnt,
        })
    items.sort(key=lambda x: -x["occurrence_count"])
    total = len(items)
    page_items = items[(page - 1) * page_size: page * page_size]
    return _ok({"items": page_items, "total": total, "page": page, "page_size": page_size})


# ==== 9. GET /api/organizations/{org_id} ====

async def get_organization(org_id: str, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """获取组织实体公开活动画像（v4.1 第12节，调用 observation_signals 模块）。

    org_id 支持两种格式：
    - org_{sha1前12位}：通过哈希反查组织名
    - 直接传组织名：URL 编码后传入
    """
    org_name = await _resolve_org_name(org_id, db)
    if not org_name:
        return _err(f"组织 {org_id} 不存在")
    win_records = await _collect_org_records(org_name, db)
    from app.processors.observation_signals import analyze_observation_signals
    result = analyze_observation_signals(
        org_id=org_id, org_name=org_name, win_records=win_records,
    )
    signals_payload = [
        {"signal_name": s.signal_name, "observed_value": s.observed_value,
         "observation_period": s.observation_period, "coverage_note": s.coverage_note,
         "details": s.details, "disclaimer": s.disclaimer}
        for s in result.signals
    ]
    data_completeness = {
        "coverage_platforms": result.coverage_platforms,
        "coverage_time_range": result.coverage_time_range,
        "valid_notice_count": result.valid_notice_count,
        "entity_resolution_status": result.entity_resolution_status,
        "signal_caliber": result.signal_caliber,
    }
    profile_payload = None
    if result.profile is not None:
        prof = result.profile
        profile_payload = {
            "win_count": prof.win_count,
            "total_win_amount": prof.total_win_amount,
            "main_purchasers": prof.main_purchasers,
            "main_agencies": prof.main_agencies,
            "active_regions": prof.active_regions,
            "first_win_date": prof.first_win_date,
            "last_win_date": prof.last_win_date,
        }
    return _ok({
        "org_id": org_id,
        "org_name": org_name,
        "normalized_name": result.normalized_name,
        # 数据完整性字段（v4.1 第 9.4 节）：同时保留扁平字段以兼容现有测试
        "coverage_platforms": result.coverage_platforms,
        "coverage_time_range": result.coverage_time_range,
        "valid_notice_count": result.valid_notice_count,
        "entity_resolution_status": result.entity_resolution_status,
        "signal_caliber": result.signal_caliber,
        "data_completeness": data_completeness,
        "profile": profile_payload,
        "signals": signals_payload,
        "summary": result.summary,
        "analyzed_at": result.analyzed_at,
    })


async def _resolve_org_name(org_id: str, db: AsyncSession) -> str | None:
    """根据 org_id 反查组织名，支持哈希 ID 与直接名称两种格式。"""
    if org_id.startswith("org_") and len(org_id) == 16:
        rows = (await db.execute(
            select(ExtractedField.raw_value).where(
                ExtractedField.field_name.in_(["purchaser_name", "winner_name"])
            )
        )).scalars().all()
        trows = (await db.execute(
            select(Tender.tender_org, Tender.win_company, Tender.agency)
        )).all()
        names = set(n for n in rows if n)
        for tr in trows:
            for n in (tr.tender_org, tr.win_company, tr.agency):
                if n:
                    names.add(n)
        for name in names:
            if f"org_{hashlib.sha1(name.encode('utf-8')).hexdigest()[:12]}" == org_id:
                return name
        return None
    return org_id


async def _collect_org_records(org_name: str, db: AsyncSession) -> list[dict]:
    """收集某组织作为中标人的公开记录，用于 observation_signals 信号计算。"""
    rows = (await db.execute(
        select(ExtractedField, Tender)
        .join(Tender, ExtractedField.tender_id == Tender.id)
        .where(ExtractedField.field_name == "winner_name")
        .where(ExtractedField.raw_value == org_name)
    )).all()
    records = []
    for _f, t in rows:
        records.append({
            "purchaser": t.tender_org or "",
            "region": t.location or "",
            "win_amount": float(t.win_amount) if t.win_amount else 0,
            "win_date": t.publish_time.isoformat() if t.publish_time else "",
            "award_date": t.publish_time.isoformat() if t.publish_time else "",
            "source_platform": t.source_platform or "",
            "notice_title": t.project_name or "",
        })
    return records


# ==== 12. GET /api/stats/quality ====

async def get_stats_quality(db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """获取数据质量和评测统计（v4.1 第12节）。"""
    tender_count = (await db.execute(select(func.count(Tender.id)))).scalar() or 0
    field_count = (await db.execute(select(func.count(ExtractedField.id)))).scalar() or 0
    evidence_count = (await db.execute(select(func.count(Evidence.id)))).scalar() or 0
    sl_rows = (await db.execute(
        select(ExtractedField.support_level, func.count(ExtractedField.id))
        .group_by(ExtractedField.support_level)
    )).all()
    fs_rows = (await db.execute(
        select(ExtractedField.field_status, func.count(ExtractedField.id))
        .group_by(ExtractedField.field_status)
    )).all()
    mm_rows = (await db.execute(
        select(Evidence.match_method, func.count(Evidence.id))
        .group_by(Evidence.match_method)
    )).all()
    verified_count = (await db.execute(
        select(func.count(Evidence.id)).where(Evidence.verified.is_(True))
    )).scalar() or 0
    return _ok({
        "tender_count": tender_count,
        "field_count": field_count,
        "evidence_count": evidence_count,
        "verified_evidence_count": verified_count,
        "support_level_distribution": {r[0]: r[1] for r in sl_rows},
        "field_status_distribution": {r[0]: r[1] for r in fs_rows},
        "match_method_distribution": {r[0]: r[1] for r in mm_rows},
        "verification_rate": round(verified_count / evidence_count, 4) if evidence_count else 0.0,
        "server_time": datetime.utcnow().isoformat(),
    })
