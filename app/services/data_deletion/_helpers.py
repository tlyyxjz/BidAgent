"""Shared private helpers used across all deletion scopes (v4.1 sec 13.3)."""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.evidence import Evidence, ExtractedField, FieldEvidenceLink
from app.models.organization import PartyRole
from app.models.tender import Tender
from app.models.tender_project import NoticeVersion
from app.models.subscription import PushLog
from app.utils.logger import get_logger

from app.services.data_deletion._models import (
    DeletionAuditRecord,
    DeletionResult,
    DeletionScope,
)

logger = get_logger("data_deletion")


class _DeletionHelpers:
    """Shared private helpers used across all deletion scopes."""

    async def _delete_versions_by_source_ids(
        self,
        session: AsyncSession,
        source_ids: list[str],
        result: DeletionResult,
    ) -> int:
        """Delete NoticeVersions by source IDs, including snapshot files.

        Returns count of versions deleted.
        """
        if not source_ids:
            return 0

        ver_stmt = select(NoticeVersion).where(
            NoticeVersion.notice_source_id.in_(source_ids)
        )
        versions = (await session.execute(ver_stmt)).scalars().all()

        # Delete snapshot files from disk
        for ver in versions:
            if ver.snapshot_path:
                snapshot_file = Path(ver.snapshot_path)
                if snapshot_file.exists():
                    try:
                        snapshot_file.unlink()
                        result.deleted_files.append(str(snapshot_file))
                    except OSError as exc:
                        logger.warning(
                            "snapshot delete failed v=%s err=%s",
                            ver.version_id, exc,
                        )

        # Delete version records
        if versions:
            await session.execute(
                delete(NoticeVersion).where(
                    NoticeVersion.version_id.in_(
                        [v.version_id for v in versions]
                    )
                )
            )
            result.deleted_counts["notice_versions"] = len(versions)
            return len(versions)
        return 0

    async def _delete_tender_cascade(
        self,
        session: AsyncSession,
        tender_ids: list[int],
        result: DeletionResult,
    ) -> None:
        """Cascade delete tender and its related data."""
        if not tender_ids:
            return

        # Delete FieldEvidenceLinks via ExtractedFields
        field_stmt = select(ExtractedField).where(
            ExtractedField.tender_id.in_(tender_ids)
        )
        fields = (await session.execute(field_stmt)).scalars().all()
        field_ids = [f.id for f in fields]
        if field_ids:
            await session.execute(
                delete(FieldEvidenceLink).where(
                    FieldEvidenceLink.field_id.in_(field_ids)
                )
            )
            result.deleted_counts["field_evidence_links"] = len(field_ids)

        # Delete ExtractedFields
        await session.execute(
            delete(ExtractedField).where(
                ExtractedField.tender_id.in_(tender_ids)
            )
        )
        result.deleted_counts["extracted_fields"] = len(fields)

        # Delete Evidence
        ev_result = await session.execute(
            delete(Evidence).where(Evidence.tender_id.in_(tender_ids))
        )
        if ev_result.rowcount:
            result.deleted_counts["evidence"] = ev_result.rowcount

        # Delete PartyRoles
        pr_result = await session.execute(
            delete(PartyRole).where(PartyRole.tender_id.in_(tender_ids))
        )
        if pr_result.rowcount:
            result.deleted_counts["party_roles"] = pr_result.rowcount

        # Delete PushLogs
        pl_result = await session.execute(
            delete(PushLog).where(PushLog.tender_id.in_(tender_ids))
        )
        if pl_result.rowcount:
            result.deleted_counts["push_logs"] = pl_result.rowcount

        # Delete Tenders
        await session.execute(
            delete(Tender).where(Tender.id.in_(tender_ids))
        )
        result.deleted_counts["tenders"] = len(tender_ids)

    async def _log_audit(
        self,
        session: AsyncSession,
        request_basis: str,
        operator: str,
        scope: DeletionScope,
        target: str,
        result: DeletionResult,
    ) -> None:
        """Log audit record for deletion operation.

        v4.1 sec 13.3 requires recording:
        - request_basis, operator, deletion_scope, executed_at, audit_record

        We log to both:
        - Application logger (structured JSON)
        - DeletionAuditRecord dataclass (returned in result)
        """
        audit = DeletionAuditRecord(
            request_basis=request_basis,
            operator=operator,
            deletion_scope=scope.value,
            target=target,
            audit_record=result.to_audit_dict(),
        )
        result.audit_id = audit.audit_id

        audit_json = json.dumps(audit.__dict__, ensure_ascii=False, default=str)
        logger.info("deletion_audit %s", audit_json)
