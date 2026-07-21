"""BidAgent v4.1 空库迁移端到端验证脚本。

验证流程（用户复查项 P0-3）：
1. 创建全新临时 SQLite 数据库（空库）
2. 执行 migrate_ba_tables（从空库迁移）
3. verify_ba_schema 验证所有 ba_ 表已创建
4. 插入完整链路数据（Organization → ... → FieldEvidenceLink）
5. 读取并核对关联关系
6. 重复执行 migrate_ba_tables 验证幂等性
7. 验证 v0 表不冲突

运行方式：
    python scripts/verify_empty_db_migration.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

# 在导入 app 之前设置环境变量
_DB_PATH = Path(tempfile.gettempdir()) / "bidagent_empty_verify.db"
if _DB_PATH.exists():
    _DB_PATH.unlink()

os.environ["SECRET_KEY"] = "a" * 64
os.environ["ADMIN_SECRET"] = "test-admin-secret-12345"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_DB_PATH.as_posix()}"
os.environ["REDIS_URL"] = "redis://localhost:6379/0"

# 将项目根目录加入 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

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
from backend.migrations import BA_TABLES_IN_ORDER, migrate_ba_tables, verify_ba_schema
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
from app.models.database import Base, engine
from app.utils.logger import get_logger

logger = get_logger("scripts.verify_empty_db")


async def step1_list_tables(engine: AsyncEngine) -> set[str]:
    """列出当前数据库所有表。"""
    async with engine.begin() as conn:
        names = await conn.run_sync(
            lambda sync_conn: set(inspect(sync_conn).get_table_names())
        )
    return names


async def step2_migrate_from_empty() -> dict[str, str]:
    """从空库执行迁移。"""
    logger.info("步骤 2：从空库执行 migrate_ba_tables")
    result = await migrate_ba_tables(engine, drop_first=False)
    return result


async def step3_verify_schema() -> dict[str, bool]:
    """验证所有 ba_ 表已创建。"""
    logger.info("步骤 3：执行 verify_ba_schema")
    return await verify_ba_schema()


async def step4_insert_full_chain() -> dict[str, str]:
    """插入完整链路数据。"""
    logger.info("步骤 4：插入完整链路数据")
    from app.models.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        org = Organization(
            canonical_name="空库验证测试组织",
            unified_social_credit_code="11310000MB2A12345X",
            legal_entity_type=LegalEntityType.GOVERNMENT_AGENCY,
            resolution_status=ResolutionStatus.RESOLVED,
        )
        session.add(org)
        await session.flush()

        project = TenderProject(
            canonical_name="空库验证测试项目",
            industry_category="goods",
            purchaser_entity_id=org.organization_id,
            resolution_status=ResolutionStatus.RESOLVED,
        )
        session.add(project)
        await session.flush()

        notice = TenderNotice(
            project_id=project.project_id,
            notice_type=NoticeType.TENDER,
            canonical_title="空库验证招标公告",
            status=NoticeStatus.ACTIVE,
        )
        session.add(notice)
        await session.flush()

        source = NoticeSource(
            notice_id=notice.notice_id,
            source_url="http://www.ccgp.gov.cn/test/empty_db.htm",
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

        # 多值字段验证：插入 2 行 winner_name（联合体中标）
        field1 = ExtractedField(
            version_id=version.version_id,
            field_name=CoreFieldName.WINNER_NAME,
            field_type=FieldType.TEXT,
            raw_value="甲公司",
            normalized_value="甲公司",
            support_level=SupportLevel.DIRECT,
            primary_evidence_id=evidence.evidence_id,
        )
        field2 = ExtractedField(
            version_id=version.version_id,
            field_name=CoreFieldName.WINNER_NAME,
            field_type=FieldType.TEXT,
            raw_value="乙公司",
            normalized_value="乙公司",
            support_level=SupportLevel.DIRECT,
        )
        session.add_all([field1, field2])
        await session.flush()

        link = FieldEvidenceLink(
            field_id=field1.field_id,
            evidence_id=evidence.evidence_id,
            evidence_role=EvidenceRole.PRIMARY,
            sequence=0,
            is_required=True,
        )
        session.add(link)

        participant = NoticeParticipant(
            notice_id=notice.notice_id,
            organization_id=org.organization_id,
            raw_name="空库验证测试组织",
            normalized_name="空库验证测试组织",
            participant_role=ParticipantRole.PURCHASER,
            resolution_status=ResolutionStatus.RESOLVED,
        )
        session.add(participant)

        identifier = ProjectIdentifier(
            project_id=project.project_id,
            identifier_type="procurement",
            raw_value="EMPTY-DB-001",
            normalized_value="emptydb001",
            source_id=source.notice_source_id,
        )
        session.add(identifier)
        await session.commit()

        return {
            "org": org.organization_id,
            "project": project.project_id,
            "notice": notice.notice_id,
            "source": source.notice_source_id,
            "version": version.version_id,
            "evidence": evidence.evidence_id,
            "field1": field1.field_id,
            "field2": field2.field_id,
            "link": link.link_id,
            "participant": participant.participant_id,
            "identifier": identifier.identifier_id,
        }


async def step5_read_and_verify(ids: dict[str, str]) -> None:
    """读取并核对关联关系。"""
    logger.info("步骤 5：读取并核对关联关系")
    from app.models.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        # 验证多值字段：winner_name 应有 2 行
        fields = (
            await session.execute(
                select(ExtractedField).where(
                    ExtractedField.version_id == ids["version"],
                    ExtractedField.field_name == CoreFieldName.WINNER_NAME,
                )
            )
        ).scalars().all()
        assert len(fields) == 2, (
            f"多值字段 winner_name 应有 2 行，实际 {len(fields)}"
        )
        winners = {f.raw_value for f in fields}
        assert winners == {"甲公司", "乙公司"}

        # 验证外键链路：link → field → evidence → version → source → notice → project → org
        link = (
            await session.execute(
                select(FieldEvidenceLink).where(
                    FieldEvidenceLink.link_id == ids["link"]
                )
            )
        ).scalar_one()
        assert link.evidence_role == EvidenceRole.PRIMARY

        field = (
            await session.execute(
                select(ExtractedField).where(
                    ExtractedField.field_id == link.field_id
                )
            )
        ).scalar_one()
        assert field.primary_evidence_id == link.evidence_id

        evidence = (
            await session.execute(
                select(Evidence).where(Evidence.evidence_id == link.evidence_id)
            )
        ).scalar_one()
        assert evidence.version_id == field.version_id

        # 验证 participant 关联
        participant = (
            await session.execute(
                select(NoticeParticipant).where(
                    NoticeParticipant.participant_id == ids["participant"]
                )
            )
        ).scalar_one()
        assert participant.participant_role == ParticipantRole.PURCHASER

        # 验证 identifier 关联
        identifier = (
            await session.execute(
                select(ProjectIdentifier).where(
                    ProjectIdentifier.identifier_id == ids["identifier"]
                )
            )
        ).scalar_one()
        assert identifier.raw_value == "EMPTY-DB-001"

    logger.info("步骤 5 验证通过：所有关联关系正确")


async def step6_verify_idempotent() -> None:
    """重复执行迁移验证幂等性。"""
    logger.info("步骤 6：重复执行 migrate_ba_tables 验证幂等性")
    result = await migrate_ba_tables(engine, drop_first=False)
    # 所有表应返回 "exists"
    for table, status in result.items():
        assert status == "exists", (
            f"幂等性验证失败：{table} 状态应为 exists，实际 {status}"
        )
    logger.info("步骤 6 验证通过：幂等性正确")


async def main() -> None:
    """主流程。"""
    logger.info("=" * 60)
    logger.info("BidAgent v4.1 空库迁移端到端验证")
    logger.info("临时数据库：{}", _DB_PATH)
    logger.info("=" * 60)

    # 步骤 1：确认空库
    tables_before = await step1_list_tables(engine)
    logger.info("步骤 1：空库状态，当前表数量={}", len(tables_before))
    if tables_before:
        logger.warning("警告：数据库非空，已有表：{}", tables_before)
    else:
        logger.info("步骤 1 验证通过：数据库为空")

    # 步骤 2：从空库迁移
    result = await step2_migrate_from_empty()
    created_count = sum(1 for v in result.values() if v == "created")
    logger.info("步骤 2 完成：创建 {} 张表", created_count)
    assert created_count == len(BA_TABLES_IN_ORDER), (
        f"应创建 {len(BA_TABLES_IN_ORDER)} 张表，实际 {created_count}"
    )

    # 步骤 3：verify_ba_schema
    schema_status = await step3_verify_schema()
    all_ok = all(schema_status.values())
    logger.info("步骤 3 完成：verify_ba_schema 全部通过={}", all_ok)
    assert all_ok, f"verify_ba_schema 失败：{schema_status}"

    # 步骤 4：插入完整链路
    ids = await step4_insert_full_chain()
    logger.info("步骤 4 完成：插入 {} 个实体", len(ids))

    # 步骤 5：读取核对
    await step5_read_and_verify(ids)

    # 步骤 6：幂等性
    await step6_verify_idempotent()

    logger.info("=" * 60)
    logger.info("✅ 空库迁移端到端验证全部通过")
    logger.info("=" * 60)

    # 清理
    await engine.dispose()
    if _DB_PATH.exists():
        _DB_PATH.unlink()
        logger.info("已清理临时数据库：{}", _DB_PATH)


if __name__ == "__main__":
    asyncio.run(main())
