"""W2-05 证据入库服务。

对应总规划 v4.1 第六章 6.1「生成多证据对象」+ 第四章 4.9 FieldEvidenceLink。

提供证据入库的原子接口：
- create_evidence：创建单条 Evidence
- create_field_with_evidence：创建 ExtractedField + 关联 Evidence
- link_field_evidence：关联已有 Field 和 Evidence
- batch_insert_evidence：批量入库证据（project_memory 要求 add_all）

工程约束：
- 历史版本不得被新版本覆盖：更新字段时旧版本 is_current=False
- 无证据字段不得进入高可信：support_level=unsupported 时不得 direct/equivalent
- 多值字段不得强行压平：同 field_name 可有多条 ExtractedField
- PushLog 批量入库用 add_all（project_memory 要求）
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.evidence import (
    EVIDENCE_ROLES,
    SUPPORT_LEVELS,
    Evidence,
    ExtractedField,
    FieldEvidenceLink,
)
from app.models.user import utc_now


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
    support_level: str = "unsupported"
    support_reason: Optional[str] = None
    derivation_rule: Optional[str] = None
    validator_version: Optional[str] = None
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


# ========== 入库接口 ==========


async def create_evidence(
    db: AsyncSession,
    tender_id: int,
    evidence_input: EvidenceInput,
    snapshot_text: Optional[str] = None,
    raw_text: Optional[str] = None,
) -> Evidence:
    """创建单条 Evidence。

    Args:
        db: 异步数据库 session
        tender_id: 关联 Tender ID
        evidence_input: 证据输入
        snapshot_text: 快照文本（用于计算 snapshot_sha256）
        raw_text: 原始文本（用于计算 raw_text_sha256）

    Returns:
        创建的 Evidence 对象
    """
    snapshot_sha = (
        compute_snapshot_sha256(snapshot_text) if snapshot_text else None
    )
    raw_text_sha = compute_raw_text_sha256(raw_text) if raw_text else None

    evidence = Evidence(
        tender_id=tender_id,
        evidence_text=evidence_input.evidence_text,
        context_before=evidence_input.context_before,
        context_after=evidence_input.context_after,
        raw_start=evidence_input.raw_start,
        raw_end=evidence_input.raw_end,
        normalized_start=evidence_input.normalized_start,
        normalized_end=evidence_input.normalized_end,
        match_method=evidence_input.match_method,
        confidence=int(evidence_input.confidence * 100),  # 0-100 存储
        verified=evidence_input.verified,
        verification_rule=evidence_input.verification_rule,
        snapshot_sha256=snapshot_sha,
        raw_text_sha256=raw_text_sha,
    )
    db.add(evidence)
    await db.flush()  # 获取 evidence.id
    return evidence


async def create_field_with_evidence(
    db: AsyncSession,
    tender_id: int,
    field_input: FieldInput,
    snapshot_text: Optional[str] = None,
    raw_text: Optional[str] = None,
) -> ExtractedField:
    """创建 ExtractedField + 关联的 Evidence + FieldEvidenceLink。

    Args:
        db: 异步数据库 session
        tender_id: 关联 Tender ID
        field_input: 字段输入（含关联证据）
        snapshot_text: 快照文本
        raw_text: 原始文本

    Returns:
        创建的 ExtractedField 对象
    """
    # 校验支持度约束
    has_evidence = len(field_input.evidences) > 0
    _validate_support_level(field_input.support_level, has_evidence)

    # 历史版本处理：同 (tender_id, field_name) 的旧记录 is_current=False
    # Sol 要求：多值字段（multi_value）不得强行压平，所以多值字段不触发历史版本覆盖
    if field_input.field_status != "multi_value":
        await _deprecate_old_versions(db, tender_id, field_input.field_name)

    # 创建 ExtractedField
    field_obj = ExtractedField(
        tender_id=tender_id,
        field_name=field_input.field_name,
        field_status=field_input.field_status,
        raw_value=field_input.raw_value,
        normalized_value=field_input.normalized_value,
        amount_type=field_input.amount_type,
        currency=field_input.currency,
        lot_id=field_input.lot_id,
        support_level=field_input.support_level,
        support_reason=field_input.support_reason,
        derivation_rule=field_input.derivation_rule,
        validator_version=field_input.validator_version,
        version_id=1,  # 简化：实际应查询最大 version_id + 1
        is_current=True,
    )
    db.add(field_obj)
    await db.flush()  # 获取 field_obj.id

    # 创建关联的 Evidence + FieldEvidenceLink
    primary_evidence_id: Optional[int] = None
    for seq, (ev_input, role) in enumerate(field_input.evidences):
        _validate_evidence_role(role)

        evidence = await create_evidence(
            db, tender_id, ev_input, snapshot_text, raw_text
        )

        link = FieldEvidenceLink(
            field_id=field_obj.id,
            evidence_id=evidence.id,
            evidence_role=role,
            sequence=seq,
            is_required=(role == "primary"),
        )
        db.add(link)

        if role == "primary":
            primary_evidence_id = evidence.id

    # 设置 primary_evidence_id
    if primary_evidence_id is not None:
        field_obj.primary_evidence_id = primary_evidence_id
        await db.flush()

    return field_obj


async def link_field_evidence(
    db: AsyncSession,
    field_id: int,
    evidence_id: int,
    evidence_role: str = "primary",
    sequence: Optional[int] = None,
    is_required: Optional[bool] = None,
) -> FieldEvidenceLink:
    """关联已有的 ExtractedField 和 Evidence。

    Args:
        db: 异步数据库 session
        field_id: 字段 ID
        evidence_id: 证据 ID
        evidence_role: 证据角色
        sequence: 展示顺序（None 时自动取最大 + 1）
        is_required: 是否必要（None 时根据角色自动判断）

    Returns:
        创建的 FieldEvidenceLink 对象
    """
    _validate_evidence_role(evidence_role)

    # 自动计算 sequence
    if sequence is None:
        result = await db.execute(
            select(FieldEvidenceLink)
            .where(FieldEvidenceLink.field_id == field_id)
            .order_by(FieldEvidenceLink.sequence.desc())
            .limit(1)
        )
        last_link = result.scalars().first()
        sequence = (last_link.sequence + 1) if last_link else 0

    if is_required is None:
        is_required = evidence_role == "primary"

    link = FieldEvidenceLink(
        field_id=field_id,
        evidence_id=evidence_id,
        evidence_role=evidence_role,
        sequence=sequence,
        is_required=is_required,
    )
    db.add(link)
    await db.flush()
    return link


async def batch_insert_evidence(
    db: AsyncSession,
    tender_id: int,
    evidence_inputs: List[EvidenceInput],
    snapshot_text: Optional[str] = None,
    raw_text: Optional[str] = None,
) -> List[Evidence]:
    """批量入库证据（project_memory 要求：用 add_all 而非逐条 add）。

    Args:
        db: 异步数据库 session
        tender_id: 关联 Tender ID
        evidence_inputs: 证据输入列表
        snapshot_text: 快照文本
        raw_text: 原始文本

    Returns:
        创建的 Evidence 对象列表
    """
    snapshot_sha = (
        compute_snapshot_sha256(snapshot_text) if snapshot_text else None
    )
    raw_text_sha = compute_raw_text_sha256(raw_text) if raw_text else None

    evidences = [
        Evidence(
            tender_id=tender_id,
            evidence_text=ev.evidence_text,
            context_before=ev.context_before,
            context_after=ev.context_after,
            raw_start=ev.raw_start,
            raw_end=ev.raw_end,
            normalized_start=ev.normalized_start,
            normalized_end=ev.normalized_end,
            match_method=ev.match_method,
            confidence=int(ev.confidence * 100),
            verified=ev.verified,
            verification_rule=ev.verification_rule,
            snapshot_sha256=snapshot_sha,
            raw_text_sha256=raw_text_sha,
        )
        for ev in evidence_inputs
    ]

    db.add_all(evidences)  # project_memory 要求：批量入库用 add_all
    await db.flush()
    return evidences


async def _deprecate_old_versions(
    db: AsyncSession, tender_id: int, field_name: str
) -> None:
    """将同 (tender_id, field_name) 的旧版本 is_current=False。

    Sol 要求：历史版本不得被新版本覆盖。
    """
    await db.execute(
        update(ExtractedField)
        .where(
            ExtractedField.tender_id == tender_id,
            ExtractedField.field_name == field_name,
            ExtractedField.is_current == True,  # noqa: E712
        )
        .values(is_current=False)
    )


# ========== 查询接口 ==========


async def get_field_with_evidence(
    db: AsyncSession, field_id: int
) -> Tuple[ExtractedField, List[Tuple[Evidence, FieldEvidenceLink]]]:
    """查询字段及其关联证据。

    Returns:
        (field, [(evidence, link), ...]) 按 sequence 排序
    """
    result = await db.execute(
        select(ExtractedField).where(ExtractedField.id == field_id)
    )
    field_obj = result.scalars().first()
    if not field_obj:
        raise ValueError(f"ExtractedField not found: id={field_id}")

    result = await db.execute(
        select(Evidence, FieldEvidenceLink)
        .join(FieldEvidenceLink, FieldEvidenceLink.evidence_id == Evidence.id)
        .where(FieldEvidenceLink.field_id == field_id)
        .order_by(FieldEvidenceLink.sequence)
    )
    evidence_links = [
        (evidence, link) for evidence, link in result.all()
    ]

    return field_obj, evidence_links


async def get_tender_fields(
    db: AsyncSession, tender_id: int, only_current: bool = True
) -> List[ExtractedField]:
    """查询 Tender 的所有字段。

    Args:
        db: 异步数据库 session
        tender_id: Tender ID
        only_current: 是否只返回当前版本
    """
    stmt = select(ExtractedField).where(ExtractedField.tender_id == tender_id)
    if only_current:
        stmt = stmt.where(ExtractedField.is_current == True)  # noqa: E712
    stmt = stmt.order_by(ExtractedField.field_name, ExtractedField.version_id.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())
