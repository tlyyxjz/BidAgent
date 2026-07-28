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
    candidate_evidences: List[CandidateEvidence] = Field(
        default_factory=list,
        description="候选证据列表（1～3 段）",
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
    temperature: float = 0.0  # P1-15: 记录请求参数（约束 #49）
    max_tokens: int = 0       # P1-15: 记录请求参数（约束 #49）


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
