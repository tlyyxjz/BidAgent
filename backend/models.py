"""BidAgent v4.1 四层数据模型。

基于 v4.1 执行定稿版第四章设计：
    TenderProject（采购项目）
        └── TenderNotice（业务公告）
                ├── NoticeParticipant（公告参与关系）
                └── NoticeSource（来源页面）
                        └── NoticeVersion（抓取版本）
                                └── ExtractedField（抽取字段）
                                        └── FieldEvidenceLink
                                                └── Evidence（字段证据）

辅助实体：Organization、ProjectIdentifier、FactAssertionKey（逻辑键，无独立表）。

主键原则：所有核心实体使用 ULID（无业务含义的内部稳定主键，可排序）。
表名前缀 ba_ 避免与现有 v0 表冲突。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base
from app.models.user import utc_now

import ulid as _ulid


def _new_id() -> str:
    """生成 26 字符 ULID（Crockford Base32，时间排序，无业务含义）。

    依赖 ulid-py（已在 requirements.txt 中声明为必需依赖）。
    不再使用 uuid4.hex 截断作为 fallback，因为：
    1. 截断后的 UUID 不是合法 ULID（不符合 Crockford Base32 编码）；
    2. 不具备 ULID 的时间排序特征；
    3. 可能使依赖 ULID 格式的代码产生误判。

    如果环境中缺少 ulid-py，应直接报错而非降级：
        pip install ulid-py>=1.1.0
    """
    return str(_ulid.new())


# ============================================================
# 实体定义（按外键依赖顺序，避免 use_alter）
# ============================================================


class Organization(Base):
    """组织实体 - 采购人、代理机构、中标人统一使用。

    只保存组织自身的法律实体属性，不保存业务角色。
    业务角色由 NoticeParticipant 记录。
    """
    __tablename__ = "ba_organizations"

    organization_id: Mapped[str] = mapped_column(
        String(26), primary_key=True, default=_new_id
    )
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    unified_social_credit_code: Mapped[str | None] = mapped_column(
        String(18), nullable=True, index=True
    )
    # JSON array 字符串，如 ["上海某公司", "上海某有限公司"]
    aliases: Mapped[str | None] = mapped_column(Text, nullable=True)
    legal_entity_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="unknown"
    )
    resolution_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unresolved"
    )
    resolution_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class TenderProject(Base):
    """采购项目 - 贯穿招标、中标、更正、合同等阶段的同一个采购项目。"""
    __tablename__ = "ba_tender_projects"

    project_id: Mapped[str] = mapped_column(
        String(26), primary_key=True, default=_new_id
    )
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    industry_category: Mapped[str] = mapped_column(
        String(50), nullable=False, default="other"
    )
    purchaser_entity_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("ba_organizations.organization_id"), nullable=True, index=True
    )
    agency_entity_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("ba_organizations.organization_id"), nullable=True, index=True
    )
    resolution_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unresolved"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class TenderNotice(Base):
    """业务公告 - 项目生命周期中的一次业务公告。"""
    __tablename__ = "ba_tender_notices"

    notice_id: Mapped[str] = mapped_column(
        String(26), primary_key=True, default=_new_id
    )
    project_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("ba_tender_projects.project_id"), nullable=False, index=True
    )
    notice_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    canonical_title: Mapped[str] = mapped_column(Text, nullable=False)
    publish_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    effective_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", index=True
    )
    # 更正公告替代原公告时指向新公告
    superseded_by: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("ba_tender_notices.notice_id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class NoticeSource(Base):
    """来源页面 - 某一公告在具体平台上的页面实例。"""
    __tablename__ = "ba_notice_sources"

    notice_source_id: Mapped[str] = mapped_column(
        String(26), primary_key=True, default=_new_id
    )
    notice_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("ba_tender_notices.notice_id"), nullable=False, index=True
    )
    source_url: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    source_platform: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )
    platform_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="unknown"
    )
    publication_role: Mapped[str] = mapped_column(
        String(50), nullable=False, default="unknown"
    )
    source_quality: Mapped[str] = mapped_column(
        String(50), nullable=False, default="unknown", index=True
    )
    quality_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 转载链：指向原始来源页面
    repost_of: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("ba_notice_sources.notice_source_id"), nullable=True
    )
    source_group: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    # 修复：同一公告在同一 URL 上应唯一
    __table_args__ = (
        Index(
            "ix_ba_sources_notice_url",
            "notice_id",
            "source_url",
            unique=True,
        ),
    )


class NoticeVersion(Base):
    """公告版本 - 同一来源页面在某一抓取时间的内容状态。"""
    __tablename__ = "ba_notice_versions"

    version_id: Mapped[str] = mapped_column(
        String(26), primary_key=True, default=_new_id
    )
    notice_source_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("ba_notice_sources.notice_source_id"),
        nullable=False, index=True,
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    http_status: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_text_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    normalized_text_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    previous_version_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("ba_notice_versions.version_id"), nullable=True
    )
    change_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="initial"
    )
    snapshot_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalizer_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    extractor_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    model_identifier: Mapped[str | None] = mapped_column(String(200), nullable=True)
    prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class Evidence(Base):
    """证据对象 - 公告版本中的原文证据片段。"""
    __tablename__ = "ba_evidence"

    evidence_id: Mapped[str] = mapped_column(
        String(26), primary_key=True, default=_new_id
    )
    version_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("ba_notice_versions.version_id"),
        nullable=False, index=True,
    )
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False)
    context_before: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_after: Mapped[str | None] = mapped_column(Text, nullable=True)
    offset_space: Mapped[str] = mapped_column(
        String(50), nullable=False, default="clean_raw_text"
    )
    raw_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    normalized_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    normalized_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    match_method: Mapped[str | None] = mapped_column(String(100), nullable=True)
    normalization_version: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    snapshot_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_text_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )
    verification_rule: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class ExtractedField(Base):
    """抽取字段 - 从公告版本中抽取的结构化字段。

    多值字段存储说明（v4.1 §4.6）：
    ---------------------------------
    一个公告版本中同一字段名可能有多条记录，每条对应一个值。
    例如：
    - 多个中标人（联合体中标）：winner_name 字段有 2 行 ExtractedField
    - 多个分包金额：amount 字段按 lot_id 分多行，每行一个金额
    - 多个项目编号：通常存 ProjectIdentifier 表，但也可能在 ExtractedField 中多行

    因此 (version_id, field_name) 不设唯一约束，改为普通索引。
    完全相同的值（同 version_id + field_name + lot_id + raw_value）由应用层去重，
    避免数据库层过严约束阻塞多值字段写入。

    value_count 字段记录该字段在当前版本中的总值数（冗余字段，便于快速查询）。
    """
    __tablename__ = "ba_extracted_fields"

    field_id: Mapped[str] = mapped_column(
        String(26), primary_key=True, default=_new_id
    )
    version_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("ba_notice_versions.version_id"),
        nullable=False, index=True,
    )
    field_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    field_type: Mapped[str] = mapped_column(String(50), nullable=False)
    semantic_role: Mapped[str | None] = mapped_column(String(100), nullable=True)
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_value: Mapped[str | None] = mapped_column(
        Text, nullable=True, index=True
    )
    value_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    amount_type: Mapped[str | None] = mapped_column(
        String(50), nullable=True, index=True
    )
    lot_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    tax_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    support_level: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unsupported", index=True
    )
    support_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cross_verify_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="single_source"
    )
    source_quality_snapshot: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    primary_evidence_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("ba_evidence.evidence_id"), nullable=True
    )
    display_grade: Mapped[str] = mapped_column(
        String(20), nullable=False, default="low", index=True
    )
    display_rule_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    # 修复：去掉 unique=True，改为普通索引。
    # 原唯一约束 (version_id, field_name) 会阻塞多值字段写入
    # （多个中标人、多分包金额、多项目编号等场景）。
    # 完全重复由应用层去重，数据库层只保证查询效率。
    __table_args__ = (
        Index(
            "ix_ba_fields_version_name",
            "version_id",
            "field_name",
        ),
    )


class FieldEvidenceLink(Base):
    """字段证据关联 - 一个抽取字段可对应多段证据。"""
    __tablename__ = "ba_field_evidence_links"

    link_id: Mapped[str] = mapped_column(
        String(26), primary_key=True, default=_new_id
    )
    field_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("ba_extracted_fields.field_id"),
        nullable=False, index=True,
    )
    evidence_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("ba_evidence.evidence_id"),
        nullable=False, index=True,
    )
    evidence_role: Mapped[str] = mapped_column(
        String(30), nullable=False, default="primary"
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        Index(
            "ix_ba_links_field_seq", "field_id", "sequence", unique=True,
        ),
    )


class NoticeParticipant(Base):
    """公告参与关系 - 组织在具体公告中扮演的业务角色。

    同一个组织在不同项目中可能是采购人、供应商或联合体成员。
    """
    __tablename__ = "ba_notice_participants"

    participant_id: Mapped[str] = mapped_column(
        String(26), primary_key=True, default=_new_id
    )
    notice_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("ba_tender_notices.notice_id"),
        nullable=False, index=True,
    )
    version_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("ba_notice_versions.version_id"),
        nullable=True, index=True,
    )
    organization_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("ba_organizations.organization_id"),
        nullable=True, index=True,
    )
    raw_name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    participant_role: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )
    lot_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resolution_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unresolved"
    )
    evidence_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("ba_evidence.evidence_id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class ProjectIdentifier(Base):
    """项目标识 - 同一项目可能有采购编号、代理编号和内部编号。"""
    __tablename__ = "ba_project_identifiers"

    identifier_id: Mapped[str] = mapped_column(
        String(26), primary_key=True, default=_new_id
    )
    project_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("ba_tender_projects.project_id"),
        nullable=False, index=True,
    )
    # procurement / agency / platform / lot / other
    identifier_type: Mapped[str] = mapped_column(String(50), nullable=False)
    raw_value: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    normalized_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    issuing_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("ba_notice_sources.notice_source_id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


# 导出所有模型类，方便 Alembic / 迁移脚本一次性导入
__all__ = [
    "Evidence",
    "ExtractedField",
    "FieldEvidenceLink",
    "NoticeParticipant",
    "NoticeSource",
    "NoticeVersion",
    "Organization",
    "ProjectIdentifier",
    "TenderNotice",
    "TenderProject",
]
