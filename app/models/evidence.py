"""W2-05 证据对象和关联表入库。

对应总规划 v4.1 第四章 4.9 FieldEvidenceLink + 4.10 Evidence + 第六章 6.1「生成多证据对象」。

三张表：
1. ExtractedField：抽取字段表（关联 Tender）
   - support_level / support_reason / primary_evidence_id（W2-05 新增）
2. Evidence：证据对象表
   - evidence_text / context_before / context_after
   - raw_start / raw_end / normalized_start / normalized_end
   - match_method / verified / verification_rule
   - snapshot_sha256 / raw_text_sha256
3. FieldEvidenceLink：字段-证据关联表
   - evidence_role（primary / context / qualifier / derivation_input / contradiction）
   - sequence / is_required

工程约束：
- 历史版本不得被新版本覆盖（version_id 递增）
- 无证据字段不得进入高可信（support_level=unsupported 时不得 direct/equivalent）
- 多值字段不得强行压平（同 field_name 可有多条 ExtractedField）
- ULID 使用 ulid-py 库（project_memory 要求）
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base
from app.models.user import utc_now

# ========== 枚举（Sol 要求：backend/enums.py 如需新增枚举） ==========

# 抽取支持度（Sol 第四章 4.9 + 第六章 6.2）
SUPPORT_LEVELS = {
    "direct": "直接证据（原文精确出现）",
    "equivalent": "等价证据（规范化后匹配）",
    "inferred": "推导证据（L3/L4 匹配或确定性校验推导）",
    "unsupported": "无依据",
    "contradicted": "冲突证据",
}

# 字段状态（不修改现有枚举，W2-05 只用）
FIELD_STATUSES = {
    "present": "字段存在且有值",
    "absent": "字段不存在",
    "ambiguous": "字段存在但含义模糊",
    "multi_value": "多值字段",
}

# 证据角色（Sol 第四章 4.9）
EVIDENCE_ROLES = {
    "primary": "主证据",
    "context": "上下文证据",
    "qualifier": "限定条件证据",
    "derivation_input": "推导输入证据",
    "contradiction": "冲突证据",
}

# 匹配方法（W2-03 五级降级）
MATCH_METHODS = {
    "exact": "L1 精确匹配",
    "stripped": "L2 去空白匹配",
    "no_punct": "L3 去标点匹配",
    "substring": "L4 核心子串匹配",
    "not_found": "L5 未匹配",
}

# 六类核心字段（Sol 要求：不修改字段定义）
CORE_FIELDS = {
    "project_identifier": "项目编号",
    "purchaser_name": "采购人",
    "winner_name": "中标人",
    "amount": "金额及类型",
    "publish_date": "发布日期",
    "bid_deadline": "投标截止日期",
}


class ExtractedField(Base):
    """抽取字段表（W2-05）。

    关联 Tender，存储 LLM 抽取的字段值 + 程序验证结果。

    约束：
    - 多值字段不得强行压平：同 (tender_id, field_name) 可有多条记录
    - 无证据字段不得进入高可信：support_level=unsupported 时不得 direct/equivalent
    - 历史版本不得被新版本覆盖：version_id 递增，旧版本保留
    """

    __tablename__ = "extracted_fields"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 关联 Tender
    tender_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tenders.id"), nullable=False, index=True
    )
    # 字段名（六类核心字段之一）
    field_name: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # 字段状态（present/absent/ambiguous/multi_value）
    field_status: Mapped[str] = mapped_column(String(20), nullable=False, default="present")
    # 原始值（LLM 抽取，不得覆盖）
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 规范化值（程序校验后）
    normalized_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 金额类型（budget/ceiling/award/contract/unit_price，仅 amount 字段使用）
    amount_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 货币（CNY/USD/EUR，仅 amount 字段使用）
    currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # 分包 ID（多分包场景）
    lot_id: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # ==== W2-05 新增：抽取支持度 ====
    support_level: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unsupported"
    )
    support_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    primary_evidence_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("evidence.id"), nullable=True
    )

    # ==== 推导规则（Sol 要求：保存推导规则）====
    derivation_rule: Mapped[str | None] = mapped_column(Text, nullable=True)
    validator_version: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # ==== 版本控制（历史版本不得被新版本覆盖）====
    version_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # ==== W3-07 新增：展示等级 + 交叉验证状态 ====
    # 展示等级（high/review/low）
    display_grade: Mapped[str] = mapped_column(
        String(16), nullable=False, default="review"
    )
    # 是否被多源交叉验证过
    cross_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ExtractedField id={self.id} tender_id={self.tender_id} "
            f"field_name={self.field_name!r} support={self.support_level!r}>"
        )


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
