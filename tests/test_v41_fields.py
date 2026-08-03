"""v4.1 §4.8 ExtractedField 三维质量维度字段测试。

覆盖：
- 枚举完整性：CROSS_VERIFY_STATUSES / SOURCE_QUALITY_TYPES / FIELD_TYPES
- 字段默认值：cross_verify_status='single_source' / value_count=1
- 字段可写：5 个新字段都能正确存储和读取
- cross_verify_status 与 cross_verified 的关系映射
- FieldInput 数据类新字段传递
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.database import Base
from app.models.evidence import (
    CROSS_VERIFY_STATUSES,
    FIELD_TYPES,
    SOURCE_QUALITY_TYPES,
    Evidence,
    ExtractedField,
    FieldEvidenceLink,
)
from app.models.tender import Tender
from app.processors.display_grade import cross_verify_status_to_bool
from app.processors.evidence_repository import (
    FieldInput,
    create_field_with_evidence,
    get_field_with_evidence,
)


@pytest_asyncio.fixture
async def db_session():
    """内存 SQLite 数据库 session。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    AsyncSessionLocal = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    async with AsyncSessionLocal() as session:
        tender = Tender(project_name="v4.1 字段测试")
        session.add(tender)
        await session.commit()
        await session.refresh(tender)
        session._test_tender_id = tender.id  # type: ignore
        yield session
    await engine.dispose()


# ========== 枚举完整性测试 ==========


class TestV41Enums:
    """v4.1 §4.8 新增枚举完整性。"""

    def test_cross_verify_statuses_six_values(self):
        """CROSS_VERIFY_STATUSES 必须有 6 种状态。"""
        assert len(CROSS_VERIFY_STATUSES) == 6
        for key in [
            "independent",
            "consistent_unknown",
            "same_origin",
            "version_difference",
            "conflict",
            "single_source",
        ]:
            assert key in CROSS_VERIFY_STATUSES

    def test_source_quality_types_six_values(self):
        """SOURCE_QUALITY_TYPES 必须有 6 类。"""
        assert len(SOURCE_QUALITY_TYPES) == 6
        for key in [
            "official_original",
            "official_repost",
            "authorized_original",
            "commercial_repost",
            "index_only",
            "unknown",
        ]:
            assert key in SOURCE_QUALITY_TYPES

    def test_field_types_six_values(self):
        """FIELD_TYPES 必须有 6 种。"""
        assert len(FIELD_TYPES) == 6
        for key in ["amount", "date", "organization", "identifier", "fact", "text"]:
            assert key in FIELD_TYPES


# ========== cross_verify_status → bool 映射测试 ==========


class TestCrossVerifyStatusMapping:
    """cross_verify_status 6 态与 cross_verified 布尔的映射关系。"""

    @pytest.mark.parametrize(
        "status,expected",
        [
            ("independent", True),
            ("consistent_unknown", True),
            ("same_origin", False),
            ("version_difference", False),
            ("conflict", False),
            ("single_source", False),
        ],
    )
    def test_status_to_bool_mapping(self, status, expected):
        """6 态 enum 到布尔的映射。"""
        assert cross_verify_status_to_bool(status) is expected

    def test_invalid_status_returns_false(self):
        """非法状态返回 False。"""
        assert cross_verify_status_to_bool("invalid_status") is False


# ========== ExtractedField 字段默认值与存储测试 ==========


class TestExtractedFieldV41Fields:
    """ExtractedField 5 个新字段的默认值和存储。"""

    async def test_default_values(self, db_session):
        """新字段默认值：cross_verify_status='single_source' / value_count=1。"""
        field = ExtractedField(
            tender_id=db_session._test_tender_id,
            field_name="project_identifier",
            field_status="present",
            raw_value="ZFCG-2026-001",
        )
        db_session.add(field)
        await db_session.commit()
        await db_session.refresh(field)

        assert field.cross_verify_status == "single_source"
        assert field.source_quality_snapshot is None
        assert field.field_type is None
        assert field.semantic_role is None
        assert field.value_count == 1
        # 向后兼容：cross_verified 默认 False
        assert field.cross_verified is False

    async def test_explicit_values(self, db_session):
        """显式设置 5 个新字段值。"""
        field = ExtractedField(
            tender_id=db_session._test_tender_id,
            field_name="amount",
            field_status="present",
            raw_value="100万元",
            cross_verify_status="independent",
            source_quality_snapshot="official_original",
            field_type="amount",
            semantic_role="budget_amount",
            value_count=3,
        )
        db_session.add(field)
        await db_session.commit()
        await db_session.refresh(field)

        assert field.cross_verify_status == "independent"
        assert field.source_quality_snapshot == "official_original"
        assert field.field_type == "amount"
        assert field.semantic_role == "budget_amount"
        assert field.value_count == 3

    async def test_all_six_cross_verify_statuses(self, db_session):
        """6 种 cross_verify_status 都能正确存储。"""
        for status in [
            "independent",
            "consistent_unknown",
            "same_origin",
            "version_difference",
            "conflict",
            "single_source",
        ]:
            field = ExtractedField(
                tender_id=db_session._test_tender_id,
                field_name=f"test_{status}",
                field_status="present",
                raw_value=status,
                cross_verify_status=status,
            )
            db_session.add(field)
        await db_session.commit()

        fields = (
            await db_session.execute(
                select(ExtractedField).where(
                    ExtractedField.tender_id == db_session._test_tender_id
                )
            )
        ).scalars().all()
        statuses = {f.cross_verify_status for f in fields if f.field_name.startswith("test_")}
        assert statuses == {
            "independent",
            "consistent_unknown",
            "same_origin",
            "version_difference",
            "conflict",
            "single_source",
        }


# ========== FieldInput 数据类传递测试 ==========


class TestFieldInputV41Fields:
    """FieldInput 数据类新增 5 字段传递到 ExtractedField。"""

    async def test_field_input_defaults(self, db_session):
        """FieldInput 默认值正确传递。"""
        field_input = FieldInput(
            field_name="purchaser_name",
            field_status="present",
            raw_value="某机关单位",
            support_level="unsupported",
        )
        field = await create_field_with_evidence(
            db_session, db_session._test_tender_id, field_input
        )
        await db_session.commit()
        await db_session.refresh(field)

        assert field.cross_verify_status == "single_source"
        assert field.value_count == 1
        assert field.source_quality_snapshot is None

    async def test_field_input_explicit_values(self, db_session):
        """FieldInput 显式值正确传递。"""
        field_input = FieldInput(
            field_name="winner_name",
            field_status="present",
            raw_value="某公司",
            support_level="unsupported",
            cross_verify_status="consistent_unknown",
            source_quality_snapshot="official_repost",
            field_type="organization",
            semantic_role="winner",
            value_count=2,
        )
        field = await create_field_with_evidence(
            db_session, db_session._test_tender_id, field_input
        )
        await db_session.commit()
        await db_session.refresh(field)

        assert field.cross_verify_status == "consistent_unknown"
        assert field.source_quality_snapshot == "official_repost"
        assert field.field_type == "organization"
        assert field.semantic_role == "winner"
        assert field.value_count == 2
