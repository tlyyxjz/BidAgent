"""ExtractedField 抽取字段表 ORM（W2-05）。

从 `app.models.evidence` 拆出。
关联 Tender，存储 LLM 抽取的字段值 + 程序验证结果。

约束：
- 多值字段不得强行压平：同 (tender_id, field_name) 可有多条记录
- 无证据字段不得进入高可信：support_level=unsupported 时不得 direct/equivalent
- 历史版本不得被新版本覆盖：version_id 递增，旧版本保留
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base
from app.models.user import utc_now


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
    # ==== v4.1 §7.2 多金额模型：补齐 8 键 ====
    # 原始单位（如 '万元' / '元' / '亿美元'）
    original_unit: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 含税状态：included / excluded / unknown
    tax_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 原文显示精度（如 '0.01万元' / '1元'），用于金额容差判定
    display_precision: Mapped[str | None] = mapped_column(String(30), nullable=True)

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

    # ==== v4.1 §4.8 三维质量维度独立保存（6 态 enum 替代布尔）====
    # 交叉验证状态（6 种：independent/consistent_unknown/same_origin/version_difference/conflict/single_source）
    # 注：保留 cross_verified 布尔做向后兼容；新逻辑应使用 cross_verify_status
    # 关系：cross_verified = cross_verify_status in {"independent", "consistent_unknown"}
    cross_verify_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="single_source"
    )
    # 抽取时来源质量快照（保存抽取时的 source_quality 类别，避免后续来源质量变化影响追溯）
    source_quality_snapshot: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # 字段类型（amount/date/organization/identifier/fact/text，用于 FactAssertionKey 跨源比较）
    field_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 字段业务语义角色（如 budget_amount/award_amount/purchaser/winner，用于 FactAssertionKey）
    semantic_role: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # 多值字段计数（同一 field_name 的值数量，避免压平）
    value_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # ==== W3-05 新增：展示等级规则版本 ====
    # 规则版本号（如 v0.1-calib / v1.0-frozen），用于追溯字段展示等级是基于哪版规则计算的
    # W3 周验收要求："展示等级包含规则版本"
    display_rule_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="v0.1-calib"
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


__all__ = ["ExtractedField"]
