"""招标信息管理 API（admin + 公共查询）。

路由：
- POST /api/tenders              admin 手动注入招标信息（测试/补录用）
- GET  /api/tenders              公共查询（按平台/地区/主题/时间过滤）
- GET  /api/tenders/{id}         查询单条详情
- DELETE /api/tenders/{id}       admin 删除招标信息
- POST /api/tenders/batch        admin 批量注入
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import verify_api_key, verify_admin
from app.models.database import get_db
from app.models.tender import Tender
from app.models.user import User
from app.scheduler.utils import safe_contains
from app.utils.logger import get_logger

logger = get_logger("tender_api")

router = APIRouter(prefix="/api/tenders", tags=["tenders"])


# ==== 认证依赖 ====

async def _get_user(
    cred: tuple[User, Any, str] = Depends(verify_api_key),
) -> User:
    """从 verify_api_key 返回的 tuple 中提取 User。"""
    return cred[0]


# ==== 请求/响应模型 ====

class TenderCreateRequest(BaseModel):
    """创建招标信息请求（admin 用）。"""
    project_name: str = Field(..., min_length=2, max_length=500)
    bid_number: str | None = Field(None, max_length=100)
    budget_amount: float | None = Field(None, ge=0)
    win_amount: float | None = Field(None, ge=0)
    location: str | None = Field(None, max_length=200)
    publish_time: datetime | None = None
    deadline: datetime | None = None
    tender_org: str | None = Field(None, max_length=300)
    agency: str | None = Field(None, max_length=300)
    contact_name: str | None = Field(None, max_length=100)
    contact_phone: str | None = Field(None, max_length=64)  # SHA256 hex
    contact_email: str | None = Field(None, max_length=64)  # SHA256 hex
    qualification: str | None = None
    notice_type: str | None = Field(None, max_length=50)
    win_company: str | None = Field(None, max_length=300)
    source_platform: str = Field(..., max_length=100)
    source_url: str = Field(..., max_length=2000)
    core_content: str | None = None
    attachment_url: str | None = Field(None, max_length=2000)


class TenderBatchCreateRequest(BaseModel):
    """批量创建请求。"""
    items: list[TenderCreateRequest] = Field(..., min_length=1, max_length=100)


class TenderResponse(BaseModel):
    """招标信息响应。"""
    id: int
    project_name: str
    bid_number: str | None = None
    budget_amount: float | None = None
    location: str | None = None
    publish_time: str | None = None
    deadline: str | None = None
    tender_org: str | None = None
    notice_type: str | None = None
    source_platform: str | None = None
    source_url: str | None = None
    core_content: str | None = None
    attachment_url: str | None = None


def _to_response(t: Tender) -> TenderResponse:
    """ORM → response。"""
    return TenderResponse(
        id=t.id,
        project_name=t.project_name,
        bid_number=t.bid_number,
        budget_amount=float(t.budget_amount) if t.budget_amount else None,
        location=t.location,
        publish_time=t.publish_time.isoformat() if t.publish_time else None,
        deadline=t.deadline.isoformat() if t.deadline else None,
        tender_org=t.tender_org,
        notice_type=t.notice_type,
        source_platform=t.source_platform,
        source_url=t.source_url,
        core_content=t.core_content,
        attachment_url=t.attachment_url,
    )


# ==== Admin 路由（需 X-Admin-Secret）====

@router.post("", dependencies=[Depends(verify_admin)])
async def create_tender(
    payload: TenderCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """admin 手动注入招标信息（测试/补录）。"""
    tender = Tender(**payload.model_dump())
    db.add(tender)
    await db.commit()
    await db.refresh(tender)

    # P0-1：手动注入的公告同步四层实体（失败降级不阻塞）
    try:
        from app.processors.entity_sync_hook import sync_tender_entities

        await sync_tender_entities(db, [tender])
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("实体同步失败（降级跳过）: {}", exc)
    logger.info("tender created id={} name={}", tender.id, tender.project_name[:50])
    return {"code": 201, "data": _to_response(tender).model_dump(), "msg": "ok"}


@router.post("/batch", dependencies=[Depends(verify_admin)])
async def create_tenders_batch(
    payload: TenderBatchCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """admin 批量注入。"""
    tenders = [Tender(**item.model_dump()) for item in payload.items]
    db.add_all(tenders)
    await db.commit()

    # P0-1：批量注入的公告同步四层实体（失败降级不阻塞）
    try:
        from app.processors.entity_sync_hook import sync_tender_entities

        for t in tenders:
            await db.refresh(t)  # 拿到自增 id
        await sync_tender_entities(db, tenders)
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("实体同步失败（降级跳过）: {}", exc)
    logger.info("batch created count={}", len(tenders))
    return {
        "code": 201,
        "data": {"count": len(tenders)},
        "msg": f"成功创建 {len(tenders)} 条招标信息",
    }


@router.delete("/{tender_id}", dependencies=[Depends(verify_admin)])
async def delete_tender(
    tender_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """admin 删除招标信息。"""
    result = await db.execute(select(Tender).where(Tender.id == tender_id))
    tender = result.scalar_one_or_none()
    if tender is None:
        raise HTTPException(status_code=404, detail="招标信息不存在")
    await db.delete(tender)
    await db.commit()
    return {"code": 200, "data": None, "msg": "已删除"}


# ==== 公共查询路由（需 Bearer API key）====

@router.get("", response_model=dict)
async def list_tenders(
    platform: str | None = Query(None, description="来源平台过滤"),
    region: str | None = Query(None, description="地区过滤"),
    topic: str | None = Query(None, description="主题关键词（项目名称包含）"),
    notice_type: str | None = Query(None, description="公告类型"),
    start_date: datetime | None = Query(None, description="发布时间起"),
    end_date: datetime | None = Query(None, description="发布时间止"),
    min_budget: float | None = Query(None, ge=0, description="最小预算"),
    max_budget: float | None = Query(None, ge=0, description="最大预算"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(_get_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """公共查询招标信息（支持多维度过滤）。"""
    # S-8 修复：用 Depends(get_db) 而非 AsyncSessionLocal()，避免每次请求开新 session
    stmt = select(Tender)
    if platform:
        stmt = stmt.where(Tender.source_platform == platform)
    if region:
        # M-1 修复：用 safe_contains 显式指定 escape，转义才生效
        stmt = stmt.where(safe_contains(Tender.location, region))
    if topic:
        stmt = stmt.where(safe_contains(Tender.project_name, topic))
    if notice_type:
        stmt = stmt.where(Tender.notice_type == notice_type)
    if start_date:
        stmt = stmt.where(Tender.publish_time >= start_date)
    if end_date:
        stmt = stmt.where(Tender.publish_time <= end_date)
    if min_budget is not None:
        stmt = stmt.where(Tender.budget_amount >= min_budget)
    if max_budget is not None:
        stmt = stmt.where(Tender.budget_amount <= max_budget)

    # count 总数
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    # 分页查询
    stmt = stmt.order_by(Tender.publish_time.desc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    tenders = result.scalars().all()

    return {
        "code": 200,
        "data": {
            "items": [_to_response(t).model_dump() for t in tenders],
            "total": total,
            "limit": limit,
            "offset": offset,
        },
        "msg": "ok",
    }


@router.get("/stats/overview", response_model=dict)
async def tender_stats(
    user: User = Depends(_get_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """招标信息统计概览（用于 Web UI 仪表板）。"""
    # S-8 修复：用 Depends(get_db) 而非 AsyncSessionLocal()
    # 总数
    total = (await db.execute(select(func.count(Tender.id)))).scalar() or 0
    # 按平台
    platform_stmt = select(
        Tender.source_platform, func.count(Tender.id)
    ).group_by(Tender.source_platform)
    platforms = {
        row[0]: row[1] for row in (await db.execute(platform_stmt)).all()
    }
    # 按公告类型
    type_stmt = select(
        Tender.notice_type, func.count(Tender.id)
    ).group_by(Tender.notice_type)
    types = {row[0]: row[1] for row in (await db.execute(type_stmt)).all()}
    # 预算总和
    budget_stmt = select(func.sum(Tender.budget_amount))
    total_budget = (await db.execute(budget_stmt)).scalar() or 0

    return {
        "code": 200,
        "data": {
            "total": total,
            "by_platform": platforms,
            "by_notice_type": types,
            "total_budget": float(total_budget),
        },
        "msg": "ok",
    }


@router.get("/{tender_id}", response_model=dict)
async def get_tender(
    tender_id: int,
    user: User = Depends(_get_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """查询单条招标信息详情。

    路由顺序说明：/stats/overview 必须在本路由之前定义，
    否则 "stats" 会被当作 tender_id 匹配到本路由导致 422。
    FastAPI 按声明顺序匹配，当前顺序已正确。
    """
    # S-8 修复：用 Depends(get_db) 而非 AsyncSessionLocal()
    result = await db.execute(select(Tender).where(Tender.id == tender_id))
    tender = result.scalar_one_or_none()
    if tender is None:
        raise HTTPException(status_code=404, detail="招标信息不存在")
    return {"code": 200, "data": _to_response(tender).model_dump(), "msg": "ok"}
