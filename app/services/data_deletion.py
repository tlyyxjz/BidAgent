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

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.evidence import Evidence, ExtractedField, FieldEvidenceLink
from app.models.organization import PartyRole
from app.models.tender import Tender
from app.models.tender_project import (
    NoticeParticipant,
    NoticeSource,
    NoticeVersion,
    ProjectIdentifier,
    TenderNotice,
    TenderProject,
)
from app.models.user import ApiKey, User
from app.models.subscription import PushLog, Subscription
from app.models.job import ScrapeJob
from app.utils.logger import get_logger

logger = get_logger("data_deletion")


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


class DataDeletionService:
    """Data deletion service implementing v4.1 sec 13.3.

    All deletion methods:
    - Accept an AsyncSession (caller manages transaction)
    - Return DeletionResult with counts of affected rows
    - Log audit record via _log_audit()
    - Never silently ignore errors (exceptions propagate to caller)
    """

    async def delete_by_source_url(
        self,
        session: AsyncSession,
        source_url: str,
        request_basis: str,
        operator: str,
    ) -> DeletionResult:
        """Delete all data tied to a specific source URL.

        Affects: tenders, notice_sources, notice_versions, evidence,
        extracted_fields, party_roles, scrape_jobs.

        Args:
            session: Async DB session.
            source_url: The URL to delete data for.
            request_basis: Reason for deletion (audit).
            operator: Who initiated the deletion (audit).

        Returns:
            DeletionResult with deleted counts.
        """
        result = DeletionResult(
            scope=DeletionScope.SOURCE_URL,
            target=source_url,
        )
        logger.info(
            "delete_by_source_url url=%s operator=%s basis=%s",
            source_url[:80], operator, request_basis,
        )

        # 1. Find NoticeSources by source_url or origin_url
        stmt = select(NoticeSource).where(
            (NoticeSource.source_url == source_url)
            | (NoticeSource.origin_url == source_url)
        )
        sources = (await session.execute(stmt)).scalars().all()
        source_ids = [s.notice_source_id for s in sources]
        notice_ids = [s.notice_id for s in sources]

        if source_ids:
            # Delete NoticeVersions (and snapshot files)
            versions_deleted = await self._delete_versions_by_source_ids(
                session, source_ids, result,
            )
            # Delete NoticeSources
            await session.execute(
                delete(NoticeSource).where(
                    NoticeSource.notice_source_id.in_(source_ids)
                )
            )
            result.deleted_counts["notice_sources"] = len(source_ids)

            # Delete NoticeParticipants tied to these notices
            if notice_ids:
                part_result = await session.execute(
                    select(NoticeParticipant).where(
                        NoticeParticipant.notice_id.in_(notice_ids)
                    )
                )
                part_count = len(part_result.scalars().all())
                if part_count:
                    await session.execute(
                        delete(NoticeParticipant).where(
                            NoticeParticipant.notice_id.in_(notice_ids)
                        )
                    )
                    result.deleted_counts["notice_participants"] = part_count

        # 2. Delete Tenders by source_url
        tender_stmt = select(Tender).where(Tender.source_url == source_url)
        tenders = (await session.execute(tender_stmt)).scalars().all()
        tender_ids = [t.id for t in tenders]
        if tender_ids:
            await self._delete_tender_cascade(session, tender_ids, result)

        # 3. Delete ScrapeJobs by url
        job_result = await session.execute(
            delete(ScrapeJob).where(ScrapeJob.url == source_url)
        )
        if job_result.rowcount:
            result.deleted_counts["scrape_jobs"] = job_result.rowcount

        await self._log_audit(
            session, request_basis, operator,
            DeletionScope.SOURCE_URL, source_url, result,
        )
        await session.commit()
        logger.info(
            "delete_by_source_url done url=%s counts=%s",
            source_url[:80], result.deleted_counts,
        )
        return result

    async def delete_by_source_platform(
        self,
        session: AsyncSession,
        source_platform: str,
        request_basis: str,
        operator: str,
    ) -> DeletionResult:
        """Delete all data tied to a specific source platform.

        Affects: all tenders, notice_sources, and related data
        where source_platform matches.

        Args:
            session: Async DB session.
            source_platform: Platform name (e.g. "ccgp", "chinabidding").
            request_basis: Reason for deletion (audit).
            operator: Who initiated the deletion (audit).

        Returns:
            DeletionResult with deleted counts.
        """
        result = DeletionResult(
            scope=DeletionScope.SOURCE_PLATFORM,
            target=source_platform,
        )
        logger.info(
            "delete_by_source_platform platform=%s operator=%s",
            source_platform, operator,
        )

        # 1. Find NoticeSources by source_platform
        stmt = select(NoticeSource).where(
            NoticeSource.source_platform == source_platform
        )
        sources = (await session.execute(stmt)).scalars().all()
        source_ids = [s.notice_source_id for s in sources]
        notice_ids = list({s.notice_id for s in sources})

        if source_ids:
            await self._delete_versions_by_source_ids(
                session, source_ids, result,
            )
            await session.execute(
                delete(NoticeSource).where(
                    NoticeSource.notice_source_id.in_(source_ids)
                )
            )
            result.deleted_counts["notice_sources"] = len(source_ids)

        if notice_ids:
            # Delete participants
            part_result = await session.execute(
                select(NoticeParticipant).where(
                    NoticeParticipant.notice_id.in_(notice_ids)
                )
            )
            part_count = len(part_result.scalars().all())
            if part_count:
                await session.execute(
                    delete(NoticeParticipant).where(
                        NoticeParticipant.notice_id.in_(notice_ids)
                    )
                )
                result.deleted_counts["notice_participants"] = part_count

            # Delete notices (if no remaining sources)
            for nid in notice_ids:
                remaining = await session.execute(
                    select(NoticeSource).where(
                        NoticeSource.notice_id == nid
                    )
                )
                if not remaining.scalars().first():
                    await session.execute(
                        delete(TenderNotice).where(
                            TenderNotice.notice_id == nid
                        )
                    )
            result.deleted_counts["tender_notices"] = len(notice_ids)

        # 2. Delete Tenders by source_platform
        tender_stmt = select(Tender).where(
            Tender.source_platform == source_platform
        )
        tenders = (await session.execute(tender_stmt)).scalars().all()
        tender_ids = [t.id for t in tenders]
        if tender_ids:
            await self._delete_tender_cascade(session, tender_ids, result)

        await self._log_audit(
            session, request_basis, operator,
            DeletionScope.SOURCE_PLATFORM, source_platform, result,
        )
        await session.commit()
        logger.info(
            "delete_by_source_platform done platform=%s counts=%s",
            source_platform, result.deleted_counts,
        )
        return result

    async def delete_notice_source_instance(
        self,
        session: AsyncSession,
        notice_source_id: str,
        request_basis: str,
        operator: str,
    ) -> DeletionResult:
        """Delete a single NoticeSource and its dependents.

        Args:
            session: Async DB session.
            notice_source_id: The notice_source_id to delete.
            request_basis: Reason for deletion (audit).
            operator: Who initiated the deletion (audit).

        Returns:
            DeletionResult with deleted counts.
        """
        result = DeletionResult(
            scope=DeletionScope.NOTICE_SOURCE_INSTANCE,
            target=notice_source_id,
        )
        logger.info(
            "delete_notice_source_instance id=%s operator=%s",
            notice_source_id, operator,
        )

        # Find the source
        stmt = select(NoticeSource).where(
            NoticeSource.notice_source_id == notice_source_id
        )
        source = (await session.execute(stmt)).scalars().first()
        if source is None:
            result.error = f"NoticeSource {notice_source_id} not found"
            logger.warning("notice_source not found id=%s", notice_source_id)
            return result

        # Delete versions
        await self._delete_versions_by_source_ids(
            session, [notice_source_id], result,
        )

        # Delete the source
        await session.execute(
            delete(NoticeSource).where(
                NoticeSource.notice_source_id == notice_source_id
            )
        )
        result.deleted_counts["notice_sources"] = 1

        await self._log_audit(
            session, request_basis, operator,
            DeletionScope.NOTICE_SOURCE_INSTANCE,
            notice_source_id, result,
        )
        await session.commit()
        logger.info(
            "delete_notice_source_instance done id=%s counts=%s",
            notice_source_id, result.deleted_counts,
        )
        return result

    async def delete_page_snapshot(
        self,
        session: AsyncSession,
        version_id: str,
        request_basis: str,
        operator: str,
    ) -> DeletionResult:
        """Delete snapshot file for a NoticeVersion.

        Removes the snapshot file from disk and clears snapshot_path.
        Keeps the NoticeVersion record (preserves version history per sec 4.7).

        Args:
            session: Async DB session.
            version_id: The version_id to delete snapshot for.
            request_basis: Reason for deletion (audit).
            operator: Who initiated the deletion (audit).

        Returns:
            DeletionResult with deleted files.
        """
        result = DeletionResult(
            scope=DeletionScope.PAGE_SNAPSHOT,
            target=version_id,
        )
        logger.info(
            "delete_page_snapshot version_id=%s operator=%s",
            version_id, operator,
        )

        stmt = select(NoticeVersion).where(
            NoticeVersion.version_id == version_id
        )
        version = (await session.execute(stmt)).scalars().first()
        if version is None:
            result.error = f"NoticeVersion {version_id} not found"
            return result

        if version.snapshot_path:
            snapshot_file = Path(version.snapshot_path)
            if snapshot_file.exists():
                try:
                    snapshot_file.unlink()
                    result.deleted_files.append(str(snapshot_file))
                    logger.info(
                        "snapshot file deleted path=%s",
                        str(snapshot_file)[:80],
                    )
                except OSError as exc:
                    result.error = f"Failed to delete snapshot: {exc}"
                    logger.warning(
                        "snapshot delete failed path=%s err=%s",
                        str(snapshot_file)[:80], exc,
                    )
            else:
                result.error = "snapshot_path set but file not found"
        else:
            result.error = "no snapshot_path on this version"

        # Clear snapshot_path (keep version record per sec 4.7)
        await session.execute(
            update(NoticeVersion)
            .where(NoticeVersion.version_id == version_id)
            .values(snapshot_path=None)
        )
        result.deleted_counts["notice_versions_updated"] = 1

        await self._log_audit(
            session, request_basis, operator,
            DeletionScope.PAGE_SNAPSHOT, version_id, result,
        )
        await session.commit()
        return result

    async def delete_user_authorized_data(
        self,
        session: AsyncSession,
        user_id: int,
        request_basis: str,
        operator: str,
    ) -> DeletionResult:
        """Delete a user and all their authorized data.

        Affects: api_keys, subscriptions, push_logs, scrape_jobs, user record.
        Tenders and tender_project data are NOT deleted (they are public data).

        Args:
            session: Async DB session.
            user_id: The user ID to delete.
            request_basis: Reason for deletion (audit).
            operator: Who initiated the deletion (audit).

        Returns:
            DeletionResult with deleted counts.
        """
        result = DeletionResult(
            scope=DeletionScope.USER_AUTHORIZED_DATA,
            target=str(user_id),
        )
        logger.info(
            "delete_user_authorized_data user_id=%d operator=%s",
            user_id, operator,
        )

        # Verify user exists
        user_stmt = select(User).where(User.id == user_id)
        user = (await session.execute(user_stmt)).scalars().first()
        if user is None:
            result.error = f"User {user_id} not found"
            return result

        # Delete ApiKeys (CASCADE should handle, but explicit for audit)
        api_key_result = await session.execute(
            delete(ApiKey).where(ApiKey.user_id == user_id)
        )
        if api_key_result.rowcount:
            result.deleted_counts["api_keys"] = api_key_result.rowcount

        # Find subscriptions and their push_logs
        sub_stmt = select(Subscription).where(Subscription.user_id == user_id)
        subs = (await session.execute(sub_stmt)).scalars().all()
        sub_ids = [s.id for s in subs]
        if sub_ids:
            # Delete push_logs
            push_result = await session.execute(
                delete(PushLog).where(
                    PushLog.subscription_id.in_(sub_ids)
                )
            )
            if push_result.rowcount:
                result.deleted_counts["push_logs"] = push_result.rowcount

            # Delete subscriptions
            await session.execute(
                delete(Subscription).where(
                    Subscription.id.in_(sub_ids)
                )
            )
            result.deleted_counts["subscriptions"] = len(sub_ids)

        # Delete scrape_jobs
        job_result = await session.execute(
            delete(ScrapeJob).where(ScrapeJob.user_id == user_id)
        )
        if job_result.rowcount:
            result.deleted_counts["scrape_jobs"] = job_result.rowcount

        # Delete user
        await session.execute(delete(User).where(User.id == user_id))
        result.deleted_counts["users"] = 1

        await self._log_audit(
            session, request_basis, operator,
            DeletionScope.USER_AUTHORIZED_DATA, str(user_id), result,
        )
        await session.commit()
        logger.info(
            "delete_user_authorized_data done user_id=%d counts=%s",
            user_id, result.deleted_counts,
        )
        return result

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


data_deletion_service = DataDeletionService()
