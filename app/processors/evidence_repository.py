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

本模块按功能职责拆分为子模块，此处通过 re-export 保持原公开接口兼容：
- evidence_types：数据结构（EvidenceInput / FieldInput）+ 哈希计算 + 校验约束
- evidence_writes：入库写操作（create_evidence / create_field_with_evidence 等）
- evidence_reads：查询读操作（get_field_with_evidence / get_tender_fields）
"""
from __future__ import annotations

# 保持向后兼容：原模块显式 import 了 utc_now，此处一并 re-export
from app.models.user import utc_now  # noqa: F401

from app.processors.evidence_types import (
    EvidenceInput,
    FieldInput,
    _validate_evidence_role,
    _validate_support_level,
    compute_raw_text_sha256,
    compute_snapshot_sha256,
)
from app.processors.evidence_writes import (
    _deprecate_old_versions,
    batch_insert_evidence,
    create_evidence,
    create_field_with_evidence,
    link_field_evidence,
)
from app.processors.evidence_reads import (
    get_field_with_evidence,
    get_tender_fields,
)

__all__ = [
    "EvidenceInput",
    "FieldInput",
    "compute_snapshot_sha256",
    "compute_raw_text_sha256",
    "_validate_support_level",
    "_validate_evidence_role",
    "create_evidence",
    "create_field_with_evidence",
    "link_field_evidence",
    "batch_insert_evidence",
    "_deprecate_old_versions",
    "get_field_with_evidence",
    "get_tender_fields",
    "utc_now",
]
