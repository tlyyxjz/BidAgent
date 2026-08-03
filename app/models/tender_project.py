"""v4.1 四层实体数据模型。

对应《标小智 项目总体规划 v4.1》第四章实体定义，建立四层聚合结构：

    TenderProject（采购项目）
      └─ TenderNotice（业务公告）
           └─ NoticeSource（来源页面）
                └─ NoticeVersion（抓取版本）

并附带两个辅助实体：
- NoticeParticipant：公告参与关系（v4.1 4.5）
- ProjectIdentifier：项目标识（v4.1 4.2）

本文件只新增四层实体定义，不改动现有扁平 Tender 表（app/models/tender.py）。

工程规范：
- 主键统一使用 ULID（String(26)），由 ulid-py 生成。
- 字符串字段必须指定长度；Text 字段例外。
- 外键列建索引；高频过滤字段建索引。
- 与现有 tender.py / organization.py 保持一致的 SQLAlchemy 2.x 风格。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base
from app.models.user import utc_now

try:  # ulid-py 已安装则优先使用
    import ulid as _ulid

    def _new_ulid() -> str:
        """生成 26 字符 ULID 字符串。"""
        return str(_ulid.new())
except ImportError:  # pragma: no cover
    import uuid

    def _new_ulid() -> str:
        # TODO: 安装 ulid-py 后替换为真正的 ULID 生成
        return uuid.uuid4().hex[:26]


class TenderProject(Base):
    """采购项目（v4.1 第四章 4.2 节）。

    四层聚合的根实体：一个采购项目聚合其下所有业务公告。
    同一采购项目可包含招标/更正/中标/废标等多类公告。

    字段说明：
    - project_id: ULID 主键
    - canonical_name: 项目规范名称（跨公告归并后的标准名）
    - industry_category: 行业分类 goods/service/engineering/other
    - purchaser_entity_id / agency_entity_id: 关联组织实体 ID（可空）
    - resolution_status: 项目归并状态 resolved/ambiguous/unresolved
    """

    __tablename__ = "tender_projects"

    project_id: Mapped[str] = mapped_column(
        String(26), primary_key=True, default=_new_ulid
    )
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    industry_category: Mapped[str] = mapped_column(String(50), nullable=False)
    purchaser_entity_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    agency_entity_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    resolution_status: Mapped[str] = mapped_column(String(20), nullable=False)
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
        return (
            f"<TenderProject project_id={self.project_id} "
            f"canonical_name={self.canonical_name!r}>"
        )


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


class ProjectIdentifier(Base):
    """项目标识（v4.1 第四章 4.2 节）。

    一个采购项目可携带多个标识号（采购编号/代理机构编号/平台编号/分包号等），
    本表记录这些标识的原始值与规范化值，用于跨平台项目归并。

    字段说明：
    - identifier_id: ULID 主键
    - project_id: 外键→tender_projects.project_id
    - identifier_type: 标识类型 procurement/agency/platform/lot/other
    - raw_value / normalized_value: 原始值与规范化值
    - issuing_body: 发证主体
    - source_id: 该标识来源（可空）
    """

    __tablename__ = "project_identifiers"

    identifier_id: Mapped[str] = mapped_column(
        String(26), primary_key=True, default=_new_ulid
    )
    project_id: Mapped[str] = mapped_column(
        String(26),
        ForeignKey("tender_projects.project_id"),
        nullable=False,
        index=True,
    )
    identifier_type: Mapped[str] = mapped_column(String(20), nullable=False)
    raw_value: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_value: Mapped[str | None] = mapped_column(String(200), nullable=True)
    issuing_body: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(26), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ProjectIdentifier identifier_id={self.identifier_id} "
            f"identifier_type={self.identifier_type!r} "
            f"project_id={self.project_id}>"
        )
