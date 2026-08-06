"""v4.1 第12节标准 REST API 实现.

对应《标小智 项目总体规划 v4.1》第十二章定义的 12 个标准 REST API 端点。
数据源优先四层实体表（tender_notices / notice_participants 等），
未命中时回退 Tender 扁平表并以 data_source 字段标注（与 v41_sources 同模式）。

统一返回格式：{"code": 0, "data": ..., "msg": "ok"}。
单文件控制在 300 行以内，超出部分拆到 v41_extract.py / v41_sources.py。

拆分说明（保证单文件 ≤300 行，公开接口不变）：
- 本文件保留：router 定义、_ok/_err/_parse_int_id 助手、
  projects_search / get_project / get_notice / get_notice_participants / get_field 端点，
  以及从 v41_extract 集中注册的端点 8-12。
- 来源与版本端点（get_notice_sources / get_source_versions）→ app/api/v41_sources.py
- 子模块在底部 import 时将各自路由注册到 router 上。
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
from app.models.tender_project import (
    NoticeParticipant,
    NoticeSource,
    NoticeVersion,
    TenderNotice,
)
from app.api.v41_extract import _EXTRACT_TASKS  # noqa: F401  复用任务存储
from app.api.auth import verify_api_key

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
@router.get("/projects/search", dependencies=[Depends(verify_api_key)])
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
@router.get("/projects/{project_id}", dependencies=[Depends(verify_api_key)])
async def get_project(project_id: str, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """获取项目及公告生命周期（v4.1 第12节）。"""
    pid = _parse_int_id(project_id, "project_id")
    if pid is None:
        return _err(f"非法 project_id: {project_id}", 400)
    tender = (await db.execute(select(Tender).where(Tender.id == pid))).scalar_one_or_none()
    if not tender:
        return _err(f"项目 {project_id} 不存在")

    # 公告生命周期：优先查四层实体（同项目下全部 TenderNotice）
    lifecycle: list[dict] = []
    data_source = "tender_fallback"
    source_url = tender.source_url or f"migrated://tender/{tender.id}"
    pid_row = (await db.execute(
        select(TenderNotice.project_id)
        .join(NoticeSource, NoticeSource.notice_id == TenderNotice.notice_id)
        .where(NoticeSource.source_url == source_url)
    )).first()
    if pid_row is not None:
        notices = (await db.execute(
            select(TenderNotice)
            .where(TenderNotice.project_id == pid_row[0])
            .order_by(TenderNotice.publish_date)
        )).scalars().all()
        lifecycle = [{
            "notice_id": n.notice_id,
            "notice_type": n.notice_type,
            "title": n.canonical_title,
            "publish_date": n.publish_date.isoformat() if n.publish_date else None,
            "status": n.status or "active",
        } for n in notices]
        data_source = "tender_notice_table"

    # 回退：扁平 Tender 表（单公告）
    if not lifecycle:
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
        "data_source": data_source,
    })


# 3. GET /api/notices/{notice_id}
@router.get("/notices/{notice_id}", dependencies=[Depends(verify_api_key)])
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


# 5. GET /api/notices/{notice_id}/participants
@router.get("/notices/{notice_id}/participants", dependencies=[Depends(verify_api_key)])
async def get_notice_participants(notice_id: str, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """获取公告参与方列表（v4.1 第12节）。"""
    nid = _parse_int_id(notice_id, "notice_id")
    if nid is None:
        return _err(f"非法 notice_id: {notice_id}", 400)
    tender = (await db.execute(select(Tender).where(Tender.id == nid))).scalar_one_or_none()
    if not tender:
        return _err(f"公告 {notice_id} 不存在")

    # 优先查四层实体：NoticeSource → NoticeParticipant（P0-1 回填的真实数据）
    participants: list[dict] = []
    data_source = "tender_fallback"
    source_url = tender.source_url or f"migrated://tender/{tender.id}"
    notice_row = (await db.execute(
        select(NoticeSource.notice_id).where(NoticeSource.source_url == source_url)
    )).first()
    if notice_row is not None:
        rows = (await db.execute(
            select(NoticeParticipant)
            .where(NoticeParticipant.notice_id == notice_row[0])
            .order_by(NoticeParticipant.participant_role)
        )).scalars().all()
        participants = [{
            "participant_id": np.participant_id,
            "role": np.participant_role,
            "raw_name": np.raw_name,
            "normalized_name": np.normalized_name or np.raw_name,
            "organization_id": np.organization_id,
            "resolution_status": np.resolution_status,
        } for np in rows]
        if participants:
            data_source = "entity_table"

    # 回退：扁平 Tender 表组织列（历史兼容）
    if not participants:
        if tender.tender_org:
            participants.append({"role": "purchaser", "raw_name": tender.tender_org,
                                 "normalized_name": tender.tender_org, "resolution_status": "resolved"})
        if tender.agency:
            participants.append({"role": "procuring_agency", "raw_name": tender.agency,
                                 "normalized_name": tender.agency, "resolution_status": "resolved"})
        if tender.win_company:
            participants.append({"role": "winner", "raw_name": tender.win_company,
                                 "normalized_name": tender.win_company, "resolution_status": "resolved"})
    return _ok({"participants": participants, "total": len(participants),
                "data_source": data_source})


# 7. GET /api/fields/{field_id}
@router.get("/fields/{field_id}", dependencies=[Depends(verify_api_key)])
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
router.add_api_route("/organizations/search", organizations_search, methods=["GET"], dependencies=[Depends(verify_api_key)])
# 9. GET /api/organizations/{org_id}
# TODO: 前端集成认证后启用
router.add_api_route("/organizations/{org_id}", get_organization, methods=["GET"])
# 10. POST /api/extract/tasks
router.add_api_route("/extract/tasks", create_extract_task, methods=["POST"], dependencies=[Depends(verify_api_key)])
# 11. GET /api/extract/tasks/{task_id}
router.add_api_route("/extract/tasks/{task_id}", get_extract_task, methods=["GET"], dependencies=[Depends(verify_api_key)])
# 12. GET /api/stats/quality
# TODO: 前端集成认证后启用
router.add_api_route("/stats/quality", get_stats_quality, methods=["GET"])


# ==== 子模块路由注册 ====
# 在 router 定义之后 import，子模块将各自路由注册到 router 上。
# 端点 4 (notices/{notice_id}/sources) 与 端点 6 (sources/{source_id}/versions)
# 实现在 v41_sources.py，import 时注册到 router。
from app.api import v41_sources  # noqa: E402,F401

# ==== re-export：保持原有公开接口不变 ====
# 以下函数已拆到 v41_sources.py 实现，但原有 import 路径
# （from app.api.v41_api import ...）必须继续可用。
from app.api.v41_sources import (  # noqa: E402,F401
    _build_versions_payload_from_notice_versions,
    _build_versions_payload_from_tender,
    get_notice_sources,
    get_source_versions,
)
