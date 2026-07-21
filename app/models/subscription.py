"""订阅与推送日志模型。

工程规范：
- Subscription 存储用户原始自然语言查询（命题硬要求）。
- PushLog 用于增量推送去重：同一 subscription + tender 组合不重复推送。
- 联合表无 id 字段时不按 id 排序（本表均有 id，按 pushed_at 排序）。
- 外键必须加 index=True；字符串字段必须指定长度。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.database import Base
from app.models.user import utc_now


# 触发类型枚举（字符串字面量，避免引入 enum 依赖）
TRIGGER_IMMEDIATE = "immediate"
TRIGGER_SCHEDULED = "scheduled"

VALID_TRIGGER_TYPES = (TRIGGER_IMMEDIATE, TRIGGER_SCHEDULED)


class Subscription(Base):
    """用户订阅：自然语言查询 + cron 调度 + 推送渠道."""

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 用户原始自然语言查询（命题硬要求）
    raw_query: Mapped[str] = mapped_column(Text, nullable=False)
    # LLM 解析出的过滤条件
    parsed_filters: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # cron 表达式，如 "0 9 * * *" 每天 9 点
    frequency_cron: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # immediate / scheduled（命题第 5 项硬要求）
    trigger_type: Mapped[str] = mapped_column(
        String(20), default=TRIGGER_IMMEDIATE, nullable=False, index=True
    )
    # 目标平台列表
    platforms: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 推送渠道：email / webhook
    push_channels: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Sol S-10/S-15：推送目标（即使配置了 push_channels=['email']，
    # 也必须 notify_email 已设置才会真实发送）
    notify_email: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    webhook_url: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_pushed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    push_logs: Mapped[list["PushLog"]] = relationship(
        "PushLog", back_populates="subscription", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Subscription id={self.id} user_id={self.user_id} "
            f"trigger_type={self.trigger_type}>"
        )


class PushLog(Base):
    """推送日志：增量推送去重核心表.

    同一 subscription + tender 组合通过唯一约束保证不重复推送。
    """

    __tablename__ = "push_logs"
    __table_args__ = (
        UniqueConstraint(
            "subscription_id", "tender_id", name="uq_pushlog_sub_tender"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tender_id: Mapped[int] = mapped_column(
        ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pushed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    # M-2 修复：推送内容哈希（report 文件的 SHA256）。
    # 用于 at-least-once 语义下的幂等去重：commit 失败导致重推时，
    # 推送前检查「最近 N 分钟内是否有相同 content_hash 的记录」，
    # 命中则跳过本次推送，降低用户收到重复邮件的概率。
    content_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )

    subscription: Mapped[Subscription] = relationship(
        "Subscription", back_populates="push_logs"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<PushLog id={self.id} subscription_id={self.subscription_id} "
            f"tender_id={self.tender_id}>"
        )
