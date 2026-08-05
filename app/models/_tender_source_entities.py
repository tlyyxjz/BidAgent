"""来源页面与版本层实体：NoticeSource + NoticeVersion。

对应《标小智 项目总体规划 v4.1》第四章 4.6、4.7 节。
从 `app.models.tender_project` 拆出，按四层聚合的来源/版本层组织。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models._tender_ulid import _new_ulid
from app.models.database import Base
from app.models.user import utc_now


class NoticeSource(Base):
    """来源页面（v4.1 第四章 4.6 节）。

    代表公告在某个具体平台上的一个发布页面。
    同一公告可被多平台转载，形成来源组（source_group）。

    字段说明：
    - notice_source_id: ULID 主键
    - notice_id: 外键→tender_notices.notice_id
    - platform_type: 平台类型 government/authorized/commercial/unknown
    - publication_role: 发布角色 original/official_repost/commercial_repost/
      index_only/unknown
    - source_quality: 来源质量评级 official_original/official_repost/
      authorized_original/commercial_repost/index_only/unknown
    - origin_url / repost_of: 原始来源 URL 与被转载来源 ID
    - source_group: 同一公告跨平台转载的分组键
    """

    __tablename__ = "notice_sources"

    notice_source_id: Mapped[str] = mapped_column(
        String(26), primary_key=True, default=_new_ulid
    )
    notice_id: Mapped[str] = mapped_column(
        String(26),
        ForeignKey("tender_notices.notice_id"),
        nullable=False,
        index=True,
    )
    source_url: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    source_platform: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    platform_type: Mapped[str] = mapped_column(String(20), nullable=False)
    publication_role: Mapped[str] = mapped_column(String(30), nullable=False)
    source_quality: Mapped[str] = mapped_column(String(30), nullable=False)
    quality_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    repost_of: Mapped[str | None] = mapped_column(String(26), nullable=True)
    source_group: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<NoticeSource notice_source_id={self.notice_source_id} "
            f"source_platform={self.source_platform!r} notice_id={self.notice_id}>"
        )


class NoticeVersion(Base):
    """公告版本（v4.1 第四章 4.7 节）。

    代表某个来源页面的一次抓取快照。通过 content_sha256 / raw_text_sha256
    判定内容是否变化，通过 previous_version_id 串联版本链。

    字段说明：
    - version_id: ULID 主键
    - notice_source_id: 外键→notice_sources.notice_source_id
    - content_sha256 / raw_text_sha256 / normalized_text_sha256: 多级内容指纹
    - previous_version_id: 上一版本 ID（版本链，可空）
    - change_type: 变更类型 initial/none/minor/material/withdrawn
    - snapshot_path: 原始快照存储路径
    - normalizer_version / extractor_version / model_identifier / prompt_hash:
      生成该版本所用流水线组件版本，用于可追溯性
    """

    __tablename__ = "notice_versions"

    version_id: Mapped[str] = mapped_column(
        String(26), primary_key=True, default=_new_ulid
    )
    notice_source_id: Mapped[str] = mapped_column(
        String(26),
        ForeignKey("notice_sources.notice_source_id"),
        nullable=False,
        index=True,
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    http_status: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_text_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    previous_version_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    change_type: Mapped[str] = mapped_column(String(20), nullable=False)
    snapshot_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalizer_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    extractor_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    model_identifier: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<NoticeVersion version_id={self.version_id} "
            f"change_type={self.change_type!r} "
            f"notice_source_id={self.notice_source_id}>"
        )


__all__ = ["NoticeSource", "NoticeVersion"]
