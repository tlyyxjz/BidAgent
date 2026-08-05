"""Evidence 证据对象表 ORM（W2-05）。

从 `app.models.evidence` 拆出。
存储原文中的证据片段 + 偏移量 + 验证结果。

约束：
- 偏移量基于清洗后原始文本快照（不依赖实时 DOM）
- snapshot_sha256 + raw_text_sha256 保证偏移量稳定复现
- match_method 来自 W2-03 五级降级匹配
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base
from app.models.user import utc_now


class Evidence(Base):
    """证据对象表（W2-05）。

    存储原文中的证据片段 + 偏移量 + 验证结果。

    约束：
    - 偏移量基于清洗后原始文本快照（不依赖实时 DOM）
    - snapshot_sha256 + raw_text_sha256 保证偏移量稳定复现
    - match_method 来自 W2-03 五级降级匹配
    """

    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 关联 Tender
    tender_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tenders.id"), nullable=False, index=True
    )
    # 证据文本（原文中的连续片段，不得改写）
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False)
    # 上下文（证据前后的文本，用于人工复核）
    context_before: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_after: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ==== 偏移量（双坐标，W2-02）====
    raw_start: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_end: Mapped[int] = mapped_column(Integer, nullable=False)
    normalized_start: Mapped[int] = mapped_column(Integer, nullable=False, default=-1)
    normalized_end: Mapped[int] = mapped_column(Integer, nullable=False, default=-1)

    # ==== 匹配与验证（W2-03 + W2-04）====
    match_method: Mapped[str] = mapped_column(
        String(20), nullable=False, default="not_found"
    )
    confidence: Mapped[float] = mapped_column(Integer, nullable=False, default=0)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verification_rule: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ==== 快照哈希（偏移量稳定复现）====
    snapshot_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_text_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Evidence id={self.id} tender_id={self.tender_id} "
            f"match={self.match_method!r} verified={self.verified}>"
        )


__all__ = ["Evidence"]
