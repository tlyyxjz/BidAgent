"""订阅 API 路由（命题第 5/6 项硬要求：定时执行 + 增量推送）。

端点：
- POST /api/subscriptions 创建订阅
- GET /api/subscriptions 列出当前用户订阅
- GET /api/subscriptions/{id} 查询单个订阅
- POST /api/subscriptions/{id}/trigger 手动触发推送
- GET /api/subscriptions/{id}/tenders 查询订阅的招标信息
- DELETE /api/subscriptions/{id} 取消订阅
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import (
    AnyHttpUrl,
    BaseModel,
    EmailStr,
    Field,
    field_validator,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import verify_api_key
from app.models.database import AsyncSessionLocal
from app.models.subscription import Subscription, PushLog
from app.models.tender import Tender
from app.models.user import User
from app.scheduler.subscription import (
    create_subscription,
    trigger_subscription,
    get_unpushed_tenders,
)
from app.utils.logger import get_logger

logger = get_logger("subscribe_api")

router = APIRouter(prefix="/api/subscriptions", tags=["subscriptions"])


# ==== 认证依赖（封装 verify_api_key 返回 tuple）====

async def get_current_user(
    cred: tuple[User, Any, str] = Depends(verify_api_key),
) -> User:
    """从 verify_api_key 返回的 tuple 中提取 User。"""
    return cred[0]


# ==== 请求/响应模型 ====

class CreateSubscriptionRequest(BaseModel):
    """创建订阅请求。"""
    raw_query: str = Field(..., min_length=2, max_length=500,
                           description="自然语言查询（命题示例格式）")
    platforms: list[str] = Field(
        default_factory=lambda: ["ccgp"],
        description="目标平台：ccgp/chinabidding/ggzy",
    )
    push_channels: list[str] = Field(
        default_factory=lambda: ["email"],
        description="推送渠道：email/webhook",
    )
    # Sol S-10/S-15：推送目标（email 通道需要 notify_email；webhook 通道需要 webhook_url）
    notify_email: EmailStr | None = Field(
        default=None,
        description="邮件推送收件地址（push_channels 含 email 时必填）",
    )
    webhook_url: AnyHttpUrl | None = Field(
        default=None,
        description="Webhook 推送地址（push_channels 含 webhook 时必填）",
    )

    @field_validator("push_channels")
    @classmethod
    def validate_push_channels(cls, value: list[str]) -> list[str]:
        """Sol S-15：校验推送渠道白名单 + 去重。"""
        allowed = {"email", "webhook"}
        normalized = list(dict.fromkeys(
            channel.strip().lower()
            for channel in value
            if channel.strip()
        ))
        unknown = set(normalized) - allowed
        if unknown:
            raise ValueError(f"不支持的推送渠道: {sorted(unknown)}")
        return normalized


class SubscriptionResponse(BaseModel):
    """订阅响应。"""
    id: int
    user_id: int
    raw_query: str
    trigger_type: str
    frequency_cron: str | None = None
    platforms: list[str]
    push_channels: list[str]
    # Sol S-10/S-15：推送目标字段
    notify_email: str | None = None
    webhook_url: str | None = None
    is_active: bool
    last_pushed_at: str | None = None
    created_at: str | None = None


class TriggerResponse(BaseModel):
    """触发响应。"""
    subscription_id: int
    status: str
    count: int = 0
    report_path: str | None = None
    push_channels: dict[str, str] | None = None


# ==== 辅助函数 ====

def _to_response(sub: Subscription) -> SubscriptionResponse:
    """ORM 转 response。"""
    return SubscriptionResponse(
        id=sub.id,
        user_id=sub.user_id,
        raw_query=sub.raw_query,
        trigger_type=sub.trigger_type,
        frequency_cron=sub.frequency_cron,
        platforms=sub.platforms or [],
        push_channels=sub.push_channels or [],
        notify_email=sub.notify_email,
        webhook_url=str(sub.webhook_url) if sub.webhook_url else None,
        is_active=sub.is_active,
        last_pushed_at=sub.last_pushed_at.isoformat() if sub.last_pushed_at else None,
        created_at=sub.created_at.isoformat() if sub.created_at else None,
    )


async def _get_user_subscription(
    db: AsyncSession, user: User, sub_id: int
) -> Subscription:
    """获取订阅并校验归属。"""
    result = await db.execute(
        select(Subscription).where(
            Subscription.id == sub_id,
            Subscription.user_id == user.id,
        )
    )
    sub = result.scalar_one_or_none()
    if sub is None:
        raise HTTPException(status_code=404, detail="订阅不存在或无权访问")
    return sub


# ==== 路由 ====

@router.post("", response_model=dict)
async def create_sub(
    req: CreateSubscriptionRequest,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """创建订阅（命题第 5/6 项硬要求）。"""
    sub_id = await create_subscription(
        user_id=user.id,
        raw_query=req.raw_query,
        platforms=req.platforms,
        push_channels=req.push_channels,
        notify_email=req.notify_email,
        webhook_url=str(req.webhook_url) if req.webhook_url else None,
    )
    return {"code": 201, "data": {"subscription_id": sub_id}, "msg": "订阅创建成功"}


@router.get("", response_model=dict)
async def list_subs(
    user: User = Depends(get_current_user),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, description="分页偏移（m-2 修复）"),
) -> dict[str, Any]:
    """列出当前用户的订阅（支持分页）。"""
    async with AsyncSessionLocal() as db:
        base_stmt = select(Subscription).where(Subscription.user_id == user.id)
        # 总数
        from sqlalchemy import func
        total = (await db.execute(
            select(func.count()).select_from(base_stmt.subquery())
        )).scalar() or 0
        # 分页查询
        result = await db.execute(
            base_stmt.order_by(Subscription.created_at.desc())
            .limit(limit).offset(offset)
        )
        subs = result.scalars().all()
        return {
            "code": 200,
            "data": {
                "items": [_to_response(s).model_dump() for s in subs],
                "total": total,
                "limit": limit,
                "offset": offset,
            },
            "msg": "ok",
        }


@router.get("/{sub_id}", response_model=dict)
async def get_sub(
    sub_id: int,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """查询单个订阅。"""
    async with AsyncSessionLocal() as db:
        sub = await _get_user_subscription(db, user, sub_id)
        return {"code": 200, "data": _to_response(sub).model_dump(), "msg": "ok"}


@router.post("/{sub_id}/trigger", response_model=dict)
async def trigger_sub(
    sub_id: int,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """手动触发订阅推送（命题第 6 项硬要求：增量推送）。

    m-7 修复：直接传 user_id 给 trigger_subscription，由底层一次性校验归属
    + 触发推送，避免本层先开 session 校验、底层再开 session 操作的双连接。
    手动触发 force=True 跳过 cron 检查。
    """
    result = await trigger_subscription(
        sub_id, force=True, user_id=user.id
    )
    return {"code": 200, "data": result, "msg": "ok"}


@router.get("/{sub_id}/tenders", response_model=dict)
async def list_sub_tenders(
    sub_id: int,
    user: User = Depends(get_current_user),
    only_unpushed: bool = Query(True, description="只看未推送的"),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """查询订阅下的招标信息。"""
    from app.llm.schemas import ParsedFilters

    async with AsyncSessionLocal() as db:
        sub = await _get_user_subscription(db, user, sub_id)
        filters = ParsedFilters(**(sub.parsed_filters or {"raw_query": sub.raw_query}))

        if only_unpushed:
            tenders = await get_unpushed_tenders(db, sub_id, filters, limit=limit)
        else:
            stmt = select(Tender).order_by(Tender.publish_time.desc()).limit(limit)
            result = await db.execute(stmt)
            tenders = result.scalars().all()

        return {
            "code": 200,
            "data": [
                {
                    "id": t.id,
                    "project_name": t.project_name,
                    "publish_time": t.publish_time.isoformat() if t.publish_time else None,
                    "source_url": t.source_url,
                    "core_content": (t.core_content or "")[:200],
                    "attachment_url": t.attachment_url,
                    "source_platform": t.source_platform,
                }
                for t in tenders
            ],
            "msg": "ok",
        }


@router.delete("/{sub_id}", response_model=dict)
async def cancel_sub(
    sub_id: int,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """取消订阅。"""
    async with AsyncSessionLocal() as db:
        sub = await _get_user_subscription(db, user, sub_id)
        sub.is_active = False
        await db.commit()
    return {"code": 200, "data": None, "msg": "订阅已取消"}
