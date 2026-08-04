"""Extra tests for app/services/data_deletion.py.

Focuses on cascade deletion paths and edge cases not covered by
the existing test_data_deletion.py:

- delete_by_source_url with NoticeVersion + NoticeParticipant cascade
- delete_by_source_platform with TenderNotice cleanup
- delete_page_snapshot with real file on disk
- delete_page_snapshot with missing file
- delete_user_authorized_data with push_logs + scrape_jobs
- _delete_tender_cascade with FieldEvidenceLink + Evidence + PartyRole + PushLog
- _delete_versions_by_source_ids with snapshot files on disk
"""

from __future__ import annotations

import json
from pathlib import Path

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
    DeletionAuditRecord,
    DeletionResult,
    DeletionScope,
)


async def _make_project_notice_source(
    session,
    source_url: str = "https://cascade-test.com/1",
    source_platform: str = "cascade_test",
) -> tuple[str, str]:
    """Create project → notice → source chain, return (notice_id, source_id)."""
    project = TenderProject(
        canonical_name="Cascade Project",
        industry_category="other",
        resolution_status="unresolved",
    )
    session.add(project)
    await session.commit()
    await session.refresh(project)

    notice = TenderNotice(
        project_id=project.project_id,
        notice_type="tender",
        canonical_title="Cascade Notice",
        status="active",
    )
    session.add(notice)
    await session.commit()
    await session.refresh(notice)

    source = NoticeSource(
        notice_id=notice.notice_id,
        source_url=source_url,
        source_platform=source_platform,
        platform_type="commercial",
        publication_role="original",
        source_quality="commercial_repost",
        source_group="grp-cascade",
    )
    session.add(source)
    await session.commit()
    await session.refresh(source)

    return notice.notice_id, source.notice_source_id


async def _make_org(session, name: str = "Test Org") -> Organization:
    org = Organization(
        organization_id="01TESTORG" + name.replace(" ", "")[:14].ljust(14, "0"),
        raw_name=name,
        normalized_name=name + "_norm",
        org_type="enterprise",
    )
    session.add(org)
    await session.commit()
    await session.refresh(org)
    return org


# =====================================================================
# delete_by_source_url — cascade with versions + participants
# =====================================================================


class TestDeleteByUrlCascade:
    """Cover _delete_versions_by_source_ids + NoticeParticipant deletion."""

    async def test_delete_url_with_versions_and_snapshot_file(self, tmp_path: Path) -> None:
        """delete_by_source_url should delete NoticeVersion records + snapshot files."""
        snapshot_file = tmp_path / "snap_v1.html"
        snapshot_file.write_text("<html>snapshot</html>", encoding="utf-8")

        async with AsyncSessionLocal() as session:
            notice_id, source_id = await _make_project_notice_source(session)

            version = NoticeVersion(
                notice_source_id=source_id,
                http_status=200,
                content_sha256="a" * 64,
                raw_text_sha256="b" * 64,
                change_type="initial",
                snapshot_path=str(snapshot_file),
            )
            session.add(version)
            await session.commit()

            service = DataDeletionService()
            result = await service.delete_by_source_url(
                session,
                "https://cascade-test.com/1",
                request_basis="cleanup",
                operator="tester",
            )

            assert result.deleted_counts.get("notice_versions", 0) >= 1
            assert str(snapshot_file) in result.deleted_files
            assert not snapshot_file.exists()

    async def test_delete_url_with_participants(self) -> None:
        """delete_by_source_url should delete NoticeParticipant rows."""
        async with AsyncSessionLocal() as session:
            notice_id, source_id = await _make_project_notice_source(session)

            participant = NoticeParticipant(
                notice_id=notice_id,
                raw_name="Test Bidder",
                participant_role="bidder",
                resolution_status="unresolved",
            )
            session.add(participant)
            await session.commit()

            service = DataDeletionService()
            result = await service.delete_by_source_url(
                session,
                "https://cascade-test.com/1",
                request_basis="cleanup",
                operator="tester",
            )

            assert result.deleted_counts.get("notice_participants", 0) >= 1

    async def test_delete_url_cascades_tender_with_evidence(self) -> None:
        """delete_by_source_url should cascade-delete tender + evidence + fields."""
        async with AsyncSessionLocal() as session:
            tender = Tender(
                project_name="Cascade Tender",
                source_url="https://tender-cascade.com/1",
                source_platform="tender_cascade",
            )
            session.add(tender)
            await session.commit()
            await session.refresh(tender)

            field = ExtractedField(
                tender_id=tender.id,
                field_name="amount",
                field_status="present",
                raw_value="100万元",
            )
            session.add(field)
            await session.commit()
            await session.refresh(field)

            evidence = Evidence(
                tender_id=tender.id,
                evidence_text="100万元",
                raw_start=0,
                raw_end=5,
            )
            session.add(evidence)
            await session.commit()
            await session.refresh(evidence)

            link = FieldEvidenceLink(
                field_id=field.id,
                evidence_id=evidence.id,
                evidence_role="primary",
            )
            session.add(link)
            await session.commit()

            service = DataDeletionService()
            result = await service.delete_by_source_url(
                session,
                "https://tender-cascade.com/1",
                request_basis="cleanup",
                operator="tester",
            )

            counts = result.deleted_counts
            assert counts.get("tenders", 0) >= 1
            assert counts.get("extracted_fields", 0) >= 1
            assert counts.get("field_evidence_links", 0) >= 1
            assert counts.get("evidence", 0) >= 1

    async def test_delete_url_cascades_party_roles_and_push_logs(self) -> None:
        """delete_by_source_url should cascade-delete PartyRole + PushLog via tender."""
        async with AsyncSessionLocal() as session:
            user = User(email="cascade-pr@test.com", plan="free")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            tender = Tender(
                project_name="PR Tender",
                source_url="https://pr-cascade.com/1",
                source_platform="pr_cascade",
            )
            session.add(tender)
            await session.commit()
            await session.refresh(tender)

            org = await _make_org(session, "PR Org")
            party_role = PartyRole(
                organization_id=org.organization_id,
                tender_id=tender.id,
                role="purchaser",
                raw_name_in_notice="PR Org",
            )
            session.add(party_role)

            sub = Subscription(
                user_id=user.id,
                raw_query="test",
                frequency_cron="0 8 * * *",
                trigger_type="scheduled",
            )
            session.add(sub)
            await session.commit()
            await session.refresh(sub)

            push_log = PushLog(
                subscription_id=sub.id,
                tender_id=tender.id,
            )
            session.add(push_log)
            await session.commit()

            service = DataDeletionService()
            result = await service.delete_by_source_url(
                session,
                "https://pr-cascade.com/1",
                request_basis="cleanup",
                operator="tester",
            )

            counts = result.deleted_counts
            assert counts.get("party_roles", 0) >= 1
            assert counts.get("push_logs", 0) >= 1


# =====================================================================
# delete_by_source_platform — TenderNotice cleanup
# =====================================================================


class TestDeleteByPlatformCascade:
    """Cover platform-level deletion with TenderNotice removal."""

    async def test_delete_platform_removes_notices_and_participants(self) -> None:
        """delete_by_source_platform should delete TenderNotice + NoticeParticipant."""
        async with AsyncSessionLocal() as session:
            notice_id, source_id = await _make_project_notice_source(
                session,
                source_url="https://plat-cascade.com/1",
                source_platform="plat_cascade",
            )

            participant = NoticeParticipant(
                notice_id=notice_id,
                raw_name="Plat Bidder",
                participant_role="bidder",
                resolution_status="unresolved",
            )
            session.add(participant)
            await session.commit()

            service = DataDeletionService()
            result = await service.delete_by_source_platform(
                session,
                "plat_cascade",
                request_basis="decommission",
                operator="admin",
            )

            counts = result.deleted_counts
            assert counts.get("notice_sources", 0) >= 1
            assert counts.get("tender_notices", 0) >= 1
            assert counts.get("notice_participants", 0) >= 1

    async def test_delete_platform_with_version_snapshot_file(self, tmp_path: Path) -> None:
        """delete_by_source_platform should delete snapshot files from versions."""
        snapshot_file = tmp_path / "plat_snap.html"
        snapshot_file.write_text("<html>plat</html>", encoding="utf-8")

        async with AsyncSessionLocal() as session:
            notice_id, source_id = await _make_project_notice_source(
                session,
                source_url="https://plat-snap.com/1",
                source_platform="plat_snap",
            )

            version = NoticeVersion(
                notice_source_id=source_id,
                http_status=200,
                content_sha256="c" * 64,
                raw_text_sha256="d" * 64,
                change_type="initial",
                snapshot_path=str(snapshot_file),
            )
            session.add(version)
            await session.commit()

            service = DataDeletionService()
            result = await service.delete_by_source_platform(
                session,
                "plat_snap",
                request_basis="cleanup",
                operator="admin",
            )

            assert result.deleted_counts.get("notice_versions", 0) >= 1
            assert str(snapshot_file) in result.deleted_files
            assert not snapshot_file.exists()


# =====================================================================
# delete_page_snapshot — file deletion paths
# =====================================================================


class TestDeletePageSnapshotFileOps:
    """Cover snapshot file deletion + missing file + OSError."""

    async def test_delete_snapshot_with_existing_file(self, tmp_path: Path) -> None:
        """Snapshot file exists on disk → file is deleted, path cleared."""
        snapshot_file = tmp_path / "existing_snap.html"
        snapshot_file.write_text("<html>exists</html>", encoding="utf-8")

        async with AsyncSessionLocal() as session:
            notice_id, source_id = await _make_project_notice_source(session)

            version = NoticeVersion(
                notice_source_id=source_id,
                http_status=200,
                content_sha256="e" * 64,
                raw_text_sha256="f" * 64,
                change_type="initial",
                snapshot_path=str(snapshot_file),
            )
            session.add(version)
            await session.commit()
            await session.refresh(version)

            service = DataDeletionService()
            result = await service.delete_page_snapshot(
                session,
                version.version_id,
                request_basis="gdpr",
                operator="admin",
            )

            assert result.error is None
            assert str(snapshot_file) in result.deleted_files
            assert not snapshot_file.exists()
            assert result.deleted_counts.get("notice_versions_updated", 0) == 1

    async def test_delete_snapshot_file_not_found(self, tmp_path: Path) -> None:
        """snapshot_path set but file does not exist → error set, path still cleared."""
        missing_file = tmp_path / "missing_snap.html"

        async with AsyncSessionLocal() as session:
            notice_id, source_id = await _make_project_notice_source(session)

            version = NoticeVersion(
                notice_source_id=source_id,
                http_status=200,
                content_sha256="1" * 64,
                raw_text_sha256="2" * 64,
                change_type="initial",
                snapshot_path=str(missing_file),
            )
            session.add(version)
            await session.commit()
            await session.refresh(version)

            service = DataDeletionService()
            result = await service.delete_page_snapshot(
                session,
                version.version_id,
                request_basis="cleanup",
                operator="admin",
            )

            assert result.error is not None
            assert "not found" in result.error
            assert result.deleted_counts.get("notice_versions_updated", 0) == 1


# =====================================================================
# delete_user_authorized_data — push_logs + scrape_jobs counts
# =====================================================================


class TestDeleteUserWithPushLogsAndJobs:
    """Cover push_logs and scrape_jobs deletion in user deletion."""

    async def test_delete_user_with_push_logs_and_scrape_jobs(self) -> None:
        """User with subscription + push_log + scrape_job → all counted."""
        async with AsyncSessionLocal() as session:
            user = User(email="user-pl-job@test.com", plan="free")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            tender = Tender(
                project_name="PL Job Tender",
                source_url="https://pl-job.com/1",
            )
            session.add(tender)
            await session.commit()
            await session.refresh(tender)

            sub = Subscription(
                user_id=user.id,
                raw_query="test",
                frequency_cron="0 8 * * *",
                trigger_type="scheduled",
            )
            session.add(sub)
            await session.commit()
            await session.refresh(sub)

            push_log = PushLog(
                subscription_id=sub.id,
                tender_id=tender.id,
            )
            session.add(push_log)

            job = ScrapeJob(
                id="job-pl-test-001",
                user_id=user.id,
                url="https://pl-job.com/scrape",
                status="completed",
            )
            session.add(job)
            await session.commit()

            service = DataDeletionService()
            result = await service.delete_user_authorized_data(
                session,
                user.id,
                request_basis="user request",
                operator="user",
            )

            counts = result.deleted_counts
            assert counts.get("users", 0) == 1
            assert counts.get("push_logs", 0) >= 1
            assert counts.get("scrape_jobs", 0) >= 1
            assert counts.get("subscriptions", 0) >= 1


# =====================================================================
# _delete_versions_by_source_ids — direct tests
# =====================================================================


class TestDeleteVersionsBySourceIds:
    """Directly test the private cascade method."""

    async def test_empty_source_ids_returns_zero(self) -> None:
        """Empty source_ids list → returns 0 immediately."""
        async with AsyncSessionLocal() as session:
            service = DataDeletionService()
            result = DeletionResult(
                scope=DeletionScope.NOTICE_SOURCE_INSTANCE,
                target="test",
            )
            count = await service._delete_versions_by_source_ids(
                session, [], result,
            )
            assert count == 0

    async def test_delete_versions_with_snapshot_file(self, tmp_path: Path) -> None:
        """Versions with snapshot files → files deleted from disk."""
        snapshot_file = tmp_path / "ver_snap.html"
        snapshot_file.write_text("<html>ver</html>", encoding="utf-8")

        async with AsyncSessionLocal() as session:
            notice_id, source_id = await _make_project_notice_source(session)

            version = NoticeVersion(
                notice_source_id=source_id,
                http_status=200,
                content_sha256="3" * 64,
                raw_text_sha256="4" * 64,
                change_type="initial",
                snapshot_path=str(snapshot_file),
            )
            session.add(version)
            await session.commit()

            service = DataDeletionService()
            result = DeletionResult(
                scope=DeletionScope.SOURCE_URL,
                target="test",
            )
            count = await service._delete_versions_by_source_ids(
                session, [source_id], result,
            )

            assert count >= 1
            assert result.deleted_counts.get("notice_versions", 0) >= 1
            assert str(snapshot_file) in result.deleted_files
            assert not snapshot_file.exists()

    async def test_delete_versions_snapshot_unlink_fails(self, tmp_path: Path) -> None:
        """Snapshot file OSError → warning logged, continues deletion."""
        non_existent = tmp_path / "never_existed.html"

        async with AsyncSessionLocal() as session:
            notice_id, source_id = await _make_project_notice_source(session)

            version = NoticeVersion(
                notice_source_id=source_id,
                http_status=200,
                content_sha256="5" * 64,
                raw_text_sha256="6" * 64,
                change_type="initial",
                snapshot_path=str(non_existent),
            )
            session.add(version)
            await session.commit()

            service = DataDeletionService()
            result = DeletionResult(
                scope=DeletionScope.SOURCE_URL,
                target="test",
            )
            count = await service._delete_versions_by_source_ids(
                session, [source_id], result,
            )

            assert count >= 1
            assert result.deleted_counts.get("notice_versions", 0) >= 1


# =====================================================================
# _delete_tender_cascade — direct tests
# =====================================================================


class TestDeleteTenderCascadeDirect:
    """Directly test _delete_tender_cascade."""

    async def test_empty_tender_ids_noop(self) -> None:
        """Empty tender_ids → no-op."""
        async with AsyncSessionLocal() as session:
            service = DataDeletionService()
            result = DeletionResult(
                scope=DeletionScope.SOURCE_URL,
                target="test",
            )
            await service._delete_tender_cascade(session, [], result)
            assert len(result.deleted_counts) == 0

    async def test_cascade_deletes_all_related(self) -> None:
        """Tender with field + evidence + link + party_role + push_log → all deleted."""
        async with AsyncSessionLocal() as session:
            user = User(email="cascade-direct@test.com", plan="free")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            tender = Tender(
                project_name="Direct Cascade",
                source_url="https://direct-cascade.com/1",
            )
            session.add(tender)
            await session.commit()
            await session.refresh(tender)

            field = ExtractedField(
                tender_id=tender.id,
                field_name="purchaser_name",
                field_status="present",
                raw_value="Test Purchaser",
            )
            session.add(field)
            await session.commit()
            await session.refresh(field)

            evidence = Evidence(
                tender_id=tender.id,
                evidence_text="Test Purchaser",
                raw_start=0,
                raw_end=10,
            )
            session.add(evidence)
            await session.commit()
            await session.refresh(evidence)

            link = FieldEvidenceLink(
                field_id=field.id,
                evidence_id=evidence.id,
                evidence_role="primary",
            )
            session.add(link)

            org = await _make_org(session, "Direct Org")
            party_role = PartyRole(
                organization_id=org.organization_id,
                tender_id=tender.id,
                role="purchaser",
                raw_name_in_notice="Direct Org",
            )
            session.add(party_role)

            sub = Subscription(
                user_id=user.id,
                raw_query="direct",
                trigger_type="immediate",
            )
            session.add(sub)
            await session.commit()
            await session.refresh(sub)

            push_log = PushLog(
                subscription_id=sub.id,
                tender_id=tender.id,
            )
            session.add(push_log)
            await session.commit()

            service = DataDeletionService()
            result = DeletionResult(
                scope=DeletionScope.SOURCE_URL,
                target="test",
            )
            await service._delete_tender_cascade(session, [tender.id], result)

            counts = result.deleted_counts
            assert counts.get("tenders", 0) == 1
            assert counts.get("extracted_fields", 0) >= 1
            assert counts.get("field_evidence_links", 0) >= 1
            assert counts.get("evidence", 0) >= 1
            assert counts.get("party_roles", 0) >= 1
            assert counts.get("push_logs", 0) >= 1


# =====================================================================
# DeletionResult / DeletionAuditRecord dataclass tests
# =====================================================================


class TestDeletionResultDataclass:
    """Test DeletionResult.to_audit_dict and DeletionAuditRecord."""

    def test_to_audit_dict_contains_all_fields(self) -> None:
        """to_audit_dict should contain all required audit fields."""
        result = DeletionResult(
            scope=DeletionScope.SOURCE_URL,
            target="https://test.com",
            deleted_counts={"tenders": 1},
            deleted_files=["/tmp/snap.html"],
            audit_id="del_123",
        )
        d = result.to_audit_dict()
        assert d["scope"] == "source_url"
        assert d["target"] == "https://test.com"
        assert d["deleted_counts"] == {"tenders": 1}
        assert d["deleted_files"] == ["/tmp/snap.html"]
        assert d["audit_id"] == "del_123"
        assert d["error"] is None

    def test_deletion_audit_record_generates_id(self) -> None:
        """DeletionAuditRecord should auto-generate audit_id if not provided."""
        audit = DeletionAuditRecord(
            request_basis="test",
            operator="tester",
            deletion_scope="source_url",
            target="https://test.com",
        )
        assert audit.audit_id.startswith("del_")
        assert audit.executed_at > 0

    def test_deletion_audit_record_preserves_custom_id(self) -> None:
        """DeletionAuditRecord should preserve a custom audit_id."""
        audit = DeletionAuditRecord(
            request_basis="test",
            operator="tester",
            deletion_scope="source_url",
            target="https://test.com",
            audit_id="custom_id_123",
        )
        assert audit.audit_id == "custom_id_123"
