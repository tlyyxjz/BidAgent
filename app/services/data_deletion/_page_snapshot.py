"""Deletion by page_snapshot scope (v4.1 sec 13.3)."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tender_project import NoticeVersion
from app.utils.logger import get_logger

from app.services.data_deletion._models import (
    DeletionResult,
    DeletionScope,
)

logger = get_logger("data_deletion")


class _PageSnapshotMixin:
    """Mixin: delete snapshot file for a NoticeVersion."""

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
