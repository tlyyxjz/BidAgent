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
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

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


# ========== 名称规范化 + 消歧 ==========

# 名称规范化清洗规则
_NAME_CLEAN_PATTERNS = [
    (re.compile(r"[（(].*?[)）]"), ""),           # 去除括号内容
    (re.compile(r"股份有限公司$"), "股份有限公司"),  # 统一后缀
    (re.compile(r"有限公司$"), "有限公司"),
    (re.compile(r"责任公司$"), "责任公司"),
    (re.compile(r"\s+"), ""),                     # 去除空白
    (re.compile(r"[·•・]"), "·"),                 # 统一间隔号
]


def normalize_org_name(raw_name: str) -> str:
    """规范化组织名称（用于消歧）。

    清洗规则：
    1. 去除括号内容（如"(上海)"）
    2. 统一公司后缀（股份有限公司/有限公司/责任公司）
    3. 去除空白
    4. 统一间隔号

    Args:
        raw_name: 原始名称

    Returns:
        规范化后的名称
    """
    if not raw_name:
        return ""
    name = raw_name.strip()
    for pattern, replacement in _NAME_CLEAN_PATTERNS:
        name = pattern.sub(replacement, name)
    return name.strip()


def compute_name_hash(normalized_name: str) -> str:
    """计算规范化名称的哈希（用于快速查重）。

    Args:
        normalized_name: 规范化后的名称

    Returns:
        SHA256 前 16 字符
    """
    return hashlib.sha256(normalized_name.encode("utf-8")).hexdigest()[:16]


# ========== 消歧结果数据类 ==========

@dataclass
class DisambiguationResult:
    """组织名称消歧结果。"""
    # 是否消歧成功
    matched: bool
    # 匹配到的 organization_id（若 matched=False 则为 None）
    organization_id: Optional[str] = None
    # 规范化后的名称
    normalized_name: str = ""
    # 名称哈希
    name_hash: str = ""
    # 置信度（0.0-1.0）
    confidence: float = 0.0
    # 匹配方式（exact_credit_code / exact_name / fuzzy_name / no_match）
    match_method: str = "no_match"
    # 候选列表（模糊匹配时可能有多个候选）
    candidates: list = None

    def __post_init__(self):
        if self.candidates is None:
            self.candidates = []


# ========== 消歧逻辑 ==========

def disambiguate_organization(
    raw_name: str,
    *,
    unified_credit_code: str = "",
    existing_orgs: list = None,
    fuzzy_threshold: int = 3,
) -> DisambiguationResult:
    """组织名称消歧。

    消歧优先级（从高到低）：
    1. unified_credit_code 精确匹配（置信度 1.0）
    2. normalized_name 精确匹配（置信度 0.95）
    3. 名称模糊匹配（SimHash 汉明距离 ≤ threshold，置信度 0.7-0.9）
    4. 无匹配（新建组织）

    Args:
        raw_name: 原始名称
        unified_credit_code: 统一社会信用代码（可选）
        existing_orgs: 现有组织列表 [(organization_id, normalized_name, unified_credit_code)]
        fuzzy_threshold: 模糊匹配汉明距离阈值（默认 3）

    Returns:
        DisambiguationResult
    """
    if not raw_name or not raw_name.strip():
        return DisambiguationResult(matched=False, match_method="empty_name")

    normalized = normalize_org_name(raw_name)
    name_hash = compute_name_hash(normalized)

    if not existing_orgs:
        return DisambiguationResult(
            matched=False,
            normalized_name=normalized,
            name_hash=name_hash,
            match_method="no_match",
        )

    # 1. unified_credit_code 精确匹配
    if unified_credit_code:
        for org_id, org_name, org_code in existing_orgs:
            if org_code and org_code == unified_credit_code:
                return DisambiguationResult(
                    matched=True,
                    organization_id=org_id,
                    normalized_name=normalized,
                    name_hash=name_hash,
                    confidence=1.0,
                    match_method="exact_credit_code",
                )

    # 2. normalized_name 精确匹配
    for org_id, org_name, org_code in existing_orgs:
        if org_name and org_name == normalized:
            return DisambiguationResult(
                matched=True,
                organization_id=org_id,
                normalized_name=normalized,
                name_hash=name_hash,
                confidence=0.95,
                match_method="exact_name",
            )

    # 3. 模糊匹配（SimHash）
    from app.processors.simhash import compute_simhash, hamming_distance

    target_hash = compute_simhash(normalized)
    candidates = []
    for org_id, org_name, org_code in existing_orgs:
        if not org_name:
            continue
        cand_hash = compute_simhash(org_name)
        if target_hash == 0 or cand_hash == 0:
            continue
        dist = hamming_distance(target_hash, cand_hash)
        if dist <= fuzzy_threshold:
            # 汉明距离越小置信度越高
            confidence = max(0.7, 0.9 - 0.1 * dist)
            candidates.append((org_id, org_name, dist, confidence))

    if candidates:
        # 按汉明距离升序，取最相似的
        candidates.sort(key=lambda x: x[2])
        best = candidates[0]
        return DisambiguationResult(
            matched=True,
            organization_id=best[0],
            normalized_name=normalized,
            name_hash=name_hash,
            confidence=best[3],
            match_method="fuzzy_name",
            candidates=candidates,
        )

    # 4. 无匹配
    return DisambiguationResult(
        matched=False,
        normalized_name=normalized,
        name_hash=name_hash,
        match_method="no_match",
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


# ========== 供应商画像生成 ==========

@dataclass
class SupplierProfile:
    """供应商公开活动画像（v4.1 第四章 4.5 + 第八章）。

    基于历史中标记录生成。
    """
    organization_id: str
    normalized_name: str
    # 中标总次数
    win_count: int = 0
    # 累计中标金额（元）
    total_win_amount: float = 0.0
    # 主要采购人（中标项目的采购人）
    main_purchasers: list = None
    # 主要代理机构
    main_agencies: list = None
    # 业务领域（基于中标项目名称聚类）
    business_areas: list = None
    # 活跃地区
    active_regions: list = None
    # 首次中标时间
    first_win_date: str = ""
    # 最近中标时间
    last_win_date: str = ""
    # 画像生成时间
    profile_generated_at: str = ""

    def __post_init__(self):
        if self.main_purchasers is None:
            self.main_purchasers = []
        if self.main_agencies is None:
            self.main_agencies = []
        if self.business_areas is None:
            self.business_areas = []
        if self.active_regions is None:
            self.active_regions = []


def build_supplier_profile(
    organization_id: str,
    normalized_name: str,
    win_records: list,
) -> SupplierProfile:
    """生成供应商画像。

    Args:
        organization_id: 组织 ID
        normalized_name: 规范化名称
        win_records: 中标记录列表，每个元素是 dict:
            {
                "win_amount": float,
                "purchaser_name": str,
                "agency_name": str,
                "project_name": str,
                "region": str,
                "win_date": str,
            }

    Returns:
        SupplierProfile
    """
    profile = SupplierProfile(
        organization_id=organization_id,
        normalized_name=normalized_name,
        win_count=len(win_records),
    )

    if not win_records:
        from datetime import datetime
        profile.profile_generated_at = datetime.utcnow().isoformat()
        return profile

    amounts = []
    purchasers = []
    agencies = []
    regions = []
    dates = []

    for rec in win_records:
        # 金额
        try:
            amt = float(rec.get("win_amount", 0) or 0)
            amounts.append(amt)
        except (ValueError, TypeError):
            pass
        # 采购人
        if rec.get("purchaser_name"):
            purchasers.append(rec["purchaser_name"])
        # 代理机构
        if rec.get("agency_name"):
            agencies.append(rec["agency_name"])
        # 地区
        if rec.get("region"):
            regions.append(rec["region"])
        # 日期
        if rec.get("win_date"):
            dates.append(rec["win_date"])

    # 累计金额
    profile.total_win_amount = sum(amounts) if amounts else 0.0

    # 主要采购人/代理机构（按出现频次 top 5）
    from collections import Counter
    profile.main_purchasers = [name for name, _ in Counter(purchasers).most_common(5)]
    profile.main_agencies = [name for name, _ in Counter(agencies).most_common(5)]
    profile.active_regions = [name for name, _ in Counter(regions).most_common(5)]

    # 首次/最近中标时间
    if dates:
        dates.sort()
        profile.first_win_date = dates[0]
        profile.last_win_date = dates[-1]

    from datetime import datetime
    profile.profile_generated_at = datetime.utcnow().isoformat()

    return profile
