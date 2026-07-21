"""招标信息模型。

工程规范：
- 联系人手机号/邮箱以 SHA256 hex 存储（64 字符）。
- source_url / core_content / attachment_url 为命题第 4 项硬要求。
- simhash 为 64 位内容指纹，用于近似去重。
- 字符串字段必须指定长度；Text 字段例外。
- 外键已加 index=True；高频过滤字段均建索引。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base
from app.models.user import utc_now


class Tender(Base):
    """一条招标/中标公告信息."""

    __tablename__ = "tenders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 项目名称
    project_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    # 招标编号
    bid_number: Mapped[str | None] = mapped_column(
        String(100), index=True, nullable=True
    )
    # 预算金额
    budget_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(15, 2), nullable=True
    )
    # 中标金额
    win_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(15, 2), nullable=True
    )
    # 地区
    location: Mapped[str | None] = mapped_column(
        String(200), index=True, nullable=True
    )
    # 发布时间
    publish_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )
    # 截止时间
    deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 招标人
    tender_org: Mapped[str | None] = mapped_column(
        String(300), index=True, nullable=True
    )
    # 代理机构
    agency: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # 联系人姓名
    contact_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 联系人电话（SHA256 hex 存储）
    contact_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 联系人邮箱（SHA256 hex 存储）
    contact_email: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 资质要求
    qualification: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 公告类型
    notice_type: Mapped[str | None] = mapped_column(
        String(50), index=True, nullable=True
    )
    # 中标公司
    win_company: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # 来源平台
    source_platform: Mapped[str | None] = mapped_column(
        String(100), index=True, nullable=True
    )
    # 来源链接（命题第 4 项硬要求）
    source_url: Mapped[str | None] = mapped_column(Text, index=True, nullable=True)
    # 核心内容（命题第 4 项硬要求，与原文事实一致）
    core_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 原始页面文本（C-2 修复：反幻觉校验时比对原文，避免永远通过）
    source_raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 附件链接（命题第 4 项硬要求）
    attachment_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 内容指纹（64 位 SimHash）
    simhash: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Tender id={self.id} project_name={self.project_name!r}>"
