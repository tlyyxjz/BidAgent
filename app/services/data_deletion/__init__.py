"""Data deletion service (v4.1 sec 13.3).

Supports deletion by 5 scopes:
- source_url: delete all data tied to a specific URL
- source_platform: delete all data tied to a platform (e.g. ccgp)
- notice_source_instance: delete a single NoticeSource and its dependents
- page_snapshot: delete snapshot files for a NoticeVersion
- user_authorized_data: delete a user and all their data

Each deletion records an audit log entry with:
- request_basis: reason for deletion
- operator: who initiated the deletion
- deletion_scope: what was deleted
- executed_at: when
- audit_record: JSON details of affected rows/files
"""
from app.services.data_deletion._models import (
    DeletionAuditRecord,
    DeletionResult,
    DeletionScope,
)
from app.services.data_deletion.service import DataDeletionService

data_deletion_service = DataDeletionService()

__all__ = [
    "DataDeletionService",
    "DeletionAuditRecord",
    "DeletionResult",
    "DeletionScope",
    "data_deletion_service",
]
