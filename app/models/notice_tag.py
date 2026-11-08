"""公告打标结果存储模型。

pipeline 打标集成点：processor_agent 加工完成后，对处理过的公告打标并写入
notice_tags（industry / region / notice_type）。打标失败不影响主链路。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base


class NoticeTag(Base):
    """一条公告的打标结果。tender_id 关联 tenders.id。"""

    __tablename__ = "notice_tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tender_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    industry: Mapped[str] = mapped_column(String(50), nullable=False)
    region: Mapped[str] = mapped_column(String(50), nullable=False)
    notice_type: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
