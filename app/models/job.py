"""抓取任务模型。

job_id 用 UUID 字符串作为主键，便于通过 RQ 入队后用同一 ID 查询。
状态机: pending -> running -> completed | failed
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base
from app.models.user import utc_now


# 任务状态枚举
JOB_PENDING = "pending"
JOB_RUNNING = "running"
JOB_COMPLETED = "completed"
JOB_FAILED = "failed"

VALID_JOB_STATUSES = (JOB_PENDING, JOB_RUNNING, JOB_COMPLETED, JOB_FAILED)


class ScrapeJob(Base):
    """一次抓取任务（同步直抓或批量入队都使用此表）."""

    __tablename__ = "scrape_jobs"

    # UUID 字符串主键
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 目标 URL（batch 任务可能为空，items 中各自带 URL）
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 任务状态
    status: Mapped[str] = mapped_column(
        String(20), default=JOB_PENDING, nullable=False, index=True
    )
    # 原始请求 JSON（selectors / template / options 等）
    request_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 抓取结果 JSON
    result_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 失败时的错误消息
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 进度（0~100，batch 用）
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ScrapeJob id={self.id} status={self.status}>"
