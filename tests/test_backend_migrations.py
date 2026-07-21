"""BidAgent v4.1 ba_ 表迁移脚本测试（W1-03 补丁）。

覆盖：
- migrate_ba_tables：首次创建 / 幂等 / drop_first
- verify_ba_schema：健康检查
- BA_TABLES_IN_ORDER 顺序正确
- 与 v0 表共存
"""
from __future__ import annotations

import pytest
from sqlalchemy import inspect

from app.models.database import AsyncSessionLocal, Base, engine
from backend.migrations import (
    BA_TABLES_IN_ORDER,
    MIGRATION_VERSION,
    migrate_ba_tables,
    verify_ba_schema,
)


# ============================================================
# 测试套件 1：基本常量
# ============================================================


class TestConstants:
    def test_migration_version_format(self):
        assert MIGRATION_VERSION.startswith("ba-v")

    def test_ba_tables_in_order_count(self):
        assert len(BA_TABLES_IN_ORDER) == 10

    def test_ba_tables_all_prefixed(self):
        for t in BA_TABLES_IN_ORDER:
            assert t.startswith("ba_"), f"表名 {t} 缺少 ba_ 前缀"

    def test_ba_tables_dependency_order(self):
        """按外键依赖顺序排列，被依赖的表在前。"""
        # ba_organizations 必须最早
        assert BA_TABLES_IN_ORDER[0] == "ba_organizations"
        # ba_field_evidence_links 必须在 ba_extracted_fields 和 ba_evidence 之后
        links_idx = BA_TABLES_IN_ORDER.index("ba_field_evidence_links")
        fields_idx = BA_TABLES_IN_ORDER.index("ba_extracted_fields")
        evidence_idx = BA_TABLES_IN_ORDER.index("ba_evidence")
        assert links_idx > fields_idx
        assert links_idx > evidence_idx


# ============================================================
# 测试套件 2：migrate_ba_tables 首次创建
# ============================================================


@pytest.mark.asyncio
async def test_migrate_creates_all_ba_tables():
    """首次调用 migrate_ba_tables 应创建所有 10 张 ba_ 表。"""
    # 先 drop 所有表（测试 fixture 已建表，需清掉）
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    result = await migrate_ba_tables()
    assert len(result) == 10
    for table in BA_TABLES_IN_ORDER:
        assert result[table] == "created", f"{table} 应为 created，实际 {result[table]}"

    # 反射验证
    async with engine.begin() as conn:
        names = await conn.run_sync(
            lambda sync_conn: set(inspect(sync_conn).get_table_names())
        )
    for table in BA_TABLES_IN_ORDER:
        assert table in names


@pytest.mark.asyncio
async def test_migrate_idempotent():
    """已存在的表第二次调用应返回 exists，不抛异常。"""
    # 第一次创建
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await migrate_ba_tables()

    # 第二次调用应幂等
    result = await migrate_ba_tables()
    for table in BA_TABLES_IN_ORDER:
        assert result[table] == "exists", f"{table} 应为 exists"


@pytest.mark.asyncio
async def test_migrate_drop_first_recreates():
    """drop_first=True 应先 DROP 再 CREATE，最终表全部存在。"""
    # 先建表
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await migrate_ba_tables()

    # 记录 drop_first 之前的反射结果
    existing_before = await _list_existing_tables_safe()
    for table in BA_TABLES_IN_ORDER:
        assert table in existing_before

    # 再 drop_first=True 重建
    result = await migrate_ba_tables(drop_first=True)
    # drop_first 后 create_all 重建，最终所有表都存在
    created = [t for t, v in result.items() if v == "created"]
    # 由于 drop 后重新 create_all，所有表都应是 created 状态
    assert len(created) == 10, f"期望 10 张表 created，实际 {created}"
    # 验证表仍存在
    async with engine.begin() as conn:
        names = await conn.run_sync(
            lambda sync_conn: set(inspect(sync_conn).get_table_names())
        )
    for table in BA_TABLES_IN_ORDER:
        assert table in names


async def _list_existing_tables_safe() -> set[str]:
    """反射当前表名集合。"""
    async with engine.begin() as conn:
        return await conn.run_sync(
            lambda sync_conn: set(inspect(sync_conn).get_table_names())
        )


# ============================================================
# 测试套件 3：verify_ba_schema 健康检查
# ============================================================


@pytest.mark.asyncio
async def test_verify_ba_schema_returns_true_for_existing():
    """所有 ba_ 表存在时 verify_ba_schema 全部返回 True。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await migrate_ba_tables()

    result = await verify_ba_schema()
    assert len(result) == 10
    for table, exists in result.items():
        assert exists is True, f"{table} 应存在"


@pytest.mark.asyncio
async def test_verify_ba_schema_returns_false_for_missing():
    """drop_all 后 verify_ba_schema 应返回 False。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    result = await verify_ba_schema()
    for table, exists in result.items():
        assert exists is False, f"{table} 应不存在"


# ============================================================
# 测试套件 4：与 v0 表共存
# ============================================================


@pytest.mark.asyncio
async def test_migrate_preserves_v0_tables():
    """迁移不应影响 v0 表（tenders 等）。"""
    from app.models.tender import Tender
    from sqlalchemy import select

    # 先建 v0 + ba_ 表
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await migrate_ba_tables()

    # 写入 v0 数据
    async with AsyncSessionLocal() as session:
        t = Tender(project_name="v0 测试")
        session.add(t)
        await session.commit()
        v0_id = t.id

    # 再次迁移（幂等），v0 数据应保留
    await migrate_ba_tables()
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Tender).where(Tender.id == v0_id))
        loaded = result.scalar_one_or_none()
        assert loaded is not None
        assert loaded.project_name == "v0 测试"
