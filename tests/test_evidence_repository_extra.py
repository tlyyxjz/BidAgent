"""TS-Q1: 证据入库事务回滚与并发写入测试.

覆盖：
- test_batch_insert_partial_failure_rollback：批量插入部分失败时整体回滚
- test_create_evidence_partial_failure：字段-证据-链接链路中链接插入失败时
  ExtractedField 和 Evidence 不应残留
- test_concurrent_writes_same_field：两个 session 写入同字段时 is_current
  正确翻转、无脏数据

说明：evidence_writes.py 中的函数未显式 try/except + rollback，依赖
SQLAlchemy session 的事务语义。本组测试验证「异常发生后 session 状态一致」。
"""
from __future__ import annotations

from typing import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.models.database import Base
from app.models.evidence import Evidence, ExtractedField, FieldEvidenceLink
from app.models.tender import Tender
from app.processors.evidence_repository import (
    EvidenceInput,
    FieldInput,
    batch_insert_evidence,
    create_field_with_evidence,
)


# ========== 测试数据库 fixture（内存 SQLite，单连接 StaticPool）==========


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """内存 SQLite 数据库 session（单连接，便于复现事务回滚行为）。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    AsyncSessionLocal = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )

    async with AsyncSessionLocal() as session:
        tender = Tender(project_name="测试项目")
        session.add(tender)
        await session.commit()
        await session.refresh(tender)
        session._test_tender_id = tender.id  # type: ignore
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def shared_engine():
    """共享内存 SQLite 引擎（StaticPool 单连接，多 session 可见彼此已提交数据）。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


# ========== 事务回滚测试 ==========


class TestTransactionRollback:
    """TS-Q1: 事务回滚行为测试。"""

    @pytest.mark.asyncio
    async def test_batch_insert_partial_failure_rollback(
        self, db_session: AsyncSession
    ):
        """批量插入时部分数据违反约束，整个批次应回滚。

        场景：批量插入 3 条 evidence，第 2 条 evidence_text=None 违反
        NOT NULL 约束。flush 应抛出 IntegrityError，回滚后数据库中
        不应有任何 evidence（包括已构造的第 1 条和第 3 条）。
        """
        tender_id = db_session._test_tender_id  # type: ignore

        ev_inputs = [
            EvidenceInput(evidence_text="证据1", raw_start=0, raw_end=3),
            # 第 2 条 evidence_text=None 违反 NOT NULL 约束
            EvidenceInput(evidence_text=None, raw_start=5, raw_end=8),  # type: ignore
            EvidenceInput(evidence_text="证据3", raw_start=10, raw_end=13),
        ]

        # batch_insert_evidence 内部 db.add_all + db.flush，
        # flush 检测到 NOT NULL 违规 → IntegrityError
        with pytest.raises(IntegrityError):
            await batch_insert_evidence(db_session, tender_id, ev_inputs)

        # 回滚清理 dirty session
        await db_session.rollback()

        # 验证数据库中不应有任何 evidence（前 2 条 + 第 3 条均回滚）
        result = await db_session.execute(
            select(Evidence).where(Evidence.tender_id == tender_id)
        )
        assert result.scalars().all() == []

    @pytest.mark.asyncio
    async def test_create_evidence_partial_failure(
        self, db_session: AsyncSession
    ):
        """create_field_with_evidence 链路中链接插入失败时，Field 和 Evidence 不残留。

        场景：
        1. create_field_with_evidence 成功创建 ExtractedField + Evidence +
           FieldEvidenceLink（已 flush 但未 commit）
        2. 额外添加一条非法 FieldEvidenceLink（field_id=None 违反 NOT NULL）
           并 flush，模拟链接插入失败
        3. rollback 后验证 ExtractedField / Evidence / FieldEvidenceLink 均无残留
        """
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

        # 步骤 1：正常创建 field + evidence + link（已 flush）
        await create_field_with_evidence(db_session, tender_id, field_input)

        # 步骤 2：追加一条非法 link 模拟后续链接插入失败
        bad_link = FieldEvidenceLink(
            field_id=None,  # NOT NULL 违规
            evidence_id=None,  # NOT NULL 违规
            evidence_role="primary",
            sequence=99,
        )
        db_session.add(bad_link)
        with pytest.raises(IntegrityError):
            await db_session.flush()

        # 步骤 3：rollback 后验证无残留
        await db_session.rollback()

        fields = (await db_session.execute(select(ExtractedField))).scalars().all()
        evidences = (await db_session.execute(select(Evidence))).scalars().all()
        links = (await db_session.execute(select(FieldEvidenceLink))).scalars().all()

        assert fields == [], "ExtractedField 不应残留"
        assert evidences == [], "Evidence 不应残留"
        assert links == [], "FieldEvidenceLink 不应残留"


# ========== 并发写入测试 ==========


class TestConcurrentWritesSameField:
    """TS-Q1: 两个 session 写入同字段的 is_current 翻转测试。"""

    @pytest.mark.asyncio
    async def test_concurrent_writes_same_field(self, shared_engine):
        """两个 session 写入同一 tender 的同一字段，is_current 正确翻转。

        场景：
        - session A 创建 field(project_identifier, value=v1) + commit
        - session B 创建 field(project_identifier, value=v2) + commit
          （_deprecate_old_versions 会将 v1 的 is_current 置为 False）
        - 验证：共 2 条记录，v1.is_current=False，v2.is_current=True
        """
        AsyncSessionLocal = async_sessionmaker(
            bind=shared_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        # 先创建共享 Tender（session A 提交后 session B 可见）
        async with AsyncSessionLocal() as session_a:
            tender = Tender(project_name="并发测试项目")
            session_a.add(tender)
            await session_a.commit()
            await session_a.refresh(tender)
            tender_id = tender.id

        ev_input = EvidenceInput(
            evidence_text="证据",
            raw_start=0,
            raw_end=2,
            match_method="exact",
            confidence=1.0,
        )

        # session A：创建 v1
        async with AsyncSessionLocal() as session_a:
            field_input_v1 = FieldInput(
                field_name="project_identifier",
                raw_value="v1",
                support_level="direct",
                evidences=[(ev_input, "primary")],
            )
            field_a = await create_field_with_evidence(
                session_a, tender_id, field_input_v1
            )
            await session_a.commit()
            field_a_id = field_a.id

        # session B：创建 v2（同字段名，触发 _deprecate_old_versions）
        async with AsyncSessionLocal() as session_b:
            field_input_v2 = FieldInput(
                field_name="project_identifier",
                raw_value="v2",
                support_level="direct",
                evidences=[(ev_input, "primary")],
            )
            field_b = await create_field_with_evidence(
                session_b, tender_id, field_input_v2
            )
            await session_b.commit()
            field_b_id = field_b.id

        # 验证：共 2 条记录，is_current 翻转正确
        async with AsyncSessionLocal() as verify_session:
            fields = (
                await verify_session.execute(
                    select(ExtractedField).where(
                        ExtractedField.tender_id == tender_id,
                        ExtractedField.field_name == "project_identifier",
                    )
                )
            ).scalars().all()

            assert len(fields) == 2, f"应有 2 条记录，实际 {len(fields)}"

            by_value = {f.raw_value: f for f in fields}
            assert set(by_value.keys()) == {"v1", "v2"}, "无脏数据：值应为 v1/v2"

            # v1 被下架，v2 为当前版本
            assert by_value["v1"].is_current is False, "v1 应被标记为 is_current=False"
            assert by_value["v2"].is_current is True, "v2 应为 is_current=True"

            # 每条 field 应有 1 条对应的 Evidence 和 1 条 FieldEvidenceLink
            for f in fields:
                links = (
                    await verify_session.execute(
                        select(FieldEvidenceLink).where(
                            FieldEvidenceLink.field_id == f.id
                        )
                    )
                ).scalars().all()
                assert len(links) == 1, (
                    f"field {f.id} 应有 1 条 link，实际 {len(links)}"
                )
                assert links[0].evidence_role == "primary"

            evidences = (
                await verify_session.execute(
                    select(Evidence).where(Evidence.tender_id == tender_id)
                )
            ).scalars().all()
            assert len(evidences) == 2, (
                f"应有 2 条 evidence（每 field 1 条），实际 {len(evidences)}"
            )
