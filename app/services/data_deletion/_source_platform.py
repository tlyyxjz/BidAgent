"""Deletion by source_platform scope (v4.1 sec 13.3)."""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tender import Tender
from app.models.tender_project import (
    NoticeParticipant,
    NoticeSource,
    TenderNotice,
)
from app.utils.logger import get_logger

from app.services.data_deletion._models import (
    DeletionResult,
    DeletionScope,
)

logger = get_logger("data_deletion")


class _SourcePlatformMixin:
    """Mixin: delete all data tied to a specific source platform."""

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
