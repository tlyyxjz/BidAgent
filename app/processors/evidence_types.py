"""W2-05 证据入库数据结构与校验工具。

从 evidence_repository.py 拆分而来，承载：
- EvidenceInput / FieldInput 入库输入数据结构
- compute_snapshot_sha256 / compute_raw_text_sha256 哈希计算
- _validate_support_level / _validate_evidence_role 校验约束

对应总规划 v4.1 第六章 6.1「生成多证据对象」。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from app.models.evidence import (
    EVIDENCE_ROLES,
    SUPPORT_LEVELS,
)


# ========== 入库数据结构 ==========


@dataclass
class EvidenceInput:
    """证据输入（从 W2-03 EvidenceLocator 转换而来）。

    对应 W2-03 EvidenceLocation。
    """

    evidence_text: str
    context_before: Optional[str] = None
    context_after: Optional[str] = None
    raw_start: int = 0
    raw_end: int = 0
    normalized_start: int = -1
    normalized_end: int = -1
    match_method: str = "not_found"
    confidence: float = 0.0
    verified: bool = False
    verification_rule: Optional[str] = None


@dataclass
class FieldInput:
    """字段输入（从 W2-01 LLM 抽取结果 + W2-04 校验结果转换而来）。"""

    field_name: str
    field_status: str = "present"
    raw_value: Optional[str] = None
    normalized_value: Optional[str] = None
    amount_type: Optional[str] = None
    currency: Optional[str] = None
    lot_id: Optional[str] = None
    # v4.1 sec 7.2: amount object new keys (persisted to ExtractedField)
    original_unit: Optional[str] = None
    tax_status: Optional[str] = None
    display_precision: Optional[str] = None
    support_level: str = "unsupported"
    support_reason: Optional[str] = None
    derivation_rule: Optional[str] = None
    validator_version: Optional[str] = None
    # ==== v4.1 §4.8 三维质量维度 ====
    cross_verify_status: str = "single_source"
    source_quality_snapshot: Optional[str] = None
    field_type: Optional[str] = None
    semantic_role: Optional[str] = None
    value_count: int = 1
    # 关联的证据（EvidenceInput 列表 + 角色）
    evidences: List[Tuple[EvidenceInput, str]] = field(default_factory=list)
    # evidences: List[(EvidenceInput, evidence_role)]


# ========== 哈希计算 ==========


def compute_snapshot_sha256(snapshot_text: str) -> str:
    """计算快照文本的 SHA256。

    用于保证偏移量稳定复现：同 snapshot_sha256 的证据偏移量可比较。
    """
    return hashlib.sha256(snapshot_text.encode("utf-8")).hexdigest()


def compute_raw_text_sha256(raw_text: str) -> str:
    """计算原始文本的 SHA256。"""
    return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()


# ========== 校验约束 ==========


def _validate_support_level(support_level: str, has_evidence: bool) -> None:
    """校验支持度约束（Sol 要求：无证据字段不得进入高可信）。

    Args:
        support_level: 支持度
        has_evidence: 是否有证据

    Raises:
        ValueError: 如果无证据但支持度为 direct/equivalent
    """
    if support_level not in SUPPORT_LEVELS:
        raise ValueError(
            f"非法 support_level: {support_level}，合法值: {list(SUPPORT_LEVELS.keys())}"
        )

    if not has_evidence and support_level in ("direct", "equivalent"):
        raise ValueError(
            f"无证据字段不得进入高可信: support_level={support_level} 但无证据"
        )


def _validate_evidence_role(role: str) -> None:
    """校验证据角色。"""
    if role not in EVIDENCE_ROLES:
        raise ValueError(
            f"非法 evidence_role: {role}，合法值: {list(EVIDENCE_ROLES.keys())}"
        )
