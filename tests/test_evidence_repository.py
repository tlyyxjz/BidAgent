"""W2-05 证据入库单元测试。

覆盖：
- 数据模型：ExtractedField / Evidence / FieldEvidenceLink 表结构
- 入库接口：create_evidence / create_field_with_evidence / link_field_evidence / batch_insert_evidence
- 查询接口：get_field_with_evidence / get_tender_fields
- 约束校验：无证据不得高可信 / 历史版本不覆盖 / 多值字段不压平
- 哈希计算：snapshot_sha256 / raw_text_sha256
"""
from __future__ import annotations

import asyncio
import hashlib
from typing import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.database import Base
from app.models.evidence import (
    EVIDENCE_ROLES,
    MATCH_METHODS,
    SUPPORT_LEVELS,
    Evidence,
    ExtractedField,
    FieldEvidenceLink,
)
from app.models.tender import Tender
from app.processors.evidence_repository import (
    EvidenceInput,
    FieldInput,
    batch_insert_evidence,
    compute_raw_text_sha256,
    compute_snapshot_sha256,
    create_evidence,
    create_field_with_evidence,
    get_field_with_evidence,
    get_tender_fields,
    link_field_evidence,
)


# ========== 测试数据库 fixture ==========


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """内存 SQLite 数据库 session。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    AsyncSessionLocal = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )

    async with AsyncSessionLocal() as session:
        # 创建测试 Tender
        tender = Tender(project_name="测试项目")
        session.add(tender)
        await session.commit()
        await session.refresh(tender)
        # 把 tender_id 存到 session 上供测试用
        session._test_tender_id = tender.id  # type: ignore
        yield session

    await engine.dispose()


# ========== 数据模型测试 ==========


class TestEnums:
    """枚举完整性测试。"""

    def test_support_levels(self):
        assert "direct" in SUPPORT_LEVELS
        assert "equivalent" in SUPPORT_LEVELS
        assert "inferred" in SUPPORT_LEVELS
        assert "unsupported" in SUPPORT_LEVELS
        assert "contradicted" in SUPPORT_LEVELS
        assert len(SUPPORT_LEVELS) == 5

    def test_evidence_roles(self):
        assert "primary" in EVIDENCE_ROLES
        assert "context" in EVIDENCE_ROLES
        assert "qualifier" in EVIDENCE_ROLES
        assert "derivation_input" in EVIDENCE_ROLES
        assert "contradiction" in EVIDENCE_ROLES
        assert len(EVIDENCE_ROLES) == 5

    def test_match_methods(self):
        assert "exact" in MATCH_METHODS
        assert "stripped" in MATCH_METHODS
        assert "no_punct" in MATCH_METHODS
        assert "substring" in MATCH_METHODS
        assert "not_found" in MATCH_METHODS
        assert len(MATCH_METHODS) == 5


# ========== 哈希计算测试 ==========


class TestHashCompute:
    def test_snapshot_sha256(self):
        text = "测试快照文本"
        sha = compute_snapshot_sha256(text)
        assert sha == hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert len(sha) == 64

    def test_raw_text_sha256(self):
        text = "测试原文"
        sha = compute_raw_text_sha256(text)
        assert sha == hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert len(sha) == 64

    def test_different_text_different_hash(self):
        assert compute_snapshot_sha256("文本1") != compute_snapshot_sha256("文本2")


# ========== create_evidence 测试 ==========


class TestCreateEvidence:
    @pytest.mark.asyncio
    async def test_create_single_evidence(self, db_session: AsyncSession):
        tender_id = db_session._test_tender_id  # type: ignore
        ev_input = EvidenceInput(
            evidence_text="项目编号：ZFCG-2026-001",
            raw_start=0,
            raw_end=20,
            match_method="exact",
            confidence=1.0,
            verified=True,
        )
        ev = await create_evidence(db_session, tender_id, ev_input)
        await db_session.commit()

        assert ev.id is not None
        assert ev.tender_id == tender_id
        assert ev.evidence_text == "项目编号：ZFCG-2026-001"
        assert ev.raw_start == 0
        assert ev.raw_end == 20
        assert ev.match_method == "exact"
        assert ev.confidence == 100  # 0-100 存储
        assert ev.verified is True

    @pytest.mark.asyncio
    async def test_create_evidence_with_snapshot(self, db_session: AsyncSession):
        tender_id = db_session._test_tender_id  # type: ignore
        snapshot = "快照文本"
        raw = "原文文本"
        ev_input = EvidenceInput(evidence_text="测试", raw_start=0, raw_end=2)
        ev = await create_evidence(
            db_session, tender_id, ev_input, snapshot_text=snapshot, raw_text=raw
        )
        await db_session.commit()

        assert ev.snapshot_sha256 == compute_snapshot_sha256(snapshot)
        assert ev.raw_text_sha256 == compute_raw_text_sha256(raw)

    @pytest.mark.asyncio
    async def test_create_evidence_default_values(self, db_session: AsyncSession):
        """默认值测试。"""
        tender_id = db_session._test_tender_id  # type: ignore
        ev_input = EvidenceInput(evidence_text="测试", raw_start=0, raw_end=2)
        ev = await create_evidence(db_session, tender_id, ev_input)
        await db_session.commit()

        assert ev.match_method == "not_found"
        assert ev.confidence == 0
        assert ev.verified is False
        assert ev.normalized_start == -1
        assert ev.normalized_end == -1


# ========== create_field_with_evidence 测试 ==========


class TestCreateFieldWithEvidence:
    @pytest.mark.asyncio
    async def test_create_field_with_primary_evidence(self, db_session: AsyncSession):
        tender_id = db_session._test_tender_id  # type: ignore
        ev_input = EvidenceInput(
            evidence_text="项目编号：ZFCG-2026-001",
            raw_start=0,
            raw_end=20,
            match_method="exact",
            confidence=1.0,
        )
        field_input = FieldInput(
            field_name="project_identifier",
            raw_value="ZFCG-2026-001",
            support_level="direct",
            evidences=[(ev_input, "primary")],
        )
        field_obj = await create_field_with_evidence(
            db_session, tender_id, field_input
        )
        await db_session.commit()

        assert field_obj.id is not None
        assert field_obj.field_name == "project_identifier"
        assert field_obj.raw_value == "ZFCG-2026-001"
        assert field_obj.support_level == "direct"
        assert field_obj.primary_evidence_id is not None
        assert field_obj.is_current is True

    @pytest.mark.asyncio
    async def test_create_field_multiple_evidences(self, db_session: AsyncSession):
        """一个字段关联多个证据（primary + context）。"""
        tender_id = db_session._test_tender_id  # type: ignore
        ev1 = EvidenceInput(evidence_text="主证据", raw_start=0, raw_end=3)
        ev2 = EvidenceInput(evidence_text="上下文", raw_start=10, raw_end=13)
        field_input = FieldInput(
            field_name="amount",
            raw_value="100万元",
            support_level="direct",
            evidences=[(ev1, "primary"), (ev2, "context")],
        )
        field_obj = await create_field_with_evidence(
            db_session, tender_id, field_input
        )
        await db_session.commit()

        # 查询关联证据
        _, evidence_links = await get_field_with_evidence(db_session, field_obj.id)
        assert len(evidence_links) == 2
        assert evidence_links[0][1].evidence_role == "primary"
        assert evidence_links[0][1].sequence == 0
        assert evidence_links[1][1].evidence_role == "context"
        assert evidence_links[1][1].sequence == 1

    @pytest.mark.asyncio
    async def test_no_evidence_high_support_rejected(self, db_session: AsyncSession):
        """Sol 要求：无证据字段不得进入高可信。"""
        tender_id = db_session._test_tender_id  # type: ignore
        field_input = FieldInput(
            field_name="project_identifier",
            raw_value="ZFCG-2026-001",
            support_level="direct",  # 高可信
            evidences=[],  # 无证据
        )
        with pytest.raises(ValueError, match="无证据字段不得进入高可信"):
            await create_field_with_evidence(db_session, tender_id, field_input)

    @pytest.mark.asyncio
    async def test_no_evidence_unsupported_ok(self, db_session: AsyncSession):
        """无证据 + unsupported 允许。"""
        tender_id = db_session._test_tender_id  # type: ignore
        field_input = FieldInput(
            field_name="winner_name",
            field_status="absent",
            support_level="unsupported",
            evidences=[],
        )
        field_obj = await create_field_with_evidence(
            db_session, tender_id, field_input
        )
        await db_session.commit()
        assert field_obj.support_level == "unsupported"

    @pytest.mark.asyncio
    async def test_invalid_support_level_rejected(self, db_session: AsyncSession):
        """非法支持度被拒。"""
        tender_id = db_session._test_tender_id  # type: ignore
        field_input = FieldInput(
            field_name="project_identifier",
            support_level="invalid_level",
        )
        with pytest.raises(ValueError, match="非法 support_level"):
            await create_field_with_evidence(db_session, tender_id, field_input)

    @pytest.mark.asyncio
    async def test_invalid_evidence_role_rejected(self, db_session: AsyncSession):
        """非法证据角色被拒。"""
        tender_id = db_session._test_tender_id  # type: ignore
        ev = EvidenceInput(evidence_text="测试", raw_start=0, raw_end=2)
        field_input = FieldInput(
            field_name="project_identifier",
            support_level="direct",
            evidences=[(ev, "invalid_role")],
        )
        with pytest.raises(ValueError, match="非法 evidence_role"):
            await create_field_with_evidence(db_session, tender_id, field_input)

    @pytest.mark.asyncio
    async def test_history_version_not_overwritten(self, db_session: AsyncSession):
        """Sol 要求：历史版本不得被新版本覆盖。"""
        tender_id = db_session._test_tender_id  # type: ignore
        ev = EvidenceInput(evidence_text="证据", raw_start=0, raw_end=2)

        # 第一次创建
        field_input1 = FieldInput(
            field_name="project_identifier",
            raw_value="ZFCG-2026-001",
            support_level="direct",
            evidences=[(ev, "primary")],
        )
        field1 = await create_field_with_evidence(db_session, tender_id, field_input1)
        await db_session.commit()
        field1_id = field1.id

        # 第二次创建（同字段）
        field_input2 = FieldInput(
            field_name="project_identifier",
            raw_value="ZFCG-2026-002",
            support_level="direct",
            evidences=[(ev, "primary")],
        )
        field2 = await create_field_with_evidence(db_session, tender_id, field_input2)
        await db_session.commit()

        # 旧版本 is_current=False
        await db_session.refresh(field1)
        assert field1.is_current is False
        assert field2.is_current is True

    @pytest.mark.asyncio
    async def test_multi_value_not_flattened(self, db_session: AsyncSession):
        """Sol 要求：多值字段不得强行压平。"""
        tender_id = db_session._test_tender_id  # type: ignore
        ev = EvidenceInput(evidence_text="证据", raw_start=0, raw_end=2)

        # 创建两个分包金额
        for lot_id in ["lot1", "lot2"]:
            field_input = FieldInput(
                field_name="amount",
                field_status="multi_value",
                raw_value="100万元",
                amount_type="award",
                lot_id=lot_id,
                support_level="direct",
                evidences=[(ev, "primary")],
            )
            await create_field_with_evidence(db_session, tender_id, field_input)
        await db_session.commit()

        # 查询应返回两条
        fields = await get_tender_fields(db_session, tender_id)
        amount_fields = [f for f in fields if f.field_name == "amount"]
        assert len(amount_fields) == 2
        assert {f.lot_id for f in amount_fields} == {"lot1", "lot2"}


# ========== link_field_evidence 测试 ==========


class TestLinkFieldEvidence:
    @pytest.mark.asyncio
    async def test_link_existing(self, db_session: AsyncSession):
        tender_id = db_session._test_tender_id  # type: ignore
        # 先创建字段（无证据）
        field_input = FieldInput(
            field_name="project_identifier",
            support_level="unsupported",
        )
        field_obj = await create_field_with_evidence(db_session, tender_id, field_input)
        await db_session.commit()

        # 再创建证据
        ev_input = EvidenceInput(evidence_text="证据", raw_start=0, raw_end=2)
        ev = await create_evidence(db_session, tender_id, ev_input)
        await db_session.commit()

        # 关联
        link = await link_field_evidence(
            db_session, field_obj.id, ev.id, "primary"
        )
        await db_session.commit()

        assert link.field_id == field_obj.id
        assert link.evidence_id == ev.id
        assert link.evidence_role == "primary"
        assert link.is_required is True  # primary 默认 is_required=True

    @pytest.mark.asyncio
    async def test_link_auto_sequence(self, db_session: AsyncSession):
        """自动计算 sequence。"""
        tender_id = db_session._test_tender_id  # type: ignore
        ev1 = EvidenceInput(evidence_text="证据1", raw_start=0, raw_end=3)
        ev2 = EvidenceInput(evidence_text="证据2", raw_start=5, raw_end=8)

        evidence1 = await create_evidence(db_session, tender_id, ev1)
        evidence2 = await create_evidence(db_session, tender_id, ev2)

        field_input = FieldInput(
            field_name="project_identifier",
            support_level="unsupported",
        )
        field_obj = await create_field_with_evidence(db_session, tender_id, field_input)
        await db_session.commit()

        # 第一个链接 sequence=0
        link1 = await link_field_evidence(
            db_session, field_obj.id, evidence1.id, "primary"
        )
        assert link1.sequence == 0

        # 第二个链接 sequence=1（自动）
        link2 = await link_field_evidence(
            db_session, field_obj.id, evidence2.id, "context"
        )
        assert link2.sequence == 1
        assert link2.is_required is False  # context 默认 is_required=False


# ========== batch_insert_evidence 测试 ==========


class TestBatchInsertEvidence:
    @pytest.mark.asyncio
    async def test_batch_insert(self, db_session: AsyncSession):
        """project_memory 要求：批量入库用 add_all。"""
        tender_id = db_session._test_tender_id  # type: ignore
        ev_inputs = [
            EvidenceInput(evidence_text=f"证据{i}", raw_start=i * 10, raw_end=i * 10 + 3)
            for i in range(5)
        ]
        evidences = await batch_insert_evidence(db_session, tender_id, ev_inputs)
        await db_session.commit()

        assert len(evidences) == 5
        for i, ev in enumerate(evidences):
            assert ev.id is not None
            assert ev.evidence_text == f"证据{i}"

    @pytest.mark.asyncio
    async def test_batch_insert_empty(self, db_session: AsyncSession):
        tender_id = db_session._test_tender_id  # type: ignore
        evidences = await batch_insert_evidence(db_session, tender_id, [])
        assert len(evidences) == 0

    @pytest.mark.asyncio
    async def test_batch_insert_with_snapshot(self, db_session: AsyncSession):
        tender_id = db_session._test_tender_id  # type: ignore
        snapshot = "快照"
        raw = "原文"
        ev_inputs = [EvidenceInput(evidence_text="测试", raw_start=0, raw_end=2)]
        evidences = await batch_insert_evidence(
            db_session, tender_id, ev_inputs, snapshot_text=snapshot, raw_text=raw
        )
        await db_session.commit()

        assert evidences[0].snapshot_sha256 == compute_snapshot_sha256(snapshot)
        assert evidences[0].raw_text_sha256 == compute_raw_text_sha256(raw)


# ========== 查询接口测试 ==========


class TestQueryInterfaces:
    @pytest.mark.asyncio
    async def test_get_field_with_evidence(self, db_session: AsyncSession):
        tender_id = db_session._test_tender_id  # type: ignore
        ev1 = EvidenceInput(evidence_text="主证据", raw_start=0, raw_end=3)
        ev2 = EvidenceInput(evidence_text="上下文", raw_start=10, raw_end=13)
        field_input = FieldInput(
            field_name="amount",
            raw_value="100万元",
            support_level="direct",
            evidences=[(ev1, "primary"), (ev2, "context")],
        )
        field_obj = await create_field_with_evidence(
            db_session, tender_id, field_input
        )
        await db_session.commit()

        field_result, evidence_links = await get_field_with_evidence(
            db_session, field_obj.id
        )
        assert field_result.id == field_obj.id
        assert len(evidence_links) == 2
        # 按 sequence 排序
        assert evidence_links[0][1].sequence == 0
        assert evidence_links[1][1].sequence == 1

    @pytest.mark.asyncio
    async def test_get_field_not_found(self, db_session: AsyncSession):
        with pytest.raises(ValueError, match="not found"):
            await get_field_with_evidence(db_session, 99999)

    @pytest.mark.asyncio
    async def test_get_tender_fields_only_current(self, db_session: AsyncSession):
        """只返回当前版本。"""
        tender_id = db_session._test_tender_id  # type: ignore
        ev = EvidenceInput(evidence_text="证据", raw_start=0, raw_end=2)

        # 创建两个版本
        for value in ["v1", "v2"]:
            field_input = FieldInput(
                field_name="project_identifier",
                raw_value=value,
                support_level="direct",
                evidences=[(ev, "primary")],
            )
            await create_field_with_evidence(db_session, tender_id, field_input)
        await db_session.commit()

        # 只返回当前版本（1 条）
        fields = await get_tender_fields(db_session, tender_id, only_current=True)
        current_fields = [
            f for f in fields if f.field_name == "project_identifier"
        ]
        assert len(current_fields) == 1
        assert current_fields[0].raw_value == "v2"

    @pytest.mark.asyncio
    async def test_get_tender_fields_all_versions(self, db_session: AsyncSession):
        """返回所有版本。"""
        tender_id = db_session._test_tender_id  # type: ignore
        ev = EvidenceInput(evidence_text="证据", raw_start=0, raw_end=2)

        for value in ["v1", "v2"]:
            field_input = FieldInput(
                field_name="project_identifier",
                raw_value=value,
                support_level="direct",
                evidences=[(ev, "primary")],
            )
            await create_field_with_evidence(db_session, tender_id, field_input)
        await db_session.commit()

        fields = await get_tender_fields(db_session, tender_id, only_current=False)
        all_fields = [
            f for f in fields if f.field_name == "project_identifier"
        ]
        assert len(all_fields) == 2

# ========== 多值字段 3 中标人测试 (#52 修复) ==========


class TestMultiValueThreeBidders:
    """3 个中标人独立存储测试 (#52 修复)。

    参考 test_multi_value_not_flattened 的入库路径：field_status=multi_value
    时 create_field_with_evidence 跳过 _deprecate_old_versions，多条记录均
    保持 is_current=True，可独立查询。
    """

    @pytest.mark.asyncio
    async def test_three_winners_stored_independently(self, db_session: AsyncSession):
        """3 个 winner_name 独立存储（不被压平为单值）。"""
        tender_id = db_session._test_tender_id  # type: ignore
        ev = EvidenceInput(evidence_text="证据", raw_start=0, raw_end=2)
        winners = ["甲公司", "乙公司", "丙公司"]
        for winner in winners:
            field_input = FieldInput(
                field_name="winner_name",
                field_status="multi_value",
                raw_value=winner,
                support_level="direct",
                evidences=[(ev, "primary")],
            )
            await create_field_with_evidence(db_session, tender_id, field_input)
        await db_session.commit()

        fields = await get_tender_fields(db_session, tender_id)
        winner_fields = [f for f in fields if f.field_name == "winner_name"]
        assert len(winner_fields) == 3
        assert {f.raw_value for f in winner_fields} == set(winners)

    @pytest.mark.asyncio
    async def test_three_winners_queryable_separately(self, db_session: AsyncSession):
        """可独立查询每个 winner（get_field_with_evidence）。"""
        tender_id = db_session._test_tender_id  # type: ignore
        ev = EvidenceInput(evidence_text="证据", raw_start=0, raw_end=2)
        winners = ["甲公司", "乙公司", "丙公司"]
        for winner in winners:
            field_input = FieldInput(
                field_name="winner_name",
                field_status="multi_value",
                raw_value=winner,
                support_level="direct",
                evidences=[(ev, "primary")],
            )
            await create_field_with_evidence(db_session, tender_id, field_input)
        await db_session.commit()

        fields = await get_tender_fields(db_session, tender_id)
        winner_fields = [f for f in fields if f.field_name == "winner_name"]
        assert len(winner_fields) == 3
        for wf in winner_fields:
            field_result, evidence_links = await get_field_with_evidence(
                db_session, wf.id
            )
            assert field_result.id == wf.id
            assert field_result.raw_value in winners
            assert len(evidence_links) == 1
            assert evidence_links[0][1].evidence_role == "primary"

    @pytest.mark.asyncio
    async def test_three_winners_field_status_multi_value(
        self, db_session: AsyncSession
    ):
        """每个 winner 字段的 field_status=multi_value。"""
        tender_id = db_session._test_tender_id  # type: ignore
        ev = EvidenceInput(evidence_text="证据", raw_start=0, raw_end=2)
        for winner in ["甲公司", "乙公司", "丙公司"]:
            field_input = FieldInput(
                field_name="winner_name",
                field_status="multi_value",
                raw_value=winner,
                support_level="direct",
                evidences=[(ev, "primary")],
            )
            await create_field_with_evidence(db_session, tender_id, field_input)
        await db_session.commit()

        fields = await get_tender_fields(db_session, tender_id)
        winner_fields = [f for f in fields if f.field_name == "winner_name"]
        assert len(winner_fields) == 3
        for wf in winner_fields:
            assert wf.field_status == "multi_value"
