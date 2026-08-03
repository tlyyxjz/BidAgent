"""v4.1 第12节标准 REST API 实现.

对应《标小智 项目总体规划 v4.1》第十二章定义的 12 个标准 REST API 端点。
数据源暂用现有 Tender + ExtractedField + Evidence 表（不强制四层实体）。

统一返回格式：{"code": 0, "data": ..., "msg": "ok"}。
单文件控制在 300 行以内，超出部分拆到 v41_extract.py。
"""
from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import get_db
from app.models.evidence import Evidence, ExtractedField, FieldEvidenceLink
from app.models.tender import Tender
from app.models.tender_project import NoticeSource, NoticeVersion
from app.api.v41_extract import _EXTRACT_TASKS  # noqa: F401  复用任务存储

router = APIRouter(prefix="/api", tags=["v41"])


def _ok(data: Any) -> JSONResponse:
    """统一成功响应。"""
    return JSONResponse({"code": 0, "data": data, "msg": "ok"})


def _err(msg: str, code: int = 404) -> JSONResponse:
    """统一错误响应。"""
    return JSONResponse({"code": code, "data": None, "msg": msg}, status_code=code)


def _parse_int_id(raw: str, field: str) -> int | None:
    """解析路径参数中的整数 ID，失败返回 None。"""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


# 1. GET /api/projects/search
@router.get("/projects/search")
async def projects_search(
    keyword: str | None = Query(None, description="项目名/编号关键词"),
    industry_category: str | None = Query(None, description="行业分类，按 notice_type 适配"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """搜索采购项目（v4.1 第12节）。"""
    stmt = select(Tender)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(Tender.project_name.like(like), Tender.bid_number.like(like)))
    if industry_category:
        stmt = stmt.where(Tender.notice_type == industry_category)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    rows = (await db.execute(
        stmt.order_by(Tender.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    items = [{
        "project_id": str(t.id),
        "canonical_name": t.project_name,
        "project_identifier": t.bid_number,
        "notice_type": t.notice_type,
        "publish_date": t.publish_time.isoformat() if t.publish_time else None,
        "source_platform": t.source_platform,
    } for t in rows]
    return _ok({"items": items, "total": total, "page": page, "page_size": page_size})


# 2. GET /api/projects/{project_id}
@router.get("/projects/{project_id}")
async def get_project(project_id: str, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """获取项目及公告生命周期（v4.1 第12节）。"""
    pid = _parse_int_id(project_id, "project_id")
    if pid is None:
        return _err(f"非法 project_id: {project_id}", 400)
    tender = (await db.execute(select(Tender).where(Tender.id == pid))).scalar_one_or_none()
    if not tender:
        return _err(f"项目 {project_id} 不存在")
    # 公告生命周期：现仅一条公告，扩展到四层实体后会有多个阶段
    lifecycle = [{
        "notice_id": str(tender.id),
        "notice_type": tender.notice_type or "tender",
        "title": tender.project_name,
        "publish_date": tender.publish_time.isoformat() if tender.publish_time else None,
        "status": "active",
    }]
    return _ok({
        "project_id": str(tender.id),
        "canonical_name": tender.project_name,
        "project_identifier": tender.bid_number,
        "lifecycle": lifecycle,
    })


# 3. GET /api/notices/{notice_id}
@router.get("/notices/{notice_id}")
async def get_notice(notice_id: str, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """获取公告详情（v4.1 第12节）。"""
    nid = _parse_int_id(notice_id, "notice_id")
    if nid is None:
        return _err(f"非法 notice_id: {notice_id}", 400)
    tender = (await db.execute(select(Tender).where(Tender.id == nid))).scalar_one_or_none()
    if not tender:
        return _err(f"公告 {notice_id} 不存在")
    return _ok({
        "notice_id": str(tender.id),
        "project_id": str(tender.id),
        "title": tender.project_name,
        "notice_type": tender.notice_type,
        "publish_date": tender.publish_time.isoformat() if tender.publish_time else None,
        "deadline": tender.deadline.isoformat() if tender.deadline else None,
        "source_platform": tender.source_platform,
        "source_url": tender.source_url,
        "core_content": tender.core_content,
    })


# 4. GET /api/notices/{notice_id}/sources
@router.get("/notices/{notice_id}/sources")
async def get_notice_sources(notice_id: str, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """获取来源页面和谱系（v4.1 第12节）。

    优先查 NoticeSource 表（真实四层实体）；未命中时调用
    source_lineage.judge_source_role 基于 URL 域名 + 内容实际计算来源质量。
    数据源标注：data_source 字段（notice_source_table / computed_by_source_lineage）。
    """
    nid = _parse_int_id(notice_id, "notice_id")
    if nid is None:
        return _err(f"非法 notice_id: {notice_id}", 400)
    tender = (await db.execute(select(Tender).where(Tender.id == nid))).scalar_one_or_none()
    if not tender:
        return _err(f"公告 {notice_id} 不存在")

    sources: list[dict] = []

    # 1. 优先查 NoticeSource 表（通过 source_url 关联）
    if tender.source_url:
        ns_rows = (await db.execute(
            select(NoticeSource).where(NoticeSource.source_url == tender.source_url)
        )).scalars().all()
        for ns in ns_rows:
            sources.append({
                "source_id": ns.notice_source_id,
                "source_url": ns.source_url,
                "source_platform": ns.source_platform,
                "publication_role": ns.publication_role,
                "source_quality": ns.source_quality,
                "quality_reason": ns.quality_reason or "",
                "first_seen_at": ns.first_seen_at.isoformat() if ns.first_seen_at else None,
                "data_source": "notice_source_table",
            })

    # 2. 未命中 NoticeSource → 调用 source_lineage.judge_source_role 实际计算
    if not sources:
        from app.processors.source_lineage import (
            judge_source_role, compute_source_group,
        )
        source_url = tender.source_url or ""
        content_text = tender.core_content or ""
        source_role, reason = judge_source_role(source_url, content_text=content_text)
        simhash_val = tender.simhash if tender.simhash is not None else 0
        source_group = (compute_source_group(source_url, simhash_val)
                        if source_url else f"grp_{tender.id}")
        sources.append({
            "source_id": f"src_{tender.id}",
            "source_url": source_url,
            "source_platform": tender.source_platform or "",
            "publication_role": source_role,
            "source_quality": source_role,
            "quality_reason": reason,
            "first_seen_at": tender.created_at.isoformat() if tender.created_at else None,
            "source_group": source_group,
            "data_source": "computed_by_source_lineage",
        })

    # 3. 构造 lineage（单源无转载链）
    first_source = sources[0]
    lineage = {
        "origin_source_id": first_source["source_id"],
        "repost_chain": [],
        "source_group": first_source.get("source_group", f"grp_{tender.id}"),
    }

    return _ok({"sources": sources, "lineage": lineage})


# 5. GET /api/notices/{notice_id}/participants
@router.get("/notices/{notice_id}/participants")
async def get_notice_participants(notice_id: str, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """获取公告参与方列表（v4.1 第12节）。"""
    nid = _parse_int_id(notice_id, "notice_id")
    if nid is None:
        return _err(f"非法 notice_id: {notice_id}", 400)
    tender = (await db.execute(select(Tender).where(Tender.id == nid))).scalar_one_or_none()
    if not tender:
        return _err(f"公告 {notice_id} 不存在")
    participants = []
    if tender.tender_org:
        participants.append({"role": "purchaser", "raw_name": tender.tender_org,
                             "normalized_name": tender.tender_org, "resolution_status": "resolved"})
    if tender.agency:
        participants.append({"role": "procuring_agency", "raw_name": tender.agency,
                             "normalized_name": tender.agency, "resolution_status": "resolved"})
    if tender.win_company:
        participants.append({"role": "winner", "raw_name": tender.win_company,
                             "normalized_name": tender.win_company, "resolution_status": "resolved"})
    return _ok({"participants": participants, "total": len(participants)})


# 6. GET /api/sources/{source_id}/versions
@router.get("/sources/{source_id}/versions")
async def get_source_versions(source_id: str, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """获取页面版本历史（v4.1 第12节）。

    source_id 支持两种格式：
    - ULID（26 字符）：直接对应 NoticeSource.notice_source_id，查 NoticeVersion 表
    - src_{tender_id}：兼容旧格式，先尝试通过 source_url 关联 NoticeSource，
      命中则查 NoticeVersion 表；未命中回退到 Tender 表 created_at/updated_at

    数据源标注：data_source 字段（notice_version_table / tender_fallback）。
    """
    # 1. ULID 格式：直接查 NoticeVersion 表
    if len(source_id) == 26 and not source_id.startswith("src_"):
        versions_rows = (await db.execute(
            select(NoticeVersion)
            .where(NoticeVersion.notice_source_id == source_id)
            .order_by(NoticeVersion.fetched_at.desc())
        )).scalars().all()
        if not versions_rows:
            return _err(f"来源 {source_id} 无版本记录")
        return _ok(_build_versions_payload_from_notice_versions(source_id, versions_rows))

    # 2. src_{tender_id} 兼容格式
    if not source_id.startswith("src_"):
        return _err(f"非法 source_id: {source_id}", 400)
    nid = _parse_int_id(source_id[4:], "source_id")
    if nid is None:
        return _err(f"非法 source_id: {source_id}", 400)
    tender = (await db.execute(select(Tender).where(Tender.id == nid))).scalar_one_or_none()
    if not tender:
        return _err(f"来源 {source_id} 不存在")

    # 2a. 尝试通过 source_url 关联 NoticeSource → NoticeVersion（真实四层实体）
    if tender.source_url:
        ns = (await db.execute(
            select(NoticeSource).where(NoticeSource.source_url == tender.source_url)
        )).scalar_one_or_none()
        if ns is not None:
            versions_rows = (await db.execute(
                select(NoticeVersion)
                .where(NoticeVersion.notice_source_id == ns.notice_source_id)
                .order_by(NoticeVersion.fetched_at.desc())
            )).scalars().all()
            if versions_rows:
                return _ok(_build_versions_payload_from_notice_versions(
                    ns.notice_source_id, versions_rows
                ))

    # 2b. 回退到 Tender 表（向后兼容，标注 data_source=tender_fallback）
    return _ok(_build_versions_payload_from_tender(tender))


def _build_versions_payload_from_notice_versions(
    source_id: str, versions_rows: list,
) -> dict:
    """从 NoticeVersion 表组装版本列表 payload。"""
    return {
        "source_id": source_id,
        "total_versions": len(versions_rows),
        "versions": [{
            "version_id": v.version_id,
            "fetched_at": v.fetched_at.isoformat() if v.fetched_at else None,
            "change_type": v.change_type,
            "content_sha256": v.content_sha256,
            "raw_text_sha256": v.raw_text_sha256,
            "http_status": v.http_status,
            "snapshot_path": v.snapshot_path,
            "previous_version_id": v.previous_version_id,
        } for v in versions_rows],
        "data_source": "notice_version_table",
    }


def _build_versions_payload_from_tender(tender) -> dict:
    """从 Tender 表组装版本列表 payload（向后兼容回退路径）。"""
    raw_text = tender.core_content or ""
    content_sha = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
    versions = [{
        "version_id": 1,
        "fetched_at": tender.created_at.isoformat() if tender.created_at else None,
        "change_type": "initial",
        "content_sha256": content_sha,
        "snapshot_path": None,
    }]
    if tender.updated_at and tender.created_at and tender.updated_at > tender.created_at:
        versions.insert(0, {
            "version_id": 2,
            "fetched_at": tender.updated_at.isoformat(),
            "change_type": "none",
            "content_sha256": content_sha,
            "snapshot_path": None,
        })
    return {
        "source_id": f"src_{tender.id}",
        "total_versions": len(versions),
        "versions": versions,
        "data_source": "tender_fallback",
    }


# 7. GET /api/fields/{field_id}
@router.get("/fields/{field_id}")
async def get_field(field_id: str, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """获取字段和全部证据（v4.1 第12节）。

    field_id 格式：{tender_id}_{field_name}，例如 1_amount。
    """
    try:
        tid_str, field_name = field_id.split("_", 1)
        tid = int(tid_str)
    except ValueError:
        return _err(f"非法 field_id: {field_id}", 400)
    db_fields = (await db.execute(
        select(ExtractedField).where(ExtractedField.tender_id == tid)
        .where(ExtractedField.field_name == field_name)
        .order_by(ExtractedField.id)
    )).scalars().all()
    if not db_fields:
        return _err(f"字段 {field_id} 不存在")
    values = []
    for vi, f in enumerate(db_fields):
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
        values.append({
            "value_id": f"{f.id}_{vi}",
            "raw_value": f.raw_value,
            "normalized_value": f.normalized_value,
            "amount_type": f.amount_type,
            # v4.1 sec 7.2: amount object new keys
            "original_unit": getattr(f, "original_unit", None),
            "tax_status": getattr(f, "tax_status", None),
            "display_precision": getattr(f, "display_precision", None),
            "evidences": evidences,
        })
    return _ok({
        "field_id": field_id,
        "field_name": field_name,
        "support_level": db_fields[0].support_level,
        "field_status": db_fields[0].field_status,
        # v4.1 sec 8: display_grade in API response
        "display_grade": getattr(db_fields[0], "display_grade", "review"),
        "values": values,
    })


# 8-12. 以下端点实现在 v41_extract.py，集中注册到同一 router
from app.api.v41_extract import (  # noqa: E402
    create_extract_task,
    get_extract_task,
    get_organization,
    get_stats_quality,
    organizations_search,
)

# 8. GET /api/organizations/search
router.add_api_route("/organizations/search", organizations_search, methods=["GET"])
# 9. GET /api/organizations/{org_id}
router.add_api_route("/organizations/{org_id}", get_organization, methods=["GET"])
# 10. POST /api/extract/tasks
router.add_api_route("/extract/tasks", create_extract_task, methods=["POST"])
# 11. GET /api/extract/tasks/{task_id}
router.add_api_route("/extract/tasks/{task_id}", get_extract_task, methods=["GET"])
# 12. GET /api/stats/quality
router.add_api_route("/stats/quality", get_stats_quality, methods=["GET"])
