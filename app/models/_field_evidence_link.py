"""FieldEvidenceLink 字段-证据关联表 ORM（W2-05）。

从 `app.models.evidence` 拆出。
多对多关联：一个 ExtractedField 可关联多个 Evidence，一个 Evidence 可被多个字段引用。

约束：
- evidence_role 标注证据角色（primary/context/qualifier/derivation_input/contradiction）
- sequence 控制多证据的展示顺序
- is_required 标注是否为必要证据
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base
from app.models.user import utc_now


class FieldEvidenceLink(Base):
    """字段-证据关联表（W2-05）。

    多对多关联：一个 ExtractedField 可关联多个 Evidence，一个 Evidence 可被多个字段引用。

    约束：
    - evidence_role 标注证据角色（primary/context/qualifier/derivation_input/contradiction）
    - sequence 控制多证据的展示顺序
    - is_required 标注是否为必要证据
    """

    __tablename__ = "field_evidence_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 关联 ExtractedField
    field_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("extracted_fields.id"), nullable=False, index=True
    )
    # 关联 Evidence
    evidence_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("evidence.id"), nullable=False, index=True
    )
    # 证据角色
    evidence_role: Mapped[str] = mapped_column(
        String(30), nullable=False, default="primary"
    )
    # 展示顺序（多证据时按 sequence 排序）
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 是否为必要证据
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<FieldEvidenceLink id={self.id} field_id={self.field_id} "
            f"evidence_id={self.evidence_id} role={self.evidence_role!r}>"
        )


__all__ = ["FieldEvidenceLink"]
