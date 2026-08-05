"""W2-08/W4 消融实验数据类型与常量定义。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# 7 篇金标 (W1 已有 + W2 D2 验证过的)
DEFAULT_DOCS = [
    "tender_06",
    "tender_07",
    "award_05",
    "award_06",
    "correction_04",
    "correction_05",
    "multi_lot_02",
]


@dataclass
class GoldField:
    field_name: str
    gold_status: str  # v4.1 §10.3: present/absent/not_applicable/ambiguous/attachment_only/unreadable
    values: list  # [{"raw_value": ..., "acceptable_evidence_spans": [{"start","end","text"}]}]


@dataclass
class GoldDoc:
    document_id: str
    file: str
    fields: list  # [GoldField]


@dataclass
class GroupResult:
    group: str  # A/B/C
    doc_id: str
    field_name: str
    gold_status: str
    pred_status: str  # present/absent/ambiguous/multi_value/missing
    has_value: bool  # 系统是否输出了值
    has_evidence: bool  # 系统是否输出证据 (B/C 才有)
    evidence_verified: bool  # 证据是否在原文中存在 (C 才有)
    field_validated: bool  # 字段是否通过确定性校验 (C 才有)
    unjustified: bool  # 无依据输出 (有值但无证据/证据不存在)
    correct: Optional[bool]  # 字段值是否与金标一致 (None=无法判断)
    multi_value_f1: Optional[float] = None  # v4.1 sec 7.4 多值字段集合级 F1 (None=非多值字段)


@dataclass
class ExpSummary:
    group: str
    docs_count: int
    fields_total: int
    fields_with_value: int
    fields_with_evidence: int
    fields_evidence_verified: int
    fields_field_validated: int
    fields_unjustified: int
    unjustified_rate: float
    fields_correct: int
    fields_evaluable: int
    field_precision: float
    evidence_precision: float  # C 组字段级证据验证率 (已验证证据字段 / 有证据字段)
    model_id: str
    prompt_hash: str
    total_tokens: int
    latency_ms_avg: float
    invalid_docs_count: int = 0  # P2: LLM 失败被排除的文档数
    invalid_docs: list = field(default_factory=list)  # P2: 失败文档 ID 列表
    multi_value_f1_avg: float = 0.0  # v4.1 sec 7.4 多值字段平均集合级 F1
    null_false_positive_rate: float = 0.0  # v4.1 §10 空值误报率（should_not_have_value 字段中系统错误输出值的比例）
    # ==== v4.1 §10.12 实验复现信息（14 项新增，prompt_hash/model_id 已有）====
    model_role: str = "primary"
    provider: str = "deepseek"
    model_snapshot: Optional[str] = None
    request_time: str = ""
    temperature: float = 0.0
    top_p: float = 1.0
    seed: Optional[int] = None
    request_id: Optional[str] = None
    response_hash: Optional[str] = None
    normalizer_version: str = "unknown"
    evidence_rule_version: str = "unknown"
    display_rule_version: str = "unknown"
    dataset_version: Optional[str] = None
    code_commit: Optional[str] = None
