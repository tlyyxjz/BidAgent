"""W2-01 LLM 字段抽取 Schema。

对应总规划 v4.1 第六章 6.1「LLM 输出字段值和候选证据列表」。

定义 LLM 抽取的输出结构：
- 六类核心字段（项目编号、采购人、中标人、金额及类型、发布日期、投标截止日期）
- 每个字段值对应的候选证据文本片段（1～3 段）
- 证据角色标注（primary / context / qualifier）

约束：
- 不修改六类字段定义
- 不修改字段状态枚举
- 候选证据文本必须是原文中的连续片段，不得改写
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class CandidateEvidence(BaseModel):
    """候选证据（LLM 输出）。

    约束：
    - evidence_text 必须是原文中的连续片段，不得改写
    - role 标注证据角色
    """

    evidence_text: str = Field(..., description="证据文本（原文连续片段，不得改写）")
    role: str = Field(
        "primary",
        description="证据角色：primary（主证据）/ context（上下文）/ qualifier（限定条件）",
    )


class FieldExtraction(BaseModel):
    """单字段抽取结果。"""

    field_name: str = Field(
        ...,
        description="字段名：project_identifier / purchaser_name / winner_name / amount / publish_date / bid_deadline",
    )
    field_status: str = Field(
        "present",
        description="字段状态：present（存在）/ absent（不存在）/ ambiguous（模糊）/ multi_value（多值）",
    )
    raw_value: Optional[str] = Field(None, description="字段原始值")
    amount_type: Optional[str] = Field(
        None,
        description="金额类型（仅 amount 字段）：budget / ceiling / award / contract / unit_price",
    )
    currency: Optional[str] = Field(
        None, description="货币（仅 amount 字段）：CNY / USD / EUR"
    )
    lot_id: Optional[str] = Field(None, description="分包 ID（多分包场景）")
    # ==== v4.1 §7.2 多金额模型：补齐 8 键 ====
    normalized_value: Optional[str] = Field(
        None,
        description="归一化数值（程序校验后，如 '1285000.00'）",
    )
    original_unit: Optional[str] = Field(
        None,
        description="原始单位（如 '万元' / '元' / '亿美元'）",
    )
    tax_status: Optional[str] = Field(
        None,
        description="含税状态：included / excluded / unknown",
    )
    display_precision: Optional[str] = Field(
        None,
        description="原文显示精度（如 '0.01万元' / '1元'），用于金额容差判定",
    )
    candidate_evidences: List[CandidateEvidence] = Field(
        default_factory=list,
        description="候选证据列表（1～3 段）",
    )
    # ==== W3-07 新增：展示等级 + 支持度 + 交叉验证状态 ====
    support_level: str = Field(
        "unsupported",
        description="抽取支持度：direct/equivalent/inferred/unsupported/contradicted",
    )
    cross_verified: bool = Field(
        False,
        description="是否被多源交叉验证",
    )
    # ==== v4.1 §4.8 三维质量维度（6 态 enum）====
    cross_verify_status: str = Field(
        "single_source",
        description="交叉验证状态：independent/consistent_unknown/same_origin/version_difference/conflict/single_source",
    )
    source_quality_snapshot: Optional[str] = Field(
        None,
        description="抽取时来源质量快照（official_original 等 6 类）",
    )
    field_type: Optional[str] = Field(
        None,
        description="字段类型：amount/date/organization/identifier/fact/text",
    )
    semantic_role: Optional[str] = Field(
        None,
        description="字段业务语义角色（用于 FactAssertionKey 跨源比较）",
    )
    value_count: int = Field(
        1,
        description="多值字段计数（同一 field_name 的值数量）",
    )
    display_grade: str = Field(
        "review",
        description="展示等级：high/review/low（由 compute_display_grade 自动计算）",
    )


class ExtractionResult(BaseModel):
    """LLM 抽取完整结果（含元信息）。

    对应 Sol 第六章 6.1 工作流程「LLM 输出字段值和候选证据列表」。
    """

    fields: List[FieldExtraction] = Field(
        ..., description="六类核心字段抽取结果"
    )
    model_id: str = Field("unknown", description="模型标识")
    prompt_hash: str = Field("", description="prompt 哈希（prompt 变更需记录）")
    total_tokens: int = Field(0, description="总 token 数")
    latency_ms: int = Field(0, description="延迟毫秒")
    error: Optional[str] = Field(None, description="错误信息（失败时记录）")


# 六类核心字段名（Sol 要求：不修改字段定义）
CORE_FIELD_NAMES = [
    "project_identifier",
    "purchaser_name",
    "winner_name",
    "amount",
    "publish_date",
    "bid_deadline",
]

# 证据角色枚举（W2-01 子集，与 W2-05 EVIDENCE_ROLES 一致）
EXTRACTION_EVIDENCE_ROLES = {"primary", "context", "qualifier"}

# 字段状态枚举
EXTRACTION_FIELD_STATUSES = {"present", "absent", "ambiguous", "multi_value"}

# 金额类型枚举（与 W2-04 AMOUNT_TYPES 一致）
EXTRACTION_AMOUNT_TYPES = {"budget", "ceiling", "award", "contract", "unit_price"}

# 金标字段状态（v4.1 §10.3，6 种，用于金标标注，不同于 LLM 抽取的 field_status）
GOLD_FIELD_STATUSES = {
    "present": "字段存在且有值",
    "absent": "字段不存在",
    "not_applicable": "字段不适用（如招标公告没有中标人）",
    "ambiguous": "字段存在但含义模糊",
    "attachment_only": "字段仅在附件中（正文未提及）",
    "unreadable": "字段存在但不可读（如扫描件模糊）",
}
