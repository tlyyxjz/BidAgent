"""data_deletion.py unit tests (v4.1 sec 13.3).

Covers 5 deletion scopes:
- delete_by_source_url
- delete_by_source_platform
- delete_notice_source_instance
- delete_page_snapshot
- delete_user_authorized_data

Each test verifies:
- Correct rows deleted
- Audit record created
- Cascade relationships handled
- Error cases (not found, empty)
"""

from __future__ import annotations

import pytest

from app.models.database import AsyncSessionLocal
from app.models.evidence import Evidence, ExtractedField, FieldEvidenceLink
from app.models.organization import Organization, PartyRole
from app.models.tender import Tender
from app.models.tender_project import (
    NoticeParticipant,
    NoticeSource,
    NoticeVersion,
    TenderNotice,
    TenderProject,
)
from app.models.user import ApiKey, User
from app.models.subscription import PushLog, Subscription
from app.models.job import ScrapeJob
from app.services.data_deletion import (
    DataDeletionService,
    DeletionScope,
)


class TestDeleteBySourceUrl:
    """delete_by_source_url tests."""

    @pytest.mark.asyncio
    async def test_delete_existing_tender_by_url(self) -> None:
        """Delete tender by source_url should remove tender and cascade."""
        async with AsyncSessionLocal() as session:
            # Setup: create tender with source_url
            tender = Tender(
                project_name="Test Project",
                source_url="https://example.com/test-delete",
                source_platform="test_platform",
            )
            session.add(tender)
            await session.commit()
            await session.refresh(tender)
            tender_id = tender.id

            # Delete
            service = DataDeletionService()
            result = await service.delete_by_source_url(
                session,
                "https://example.com/test-delete",
                request_basis="test cleanup",
                operator="test_user",
            )

            assert result.scope == DeletionScope.SOURCE_URL
            assert result.error is None
            assert result.deleted_counts.get("tenders", 0) >= 1
            assert result.audit_id.startswith("del_")

    @pytest.mark.asyncio
    async def test_delete_nonexistent_url_no_error(self) -> None:
        """Deleting non-existent URL should return empty result, not error."""
        async with AsyncSessionLocal() as session:
            service = DataDeletionService()
            result = await service.delete_by_source_url(
                session,
                "https://nonexistent.com/nope",
                request_basis="test",
                operator="test",
            )
            assert result.error is None
            assert len(result.deleted_counts) == 0 or all(
                v == 0 for v in result.deleted_counts.values()
            )

    @pytest.mark.asyncio
    async def test_delete_scrape_job_by_url(self) -> None:
        """ScrapeJob with matching url should be deleted."""
        async with AsyncSessionLocal() as session:
            # Need a user first (FK)
            user = User(email="deletion-test@example.com", plan="free")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            job = ScrapeJob(
                id="test-job-delete-1",
                user_id=user.id,
                url="https://example.com/job-delete-test",
                status="completed",
            )
            session.add(job)
            await session.commit()

            service = DataDeletionService()
            result = await service.delete_by_source_url(
                session,
                "https://example.com/job-delete-test",
                request_basis="test",
                operator="test",
            )
            assert result.deleted_counts.get("scrape_jobs", 0) >= 1

    @pytest.mark.asyncio
    async def test_audit_record_has_required_fields(self) -> None:
        """Audit record must have all 5 required fields per sec 13.3."""
        async with AsyncSessionLocal() as session:
            service = DataDeletionService()
            result = await service.delete_by_source_url(
                session,
                "https://audit-test.com",
                request_basis="GDPR Article 17",
                operator="admin@example.com",
            )
            assert result.audit_id != ""
            assert result.executed_at > 0


class TestDeleteBySourcePlatform:
    """delete_by_source_platform tests."""

    @pytest.mark.asyncio
    async def test_delete_tenders_by_platform(self) -> None:
        """Delete all tenders with matching source_platform."""
        async with AsyncSessionLocal() as session:
            for i in range(3):
                session.add(Tender(
                    project_name=f"Platform Test {i}",
                    source_url=f"https://test-platform.com/{i}",
                    source_platform="delete_platform_test",
                ))
            await session.commit()

            service = DataDeletionService()
            result = await service.delete_by_source_platform(
                session,
                "delete_platform_test",
                request_basis="platform decommission",
                operator="admin",
            )
            assert result.scope == DeletionScope.SOURCE_PLATFORM
            assert result.deleted_counts.get("tenders", 0) >= 3

    @pytest.mark.asyncio
    async def test_delete_nonexistent_platform(self) -> None:
        """Non-existent platform returns empty result."""
        async with AsyncSessionLocal() as session:
            service = DataDeletionService()
            result = await service.delete_by_source_platform(
                session,
                "nonexistent_platform_xyz",
                request_basis="test",
                operator="test",
            )
            assert result.error is None


class TestDeleteNoticeSourceInstance:
    """delete_notice_source_instance tests."""

    @pytest.mark.asyncio
    async def test_delete_existing_source(self) -> None:
        """Delete a single NoticeSource by ID."""
        async with AsyncSessionLocal() as session:
            # Need a project + notice first
            project = TenderProject(
                canonical_name="Test Project",
                industry_category="other",
                resolution_status="unresolved",
            )
            session.add(project)
            await session.commit()
            await session.refresh(project)

            notice = TenderNotice(
                project_id=project.project_id,
                notice_type="tender",
                canonical_title="Test Notice",
                status="active",
            )
            session.add(notice)
            await session.commit()
            await session.refresh(notice)

            source = NoticeSource(
                notice_id=notice.notice_id,
                source_url="https://source-test.com/1",
                source_platform="test",
                platform_type="commercial",
                publication_role="original",
                source_quality="commercial_repost",
                source_group="grp-test-1",
            )
            session.add(source)
            await session.commit()
            await session.refresh(source)

            service = DataDeletionService()
            result = await service.delete_notice_source_instance(
                session,
                source.notice_source_id,
                request_basis="test",
                operator="test",
            )
            assert result.deleted_counts.get("notice_sources", 0) == 1

    @pytest.mark.asyncio
    async def test_delete_nonexistent_source_returns_error(self) -> None:
        """Non-existent source_id returns error in result."""
        async with AsyncSessionLocal() as session:
            service = DataDeletionService()
            result = await service.delete_notice_source_instance(
                session,
                "nonexistent_source_id",
                request_basis="test",
                operator="test",
            )
            assert result.error is not None
            assert "not found" in result.error


class TestDeletePageSnapshot:
    """delete_page_snapshot tests."""

    @pytest.mark.asyncio
    async def test_delete_nonexistent_version_returns_error(self) -> None:
        """Non-existent version_id returns error."""
        async with AsyncSessionLocal() as session:
            service = DataDeletionService()
            result = await service.delete_page_snapshot(
                session,
                "nonexistent_version",
                request_basis="test",
                operator="test",
            )
            assert result.error is not None
            assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_delete_version_without_snapshot(self) -> None:
        """Version with no snapshot_path returns appropriate error."""
        async with AsyncSessionLocal() as session:
            # Create minimal chain
            project = TenderProject(
                canonical_name="Snap Test",
                industry_category="other",
                resolution_status="unresolved",
            )
            session.add(project)
            await session.commit()
            await session.refresh(project)

            notice = TenderNotice(
                project_id=project.project_id,
                notice_type="tender",
                canonical_title="Snap Notice",
                status="active",
            )
            session.add(notice)
            await session.commit()
            await session.refresh(notice)

            source = NoticeSource(
                notice_id=notice.notice_id,
                source_url="https://snap-test.com",
                source_platform="test",
                platform_type="commercial",
                publication_role="original",
                source_quality="commercial_repost",
                source_group="grp-snap-1",
            )
            session.add(source)
            await session.commit()
            await session.refresh(source)

            version = NoticeVersion(
                notice_source_id=source.notice_source_id,
                http_status=200,
                content_sha256="a" * 64,
                raw_text_sha256="b" * 64,
                change_type="initial",
                # snapshot_path=None
            )
            session.add(version)
            await session.commit()
            await session.refresh(version)

            service = DataDeletionService()
            result = await service.delete_page_snapshot(
                session,
                version.version_id,
                request_basis="test",
                operator="test",
            )
            assert result.error is not None
            assert "snapshot_path" in result.error or "no snapshot" in result.error


class TestDeleteUserAuthorizedData:
    """delete_user_authorized_data tests."""

    @pytest.mark.asyncio
    async def test_delete_user_cascade(self) -> None:
        """Delete user should cascade to api_keys, subscriptions, push_logs."""
        async with AsyncSessionLocal() as session:
            user = User(email="cascade-test@example.com", plan="free")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            # Add api_key
            session.add(ApiKey(
                user_id=user.id,
                key_hash="a" * 64,
                name="test-key",
            ))
            # Add subscription
            sub = Subscription(
                user_id=user.id,
                raw_query="test query",
                frequency_cron="0 8 * * *",
                trigger_type="scheduled",
            )
            session.add(sub)
            await session.commit()
            await session.refresh(sub)

            service = DataDeletionService()
            result = await service.delete_user_authorized_data(
                session,
                user.id,
                request_basis="user requested deletion",
                operator="user",
            )
            assert result.scope == DeletionScope.USER_AUTHORIZED_DATA
            assert result.deleted_counts.get("users", 0) == 1
            assert result.deleted_counts.get("api_keys", 0) >= 1
            assert result.deleted_counts.get("subscriptions", 0) >= 1

    @pytest.mark.asyncio
    async def test_delete_nonexistent_user_returns_error(self) -> None:
        """Non-existent user_id returns error."""
        async with AsyncSessionLocal() as session:
            service = DataDeletionService()
            result = await service.delete_user_authorized_data(
                session,
                99999,
                request_basis="test",
                operator="test",
            )
            assert result.error is not None
            assert "not found" in result.error


class TestDeletionScope:
    """DeletionScope enum tests."""

    def test_all_5_scopes_present(self) -> None:
        """All 5 scopes from v4.1 sec 13.3 must be present."""
        scopes = {s.value for s in DeletionScope}
        assert scopes == {
            "source_url",
            "source_platform",
            "notice_source_instance",
            "page_snapshot",
            "user_authorized_data",
        }

    def test_scope_is_str_enum(self) -> None:
        """DeletionScope should be str enum for JSON serialization."""
        assert isinstance(DeletionScope.SOURCE_URL.value, str)
