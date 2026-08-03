"""用户与 API Key 模型。

工程规范：
- API Key 以 HMAC-SHA256 摘要存储（v4.1 §13.1 升级），不存明文。
  HMAC 引入服务端 SECRET_KEY，相比纯 SHA256 抗离线爆破。
- 用户套餐 plan 决定速率限制（free/starter/pro）。
- 联合表无 id 字段时不按 id 排序（本 MVP 暂无联合表）。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.database import Base


def utc_now() -> datetime:
    """返回当前 UTC 时间。"""
    return datetime.now(timezone.utc)


# 套餐枚举（字符串字面量，避免引入 enum 依赖）
PLAN_FREE = "free"
PLAN_STARTER = "starter"
PLAN_PRO = "pro"

VALID_PLANS = (PLAN_FREE, PLAN_STARTER, PLAN_PRO)


class User(Base):
    """ScrapeFlow 用户."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    # free / starter / pro
    plan: Mapped[str] = mapped_column(String(20), default=PLAN_FREE, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    api_keys: Mapped[list["ApiKey"]] = relationship(
        "ApiKey", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User id={self.id} email={self.email} plan={self.plan}>"


class ApiKey(Base):
    """用户拥有的 API Key，以 HMAC-SHA256 摘要存储（v4.1 §13.1）。

    摘要由 `app.utils.credentials.hash_api_key` 生成：以 SECRET_KEY 环境变量
    作为 HMAC 密钥，对 API Key 明文计算 SHA256 摘要。相比纯 SHA256，HMAC 引入
    服务端密钥使得即使数据库泄露，攻击者也无法离线爆破出原始 API Key。
    摘要长度仍为 64 字符 hex，与原 SHA256 hexdigest 长度一致，DB schema 无需变更。
    """

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # HMAC-SHA256 hexdigest（v4.1 §13.1），64 字符。
    # 由 app.utils.credentials.hash_api_key 生成，使用 SECRET_KEY 作为 HMAC 密钥。
    # 注意：原 SHA256 hash 字段已废弃，新存储的 key 一律使用 HMAC 摘要。
    key_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    # 可读名称（如 "production" / "local-test"）
    name: Mapped[str] = mapped_column(String(128), default="default", nullable=False)
    # 最后一次使用时间
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    user: Mapped[User] = relationship("User", back_populates="api_keys")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ApiKey id={self.id} name={self.name} user_id={self.user_id}>"
