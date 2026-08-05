"""采购项目层实体：TenderProject + ProjectIdentifier。

对应《标小智 项目总体规划 v4.1》第四章 4.2 节。
从 `app.models.tender_project` 拆出，按四层聚合的根实体层组织。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models._tender_ulid import _new_ulid
from app.models.database import Base
from app.models.user import utc_now


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


__all__ = ["ProjectIdentifier", "TenderProject"]
