"""Deletion by source_url scope (v4.1 sec 13.3)."""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tender import Tender
from app.models.tender_project import NoticeParticipant, NoticeSource
from app.models.job import ScrapeJob
from app.utils.logger import get_logger

from app.services.data_deletion._models import (
    DeletionResult,
    DeletionScope,
)

logger = get_logger("data_deletion")


class _SourceUrlMixin:
    """Mixin: delete all data tied to a specific source URL."""

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
