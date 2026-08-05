"""公告层实体：TenderNotice + NoticeParticipant。

对应《标小智 项目总体规划 v4.1》第四章 4.3、4.5 节。
从 `app.models.tender_project` 拆出，按四层聚合的公告层组织。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models._tender_ulid import _new_ulid
from app.models.database import Base
from app.models.user import utc_now


class TenderNotice(Base):
    """业务公告（v4.1 第四章 4.3 节）。

    隶属于某个采购项目，代表一类业务公告（招标/更正/中标等）。
    同一公告可被多个来源页面转载，对应多个 NoticeSource。

    字段说明：
    - notice_id: ULID 主键
    - project_id: 外键→tender_projects.project_id
    - notice_type: 公告类型 tender/correction/clarification/award/
      cancellation/contract/other
    - status: 公告状态 active/superseded/withdrawn
    - superseded_by: 被哪条公告取代（可空）
    """

    __tablename__ = "tender_notices"

    notice_id: Mapped[str] = mapped_column(
        String(26), primary_key=True, default=_new_ulid
    )
    project_id: Mapped[str] = mapped_column(
        String(26),
        ForeignKey("tender_projects.project_id"),
        nullable=False,
        index=True,
    )
    notice_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    canonical_title: Mapped[str] = mapped_column(Text, nullable=False)
    publish_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    effective_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    superseded_by: Mapped[str | None] = mapped_column(String(26), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<TenderNotice notice_id={self.notice_id} "
            f"notice_type={self.notice_type!r} project_id={self.project_id}>"
        )


class NoticeParticipant(Base):
    """公告参与关系（v4.1 第四章 4.5 节）。

    记录某条公告中出现的参与方及其角色（采购人/代理机构/投标人/中标人等）。
    通过 organization_id 关联消歧后的组织实体；未消歧时仅保留 raw_name。

    字段说明：
    - participant_id: ULID 主键
    - notice_id: 外键→tender_notices.notice_id
    - version_id: 关联版本 ID（可空，标记该参与方源自哪次抓取）
    - organization_id: 外键→organizations.organization_id（可空，未消歧时为空）
    - participant_role: 角色 purchaser/procuring_agency/bidder/winner/
      consortium_member/subcontractor/other
    - lot_id: 分包 ID（多分包场景）
    - resolution_status: 消歧状态 resolved/ambiguous/unresolved
    """

    __tablename__ = "notice_participants"

    participant_id: Mapped[str] = mapped_column(
        String(26), primary_key=True, default=_new_ulid
    )
    notice_id: Mapped[str] = mapped_column(
        String(26),
        ForeignKey("tender_notices.notice_id"),
        nullable=False,
        index=True,
    )
    version_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    organization_id: Mapped[str | None] = mapped_column(
        String(26),
        ForeignKey("organizations.organization_id"),
        nullable=True,
    )
    raw_name: Mapped[str] = mapped_column(String(300), nullable=False)
    normalized_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    participant_role: Mapped[str] = mapped_column(
        String(30), nullable=False, index=True
    )
    lot_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resolution_status: Mapped[str] = mapped_column(String(20), nullable=False)
    evidence_id: Mapped[str | None] = mapped_column(String(26), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<NoticeParticipant participant_id={self.participant_id} "
            f"participant_role={self.participant_role!r} "
            f"notice_id={self.notice_id}>"
        )


__all__ = ["NoticeParticipant", "TenderNotice"]
