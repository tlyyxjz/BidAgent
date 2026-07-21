"""BidAgent v4.1 四层数据模型测试（W1-03）。

覆盖目标：
- 10 个 ba_ 表可通过 Base.metadata.create_all 创建
- 外键链：Organization → TenderProject → TenderNotice → NoticeSource
        → NoticeVersion → Evidence → ExtractedField → FieldEvidenceLink
- ULID 主键自动生成（26 字符）
- 自引用外键：TenderNotice.superseded_by / NoticeSource.repost_of
  / NoticeVersion.previous_version_id
- 唯一索引 ix_ba_links_field_seq 防止同字段同序号重复
- 辅助实体：NoticeParticipant、ProjectIdentifier

工程规范：
- 复用 conftest.py 的 _reset_db_and_rate_limit fixture（自动建表）
- 异步测试用 pytest.mark.asyncio
- 不依赖外部服务
"""
from __future__ import annotations

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from app.models.database import AsyncSessionLocal, Base
from backend.enums import (
    ChangeType,
    CoreFieldName,
    EvidenceRole,
    FieldType,
    LegalEntityType,
    NoticeStatus,
    NoticeType,
    ParticipantRole,
    PlatformType,
    PublicationRole,
    ResolutionStatus,
    SourceQuality,
    SupportLevel,
)
from backend.models import (
    Evidence,
    ExtractedField,
    FieldEvidenceLink,
    NoticeParticipant,
    NoticeSource,
    NoticeVersion,
    Organization,
    ProjectIdentifier,
    TenderNotice,
    TenderProject,
)


# ============================================================
# 工具：完整链路构建（10 个实体一次性插入）
# ============================================================


async def _build_full_chain() -> dict[str, str]:
    """构建一条完整四层链路，返回各实体 ID。

    顺序遵循外键依赖：
    Organization → TenderProject → TenderNotice → NoticeSource
    → NoticeVersion → Evidence → ExtractedField → FieldEvidenceLink
    """
    async with AsyncSessionLocal() as session:
        org = Organization(
            canonical_name="上海市某机关事务管理局",
            unified_social_credit_code="11310000MB2A12345X",
            legal_entity_type=LegalEntityType.GOVERNMENT_AGENCY,
            resolution_status=ResolutionStatus.RESOLVED,
        )
        session.add(org)
        await session.flush()

        project = TenderProject(
            canonical_name="2026 年办公设备采购项目",
            industry_category="goods",
            purchaser_entity_id=org.organization_id,
            resolution_status=ResolutionStatus.RESOLVED,
        )
        session.add(project)
        await session.flush()

        notice = TenderNotice(
            project_id=project.project_id,
            notice_type=NoticeType.TENDER,
            canonical_title="2026 年办公设备采购项目招标公告",
            status=NoticeStatus.ACTIVE,
        )
        session.add(notice)
        await session.flush()

        source = NoticeSource(
            notice_id=notice.notice_id,
            source_url="http://www.ccgp.gov.cn/cggg/zygg/gkzb/202607/t20260720_001.htm",
            source_platform="ccgp",
            platform_type=PlatformType.GOVERNMENT,
            publication_role=PublicationRole.ORIGINAL,
            source_quality=SourceQuality.OFFICIAL_ORIGINAL,
        )
        session.add(source)
        await session.flush()

        version = NoticeVersion(
            notice_source_id=source.notice_source_id,
            http_status=200,
            content_sha256="a" * 64,
            raw_text_sha256="b" * 64,
            change_type=ChangeType.INITIAL,
        )
        session.add(version)
        await session.flush()

        evidence = Evidence(
            version_id=version.version_id,
            evidence_text="项目预算金额：人民币壹佰贰拾万元整",
            raw_start=0,
            raw_end=18,
            verified=True,
        )
        session.add(evidence)
        await session.flush()

        field = ExtractedField(
            version_id=version.version_id,
            field_name=CoreFieldName.AMOUNT,
            field_type=FieldType.AMOUNT,
            raw_value="1200000.00",
            normalized_value="1200000.00",
            support_level=SupportLevel.DIRECT,
            primary_evidence_id=evidence.evidence_id,
        )
        session.add(field)
        await session.flush()

        link = FieldEvidenceLink(
            field_id=field.field_id,
            evidence_id=evidence.evidence_id,
            evidence_role=EvidenceRole.PRIMARY,
            sequence=0,
            is_required=True,
        )
        session.add(link)
        await session.commit()

        return {
            "org": org.organization_id,
            "project": project.project_id,
            "notice": notice.notice_id,
            "source": source.notice_source_id,
            "version": version.version_id,
            "evidence": evidence.evidence_id,
            "field": field.field_id,
            "link": link.link_id,
        }


# ============================================================
# 测试套件 1：表结构创建（10 个 ba_ 表必须存在）
# ============================================================


@pytest.mark.asyncio
async def test_all_ba_tables_created():
    """W1-03 验收点 1：10 个 ba_ 表全部通过 create_all 创建。"""
    expected_tables = {
        "ba_organizations",
        "ba_tender_projects",
        "ba_tender_notices",
        "ba_notice_sources",
        "ba_notice_versions",
        "ba_evidence",
        "ba_extracted_fields",
        "ba_field_evidence_links",
        "ba_notice_participants",
        "ba_project_identifiers",
    }

    def _get_tables(sync_session):
        return set(inspect(sync_session.bind).get_table_names())

    async with AsyncSessionLocal() as session:
        actual = await session.run_sync(_get_tables)
    missing = expected_tables - actual
    assert not missing, f"缺失表：{missing}"


@pytest.mark.asyncio
async def test_ba_tables_not_collide_with_v0():
    """W1-03 验收点 2：ba_ 前缀避免与 v0 表（tenders 等）冲突。"""
    def _get_tables(sync_session):
        return set(inspect(sync_session.bind).get_table_names())

    async with AsyncSessionLocal() as session:
        actual = await session.run_sync(_get_tables)
    # v0 表仍存在
    assert "tenders" in actual
    # ba_ 表也存在
    assert "ba_tender_projects" in actual


# ============================================================
# 测试套件 2：ULID 主键自动生成（26 字符）
# ============================================================


@pytest.mark.asyncio
async def test_ulid_primary_keys_auto_generated():
    """W1-03 验收点 3：所有核心实体的 ULID 主键在 flush 后自动生成。"""
    async with AsyncSessionLocal() as session:
        org = Organization(canonical_name="测试组织")
        project = TenderProject(canonical_name="测试项目")
        notice = TenderNotice(project_id="", canonical_title="测试公告", notice_type="tender")
        session.add_all([org, project, notice])
        # 不能 commit，因为 notice.project_id 为空，先只验证 org/project
        session.add(Organization(canonical_name="测试组织2"))
        await session.rollback()

    # 重新验证 ULID 生成
    async with AsyncSessionLocal() as session:
        org = Organization(canonical_name="ULID 测试组织")
        session.add(org)
        await session.flush()
        assert len(org.organization_id) == 26, (
            f"ULID 应为 26 字符，实际 {len(org.organization_id)}"
        )
        assert org.organization_id.isascii(), "ULID 必须为 ASCII"
        await session.rollback()


@pytest.mark.asyncio
async def test_ulid_time_sortable():
    """W1-03 验收点 4：ULID 时间排序特性（先生成的字典序靠前）。"""
    async with AsyncSessionLocal() as session:
        org1 = Organization(canonical_name="A 组织")
        session.add(org1)
        await session.flush()
        id1 = org1.organization_id

        # 强制等待下一毫秒（ULID 精度为毫秒）
        import time
        time.sleep(0.002)

        org2 = Organization(canonical_name="B 组织")
        session.add(org2)
        await session.flush()
        id2 = org2.organization_id

        assert id1 < id2, f"ULID 应时间排序，id1={id1} 应小于 id2={id2}"
        await session.rollback()


# ============================================================
# 测试套件 3：完整外键链路
# ============================================================


@pytest.mark.asyncio
async def test_full_chain_insert_and_query():
    """W1-03 验收点 5：Organization → ... → FieldEvidenceLink 完整链路可写入可查询。"""
    ids = await _build_full_chain()

    async with AsyncSessionLocal() as session:
        # 通过 FieldEvidenceLink 反查到 Organization（join 链路）
        stmt = (
            select(FieldEvidenceLink)
            .where(FieldEvidenceLink.link_id == ids["link"])
        )
        result = await session.execute(stmt)
        link = result.scalar_one()

        assert link.evidence_role == EvidenceRole.PRIMARY
        assert link.is_required is True
        assert link.sequence == 0

        # 验证字段引用的证据
        stmt_field = (
            select(ExtractedField)
            .where(ExtractedField.field_id == link.field_id)
        )
        field = (await session.execute(stmt_field)).scalar_one()
        assert field.field_name == CoreFieldName.AMOUNT
        assert field.support_level == SupportLevel.DIRECT
        assert field.primary_evidence_id == link.evidence_id


@pytest.mark.asyncio
async def test_cascade_relationships_loadable():
    """W1-03 验收点 6：从顶层 Organization 可以逐层向下查询到所有子实体。"""
    ids = await _build_full_chain()

    async with AsyncSessionLocal() as session:
        # 查 Organization 下所有项目
        projects = (
            await session.execute(
                select(TenderProject).where(
                    TenderProject.purchaser_entity_id == ids["org"]
                )
            )
        ).scalars().all()
        assert len(projects) == 1
        assert projects[0].project_id == ids["project"]

        # 查项目下所有公告
        notices = (
            await session.execute(
                select(TenderNotice).where(
                    TenderNotice.project_id == ids["project"]
                )
            )
        ).scalars().all()
        assert len(notices) == 1
        assert notices[0].notice_id == ids["notice"]

        # 查公告下所有来源
        sources = (
            await session.execute(
                select(NoticeSource).where(
                    NoticeSource.notice_id == ids["notice"]
                )
            )
        ).scalars().all()
        assert len(sources) == 1
        assert sources[0].notice_source_id == ids["source"]


# ============================================================
# 测试套件 4：自引用外键
# ============================================================


@pytest.mark.asyncio
async def test_self_reference_superseded_by():
    """W1-03 验收点 7：TenderNotice.superseded_by 自引用外键。"""
    async with AsyncSessionLocal() as session:
        project = TenderProject(canonical_name="更正测试项目")
        session.add(project)
        await session.flush()

        notice1 = TenderNotice(
            project_id=project.project_id,
            notice_type=NoticeType.TENDER,
            canonical_title="原招标公告",
            status=NoticeStatus.ACTIVE,
        )
        session.add(notice1)
        await session.flush()

        notice2 = TenderNotice(
            project_id=project.project_id,
            notice_type=NoticeType.CORRECTION,
            canonical_title="更正公告",
            status=NoticeStatus.ACTIVE,
            superseded_by=notice1.notice_id,  # 自引用
        )
        session.add(notice2)
        await session.commit()

        # 查询验证
        loaded = (
            await session.execute(
                select(TenderNotice).where(
                    TenderNotice.notice_id == notice2.notice_id
                )
            )
        ).scalar_one()
        assert loaded.superseded_by == notice1.notice_id


@pytest.mark.asyncio
async def test_self_reference_repost_of():
    """W1-03 验收点 8：NoticeSource.repost_of 自引用外键（同源转载识别）。"""
    async with AsyncSessionLocal() as session:
        project = TenderProject(canonical_name="转载测试项目")
        session.add(project)
        await session.flush()
        notice = TenderNotice(
            project_id=project.project_id,
            notice_type=NoticeType.AWARD,
            canonical_title="中标公告",
        )
        session.add(notice)
        await session.flush()

        original = NoticeSource(
            notice_id=notice.notice_id,
            source_url="http://www.ccgp.gov.cn/award/001.htm",
            source_platform="ccgp",
            publication_role=PublicationRole.ORIGINAL,
            source_quality=SourceQuality.OFFICIAL_ORIGINAL,
        )
        session.add(original)
        await session.flush()

        repost = NoticeSource(
            notice_id=notice.notice_id,
            source_url="http://example.com/repost/001.htm",
            source_platform="commercial_platform",
            publication_role=PublicationRole.COMMERCIAL_REPOST,
            source_quality=SourceQuality.COMMERCIAL_REPOST,
            repost_of=original.notice_source_id,  # 自引用
            source_group="group_001",
        )
        session.add(repost)
        await session.commit()

        loaded = (
            await session.execute(
                select(NoticeSource).where(
                    NoticeSource.notice_source_id == repost.notice_source_id
                )
            )
        ).scalar_one()
        assert loaded.repost_of == original.notice_source_id


@pytest.mark.asyncio
async def test_self_reference_previous_version():
    """W1-03 验收点 9：NoticeVersion.previous_version_id 自引用外键。"""
    async with AsyncSessionLocal() as session:
        project = TenderProject(canonical_name="版本测试项目")
        session.add(project)
        await session.flush()
        notice = TenderNotice(
            project_id=project.project_id,
            notice_type=NoticeType.TENDER,
            canonical_title="版本测试公告",
        )
        session.add(notice)
        await session.flush()
        source = NoticeSource(
            notice_id=notice.notice_id,
            source_url="http://example.com/v1.htm",
            source_platform="ccgp",
        )
        session.add(source)
        await session.flush()

        v1 = NoticeVersion(
            notice_source_id=source.notice_source_id,
            http_status=200,
            content_sha256="1" * 64,
            change_type=ChangeType.INITIAL,
        )
        session.add(v1)
        await session.flush()

        v2 = NoticeVersion(
            notice_source_id=source.notice_source_id,
            http_status=200,
            content_sha256="2" * 64,
            change_type=ChangeType.MATERIAL,
            previous_version_id=v1.version_id,  # 自引用
        )
        session.add(v2)
        await session.commit()

        loaded = (
            await session.execute(
                select(NoticeVersion).where(
                    NoticeVersion.version_id == v2.version_id
                )
            )
        ).scalar_one()
        assert loaded.previous_version_id == v1.version_id
        assert loaded.change_type == ChangeType.MATERIAL


# ============================================================
# 测试套件 5：唯一索引 ix_ba_links_field_seq
# ============================================================


@pytest.mark.asyncio
async def test_unique_index_field_sequence():
    """W1-03 验收点 10：ix_ba_links_field_seq 唯一索引防同字段同序号重复。"""
    ids = await _build_full_chain()

    # 试图插入同 field_id + sequence=0 的重复 link
    async with AsyncSessionLocal() as session:
        dup_link = FieldEvidenceLink(
            field_id=ids["field"],
            evidence_id=ids["evidence"],
            evidence_role=EvidenceRole.CONTEXT,
            sequence=0,  # 重复 sequence
        )
        session.add(dup_link)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_unique_index_allows_different_sequence():
    """W1-03 验收点 11：同字段不同 sequence 的多条 link 允许共存。"""
    ids = await _build_full_chain()

    async with AsyncSessionLocal() as session:
        link1 = FieldEvidenceLink(
            field_id=ids["field"],
            evidence_id=ids["evidence"],
            evidence_role=EvidenceRole.PRIMARY,
            sequence=1,  # 不同于已存在的 0
        )
        link2 = FieldEvidenceLink(
            field_id=ids["field"],
            evidence_id=ids["evidence"],
            evidence_role=EvidenceRole.CONTEXT,
            sequence=2,
        )
        session.add_all([link1, link2])
        await session.commit()

        links = (
            await session.execute(
                select(FieldEvidenceLink).where(
                    FieldEvidenceLink.field_id == ids["field"]
                )
            )
        ).scalars().all()
        assert len(links) == 3  # 原始 1 + 新增 2


# ============================================================
# 测试套件 6：辅助实体（NoticeParticipant / ProjectIdentifier）
# ============================================================


@pytest.mark.asyncio
async def test_notice_participant_with_role():
    """W1-03 验收点 12：NoticeParticipant 记录组织在公告中的业务角色。"""
    ids = await _build_full_chain()

    async with AsyncSessionLocal() as session:
        participant = NoticeParticipant(
            notice_id=ids["notice"],
            organization_id=ids["org"],
            raw_name="上海市某机关事务管理局",
            normalized_name="上海市某机关事务管理局",
            participant_role=ParticipantRole.PURCHASER,
            resolution_status=ResolutionStatus.RESOLVED,
        )
        session.add(participant)
        await session.commit()

        loaded = (
            await session.execute(
                select(NoticeParticipant).where(
                    NoticeParticipant.participant_id == participant.participant_id
                )
            )
        ).scalar_one()
        assert loaded.participant_role == ParticipantRole.PURCHASER
        assert loaded.organization_id == ids["org"]


@pytest.mark.asyncio
async def test_project_identifier_multiple_codes():
    """W1-03 验收点 13：ProjectIdentifier 支持同一项目多个编号。"""
    ids = await _build_full_chain()

    async with AsyncSessionLocal() as session:
        id1 = ProjectIdentifier(
            project_id=ids["project"],
            identifier_type="procurement",
            raw_value="SH-2026-001",
            normalized_value="sh2026001",
            source_id=ids["source"],
        )
        id2 = ProjectIdentifier(
            project_id=ids["project"],
            identifier_type="agency",
            raw_value="AG-2026-888",
            normalized_value="ag2026888",
        )
        session.add_all([id1, id2])
        await session.commit()

        identifiers = (
            await session.execute(
                select(ProjectIdentifier).where(
                    ProjectIdentifier.project_id == ids["project"]
                )
            )
        ).scalars().all()
        assert len(identifiers) == 2
        types = {i.identifier_type for i in identifiers}
        assert types == {"procurement", "agency"}


# ============================================================
# 测试套件 7：默认值与约束
# ============================================================


@pytest.mark.asyncio
async def test_default_values_on_insert():
    """W1-03 验收点 14：所有带默认值的字段在插入后为期望值。"""
    async with AsyncSessionLocal() as session:
        org = Organization(canonical_name="默认值测试组织")
        session.add(org)
        await session.flush()

        assert org.legal_entity_type == LegalEntityType.UNKNOWN
        assert org.resolution_status == ResolutionStatus.UNRESOLVED
        assert org.created_at is not None

        project = TenderProject(canonical_name="默认值测试项目")
        session.add(project)
        await session.flush()
        assert project.industry_category == "other"
        assert project.resolution_status == ResolutionStatus.UNRESOLVED

        await session.rollback()


@pytest.mark.asyncio
async def test_not_null_constraints_enforced():
    """W1-03 验收点 15：NOT NULL 字段为空时 IntegrityError。"""
    async with AsyncSessionLocal() as session:
        # canonical_name 不可为空
        bad_org = Organization(canonical_name=None)  # type: ignore[arg-type]
        session.add(bad_org)
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


# ============================================================
# 测试套件 8：Base 共享与 v0 兼容
# ============================================================


@pytest.mark.asyncio
async def test_shared_base_with_v0_models():
    """W1-03 验收点 16：backend models 与 v0 共享同一 Base，可同时存在。"""
    from app.models.tender import Tender  # v0 表

    async with AsyncSessionLocal() as session:
        # v0 表写入
        v0 = Tender(project_name="v0 测试招标")
        session.add(v0)
        await session.flush()

        # ba_ 表写入
        ba_org = Organization(canonical_name="v4 测试组织")
        session.add(ba_org)
        await session.flush()

        assert v0.id is not None
        assert len(ba_org.organization_id) == 26

        await session.rollback()


def test_models_module_exports():
    """W1-03 验收点 17：__all__ 导出全部 10 个实体类。"""
    from backend import models

    expected = {
        "Evidence",
        "ExtractedField",
        "FieldEvidenceLink",
        "NoticeParticipant",
        "NoticeSource",
        "NoticeVersion",
        "Organization",
        "ProjectIdentifier",
        "TenderNotice",
        "TenderProject",
    }
    actual = set(models.__all__)
    assert expected.issubset(actual), f"缺失导出：{expected - actual}"


def test_all_models_registered_in_metadata():
    """W1-03 验收点 18：所有 ba_ 表已在 Base.metadata 注册。"""
    registered = set(Base.metadata.tables.keys())
    expected = {
        "ba_organizations",
        "ba_tender_projects",
        "ba_tender_notices",
        "ba_notice_sources",
        "ba_notice_versions",
        "ba_evidence",
        "ba_extracted_fields",
        "ba_field_evidence_links",
        "ba_notice_participants",
        "ba_project_identifiers",
    }
    assert expected.issubset(registered), f"未注册的表：{expected - registered}"


# ============================================================
# 测试套件 9：多值字段写入（修复唯一约束后）
# ============================================================


@pytest.mark.asyncio
async def test_multi_value_field_multiple_rows_allowed():
    """W1-03 修复验证：同一版本同一字段名允许多行（多值字段）。

    场景：联合体中标，winner_name 字段有两个值。
    修复前：(version_id, field_name) 唯一约束会阻塞写入。
    修复后：去掉 unique=True，允许多行。
    """
    ids = await _build_full_chain()

    async with AsyncSessionLocal() as session:
        # 同一版本同一字段名插入第二行（不同 raw_value）
        field2 = ExtractedField(
            version_id=ids["version"],
            field_name=CoreFieldName.WINNER_NAME,
            field_type=FieldType.TEXT,
            raw_value="乙公司",
            normalized_value="乙公司",
            support_level=SupportLevel.DIRECT,
        )
        field3 = ExtractedField(
            version_id=ids["version"],
            field_name=CoreFieldName.WINNER_NAME,
            field_type=FieldType.TEXT,
            raw_value="丙公司",
            normalized_value="丙公司",
            support_level=SupportLevel.DIRECT,
        )
        session.add_all([field2, field3])
        await session.commit()

        # 查询验证：同 version_id 同 field_name 应有 2 行
        rows = (
            await session.execute(
                select(ExtractedField).where(
                    ExtractedField.version_id == ids["version"],
                    ExtractedField.field_name == CoreFieldName.WINNER_NAME,
                )
            )
        ).scalars().all()
        assert len(rows) == 2, f"多值字段应允许 2 行，实际 {len(rows)}"
        winners = {r.raw_value for r in rows}
        assert winners == {"乙公司", "丙公司"}


@pytest.mark.asyncio
async def test_multi_lot_amount_multiple_rows_allowed():
    """W1-03 修复验证：多分包金额允许多行（按 lot_id 区分）。

    场景：招标项目分 2 个包，每包有独立预算金额。
    """
    ids = await _build_full_chain()

    async with AsyncSessionLocal() as session:
        lot1_amount = ExtractedField(
            version_id=ids["version"],
            field_name=CoreFieldName.AMOUNT,
            field_type=FieldType.AMOUNT,
            raw_value="500000.00",
            normalized_value="500000.00",
            amount_type="budget",
            lot_id="包1",
            support_level=SupportLevel.DIRECT,
        )
        lot2_amount = ExtractedField(
            version_id=ids["version"],
            field_name=CoreFieldName.AMOUNT,
            field_type=FieldType.AMOUNT,
            raw_value="800000.00",
            normalized_value="800000.00",
            amount_type="budget",
            lot_id="包2",
            support_level=SupportLevel.DIRECT,
        )
        session.add_all([lot1_amount, lot2_amount])
        await session.commit()

        rows = (
            await session.execute(
                select(ExtractedField).where(
                    ExtractedField.version_id == ids["version"],
                    ExtractedField.field_name == CoreFieldName.AMOUNT,
                ).order_by(ExtractedField.lot_id)
            )
        ).scalars().all()
        # 原始 _build_full_chain 中已有 1 行 amount，新增 2 行，共 3 行
        assert len(rows) == 3, f"多分包金额应允许 3 行，实际 {len(rows)}"
        lot_ids = {r.lot_id for r in rows}
        assert "包1" in lot_ids and "包2" in lot_ids


@pytest.mark.asyncio
async def test_ulid_format_validation():
    """W1-03 修复验证：ULID 必须是合法的 26 字符 Crockford Base32。

    修复前：fallback 用 uuid4.hex[:26] 截断，不是合法 ULID。
    修复后：直接 import ulid，import 失败就报错。
    """
    import re

    # Crockford Base32 字符集：0-9 A-Z（不含 I L O U）
    crockford_pattern = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")

    async with AsyncSessionLocal() as session:
        org = Organization(canonical_name="ULID 格式验证组织")
        session.add(org)
        await session.flush()
        uid = org.organization_id
        await session.rollback()

    assert len(uid) == 26, f"ULID 长度应为 26，实际 {len(uid)}"
    assert crockford_pattern.match(uid), (
        f"ULID 必须符合 Crockford Base32 格式，实际 {uid!r}"
    )
    # 不应包含 I L O U（Crockford 排除字符）
    forbidden = set("ILOU")
    assert not (set(uid) & forbidden), (
        f"ULID 不应包含 I/L/O/U，实际 {uid!r}"
    )
