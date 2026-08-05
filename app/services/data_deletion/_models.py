"""Shared data models for data deletion service (v4.1 sec 13.3)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class DeletionScope(str, Enum):
    """Deletion scope enum (v4.1 sec 13.3)."""

    SOURCE_URL = "source_url"
    SOURCE_PLATFORM = "source_platform"
    NOTICE_SOURCE_INSTANCE = "notice_source_instance"
    PAGE_SNAPSHOT = "page_snapshot"
    USER_AUTHORIZED_DATA = "user_authorized_data"


@dataclass
class DeletionResult:
    """Result of a deletion operation."""

    scope: DeletionScope
    target: str
    deleted_counts: dict[str, int] = field(default_factory=dict)
    deleted_files: list[str] = field(default_factory=list)
    audit_id: str = ""
    executed_at: float = field(default_factory=time.time)
    error: Optional[str] = None

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope.value,
            "target": self.target,
            "deleted_counts": self.deleted_counts,
            "deleted_files": self.deleted_files,
            "audit_id": self.audit_id,
            "executed_at": self.executed_at,
            "error": self.error,
        }


@dataclass
class DeletionAuditRecord:
    """Audit record for a deletion operation (v4.1 sec 13.3).

    Required fields per spec:
    - request_basis: reason for deletion
    - operator: who initiated the deletion
    - deletion_scope: what was deleted
    - executed_at: when
    - audit_record: JSON details
    """

    request_basis: str
    operator: str
    deletion_scope: str
    target: str
    executed_at: float = field(default_factory=time.time)
    audit_record: dict[str, Any] = field(default_factory=dict)
    audit_id: str = ""

    def __post_init__(self) -> None:
        if not self.audit_id:
            self.audit_id = f"del_{int(self.executed_at * 1000)}"
