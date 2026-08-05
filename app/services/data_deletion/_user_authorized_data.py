"""Deletion by user_authorized_data scope (v4.1 sec 13.3)."""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import ApiKey, User
from app.models.subscription import PushLog, Subscription
from app.models.job import ScrapeJob
from app.utils.logger import get_logger

from app.services.data_deletion._models import (
    DeletionResult,
    DeletionScope,
)

logger = get_logger("data_deletion")


class _UserAuthorizedDataMixin:
    """Mixin: delete a user and all their authorized data."""

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
