"""Deletion by notice_source_instance scope (v4.1 sec 13.3)."""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tender_project import NoticeSource
from app.utils.logger import get_logger

from app.services.data_deletion._models import (
    DeletionResult,
    DeletionScope,
)

logger = get_logger("data_deletion")


class _NoticeSourceInstanceMixin:
    """Mixin: delete a single NoticeSource and its dependents."""

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
