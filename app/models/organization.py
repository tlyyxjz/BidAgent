"""W3-02 组织实体模型 + 消歧逻辑。

对应总规划 v4.1 第四章 4.4 Organization + 4.5 PartyRole + 第八章「基础组织实体活动画像」。

核心职责：
1. Organization 表：组织实体（采购人/代理机构/中标人/供应商统一表示）
2. PartyRole 表：组织在具体公告中的角色（ bidder/winner/purchaser/agency）
3. 名称消歧：raw_name → normalized_name → organization_id 映射
4. 公开活动画像：基于历史中标记录生成供应商画像

W3 周验收要求：基础组织实体活动画像

工程约束：
- Organization 只保存组织自身的法律实体属性，不保存业务角色（v4.1 4.4）
- 业务角色通过 PartyRole 表关联（多角色支持：同一组织可同时是采购人和代理机构）
- ULID 使用 ulid-py 库（project_memory 要求）
- 消歧基于名称规范化 + 统一社会信用代码 + SimHash 模糊匹配

拆分说明（保证单文件 ≤300 行，公开接口不变）：
- 名称规范化 + 消歧逻辑 → organization_disambiguation.py
- 供应商画像生成 → organization_profile.py
- 本文件保留：组织/角色枚举、ORM 模型（Organization/PartyRole）、组织类型推断
  并 re-export 上述模块的公开符号，保持 ``from app.models.organization import X`` 可用。
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
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base
from app.models.user import utc_now
from app.utils.logger import get_logger

# 拆分模块 re-export（保持原有 import 路径不变）
from app.models.organization_disambiguation import (  # noqa: F401
    DisambiguationResult,
    compute_name_hash,
    disambiguate_organization,
    normalize_org_name,
)
from app.models.organization_profile import (  # noqa: F401
    SupplierProfile,
    build_supplier_profile,
)

logger = get_logger("organization")


# ========== 组织实体枚举 ==========

# 组织类型（v4.1 第四章 4.4）
ORG_TYPE_GOVERNMENT = "government"        # 政府机关
ORG_TYPE_INSTITUTION = "institution"      # 事业单位
ORG_TYPE_ENTERPRISE = "enterprise"        # 企业
ORG_TYPE_SOCIAL_ORG = "social_org"        # 社会组织
ORG_TYPE_UNKNOWN = "unknown"

VALID_ORG_TYPES = (
    ORG_TYPE_GOVERNMENT,
    ORG_TYPE_INSTITUTION,
    ORG_TYPE_ENTERPRISE,
    ORG_TYPE_SOCIAL_ORG,
    ORG_TYPE_UNKNOWN,
)

# 业务角色（v4.1 第四章 4.5）
ROLE_PURCHASER = "purchaser"      # 采购人
ROLE_AGENCY = "agency"            # 代理机构
ROLE_BIDDER = "bidder"            # 投标人
ROLE_WINNER = "winner"            # 中标人
ROLE_CONSORTIUM = "consortium"    # 联合体成员

VALID_ROLES = (ROLE_PURCHASER, ROLE_AGENCY, ROLE_BIDDER, ROLE_WINNER, ROLE_CONSORTIUM)


# ========== ORM 模型 ==========

class Organization(Base):
    """组织实体表（v4.1 第四章 4.4）。

    只保存组织自身的法律实体属性，不保存业务角色。
    业务角色通过 PartyRole 表关联。

    约束：
    - unified_credit_code 唯一（若存在）
    - normalized_name 唯一（消歧后的标准名称）
    - raw_name 保留原始名称（可能有多个别名）
    """
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # ULID（project_memory 要求：使用 ulid-py 库）
    organization_id: Mapped[str] = mapped_column(
        String(26), unique=True, nullable=False, index=True
    )
    # 原始名称（公告中的名称，可能有多个别名）
    raw_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # 规范化名称（消歧后的标准名称，唯一）
    normalized_name: Mapped[str] = mapped_column(
        String(200), unique=True, nullable=False, index=True
    )
    # 统一社会信用代码（18 位，若存在则唯一）
    unified_credit_code: Mapped[str | None] = mapped_column(
        String(18), unique=True, nullable=True, index=True
    )
    # 组织类型
    org_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ORG_TYPE_UNKNOWN
    )
    # 法定代表人
    legal_representative: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 注册资本（万元）
    registered_capital: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # 注册地
    registered_address: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 成立日期
    established_date: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 经营范围
    business_scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 名称消歧置信度（0.0-1.0）
    disambiguation_confidence: Mapped[float] = mapped_column(
        Integer, nullable=False, default=0  # 存为整数 * 100，避免浮点
    )
    # 是否已人工核实
    manually_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Organization id={self.organization_id} name={self.normalized_name!r}>"


class PartyRole(Base):
    """组织角色关联表（v4.1 第四章 4.5）。

    组织在具体公告中的角色（ bidder/winner/purchaser/agency）。
    同一组织可同时是采购人和代理机构（多角色支持）。

    约束：
    - 同一组织在同一公告中可有多个角色（如采购人 + 代理机构）
    - 联合体成员通过 consortium_id 关联
    """
    __tablename__ = "party_roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 关联组织
    organization_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("organizations.organization_id"),
        nullable=False, index=True
    )
    # 关联公告（tender_id）
    tender_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tenders.id"), nullable=False, index=True
    )
    # 业务角色
    role: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # 原始名称（公告中出现的名称，可能与 normalized_name 不同）
    raw_name_in_notice: Mapped[str] = mapped_column(String(200), nullable=False)
    # 分包 ID（多分包场景，投标人/中标人按分包）
    lot_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # 联合体 ID（联合体成员关联）
    consortium_id: Mapped[str | None] = mapped_column(String(26), nullable=True, index=True)
    # 中标金额（仅 winner 角色使用）
    win_amount: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # 中标金额类型（award/contract）
    win_amount_type: Mapped[str | None] = mapped_column(String(20), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        # 同一组织在同一公告同一分包同一角色唯一
        Index(
            "idx_party_role_unique",
            "organization_id", "tender_id", "role", "lot_id",
            unique=True,
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<PartyRole org={self.organization_id} tender={self.tender_id} "
            f"role={self.role!r}>"
        )


# ========== 组织类型推断 ==========

# 组织类型关键词
_ORG_TYPE_KEYWORDS = {
    ORG_TYPE_GOVERNMENT: ("局", "委", "办", "厅", "部", "政府", "街道办事处"),
    ORG_TYPE_INSTITUTION: ("院", "校", "所", "中心", "站", "馆", "医院", "学校", "大学"),
    ORG_TYPE_ENTERPRISE: ("公司", "集团", "厂", "店", "商行"),
    ORG_TYPE_SOCIAL_ORG: ("协会", "基金会", "商会", "联合会"),
}


def infer_org_type(name: str) -> str:
    """根据名称推断组织类型。

    Args:
        name: 组织名称

    Returns:
        组织类型（government/institution/enterprise/social_org/unknown）
    """
    if not name:
        return ORG_TYPE_UNKNOWN
    for org_type, keywords in _ORG_TYPE_KEYWORDS.items():
        if any(kw in name for kw in keywords):
            return org_type
    return ORG_TYPE_UNKNOWN
