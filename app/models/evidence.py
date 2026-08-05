"""W2-05 证据对象和关联表入库（re-export 入口）。

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

为满足单文件 ≤ 300 行的工程约束，本模块已拆分为子模块：
- `_evidence_enums`：枚举常量（SUPPORT_LEVELS / EVIDENCE_ROLES / ... ）
- `_extracted_field`：ExtractedField ORM
- `_evidence_model`：Evidence ORM
- `_field_evidence_link`：FieldEvidenceLink ORM

本文件仅做 re-export，保持对外公开 API 不变，向后兼容所有
`from app.models.evidence import XXX` 的导入路径。

工程约束：
- 历史版本不得被新版本覆盖（version_id 递增）
- 无证据字段不得进入高可信（support_level=unsupported 时不得 direct/equivalent）
- 多值字段不得强行压平（同 field_name 可有多条 ExtractedField）
- ULID 使用 ulid-py 库（project_memory 要求）
"""

from __future__ import annotations

from app.models._evidence_enums import (  # noqa: F401
    CORE_FIELDS,
    CROSS_VERIFY_STATUSES,
    EVIDENCE_ROLES,
    FIELD_STATUSES,
    FIELD_TYPES,
    MATCH_METHODS,
    SOURCE_QUALITY_TYPES,
    SUPPORT_LEVELS,
)
from app.models._evidence_model import Evidence  # noqa: F401
from app.models._extracted_field import ExtractedField  # noqa: F401
from app.models._field_evidence_link import FieldEvidenceLink  # noqa: F401

__all__ = [
    "CORE_FIELDS",
    "CROSS_VERIFY_STATUSES",
    "EVIDENCE_ROLES",
    "FIELD_STATUSES",
    "FIELD_TYPES",
    "MATCH_METHODS",
    "SOURCE_QUALITY_TYPES",
    "SUPPORT_LEVELS",
    "Evidence",
    "ExtractedField",
    "FieldEvidenceLink",
]
