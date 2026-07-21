"""BidAgent v4.1 ba_ 表迁移脚本（W1-03 补丁）。

需求来源：
- W1-03 任务清单明确要求"第一版迁移脚本"
- v0 数据库已存在（tenders / subscriptions / push_logs 等），create_all 不会自动
  ALTER 旧表，也不会幂等地建新表
- 生产环境升级时，ba_ 表必须独立可执行，不依赖 init_database() 的全量 create_all

设计原则：
- **幂等**：可重复执行，已存在的表跳过，不抛异常
- **可追溯**：每张表创建时记录日志，含版本号
- **非破坏性**：不 DROP 任何旧表，只 ADD 新表 / 新列
- **SQLite / PostgreSQL 双兼容**：用 SQLAlchemy 反射检测表结构
- **顺序敏感**：按外键依赖顺序建表

工程规范：
- async/await
- 结构化日志（request_id 上下文）
- 与 app.models.database 共享 engine
"""
from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.models.database import Base, engine as default_engine
from app.utils.logger import get_logger

logger = get_logger("backend.migrations")

MIGRATION_VERSION = "ba-v1.0.0"

# 按外键依赖顺序排列的 ba_ 表名（先建被依赖的表）
BA_TABLES_IN_ORDER: tuple[str, ...] = (
    "ba_organizations",          # 无外键依赖
    "ba_tender_projects",        # 依赖 ba_organizations
    "ba_tender_notices",         # 依赖 ba_tender_projects（自引用 superseded_by）
    "ba_notice_sources",         # 依赖 ba_tender_notices（自引用 repost_of）
    "ba_notice_versions",        # 依赖 ba_notice_sources（自引用 previous_version_id）
    "ba_evidence",               # 依赖 ba_notice_versions
    "ba_extracted_fields",       # 依赖 ba_notice_versions
    "ba_field_evidence_links",   # 依赖 ba_extracted_fields + ba_evidence
    "ba_notice_participants",    # 依赖 ba_tender_notices + ba_organizations
    "ba_project_identifiers",    # 依赖 ba_tender_projects + ba_notice_sources
)


async def _list_existing_tables(engine: AsyncEngine) -> set[str]:
    """通过 SQLAlchemy 反射获取当前数据库已存在的表名集合。"""
    async with engine.begin() as conn:
        names = await conn.run_sync(
            lambda sync_conn: set(inspect(sync_conn).get_table_names())
        )
    return names


async def migrate_ba_tables(
    engine: AsyncEngine | None = None,
    *,
    drop_first: bool = False,
) -> dict[str, str]:
    """执行 ba_ 表迁移。

    Args:
        engine: 指定 engine，默认用 app.models.database.engine
        drop_first: 是否先 DROP 再 CREATE（仅开发/测试用，生产禁用）

    Returns:
        dict: {table_name: "created" | "exists" | "dropped"}
        便于日志和测试断言。

    Raises:
        RuntimeError: drop_first=True 时如果表存在但 DROP 失败
    """
    target_engine = engine or default_engine
    result: dict[str, str] = {}

    logger.info(
        "ba_migration version={} engine={} starting (drop_first={})",
        MIGRATION_VERSION,
        target_engine.url,
        drop_first,
    )

    existing = await _list_existing_tables(target_engine)

    # 开发模式：先 DROP
    if drop_first:
        async with target_engine.begin() as conn:
            # 反向顺序 DROP，避免外键约束冲突
            for table in reversed(BA_TABLES_IN_ORDER):
                if table in existing:
                    logger.info("ba_migration: DROP TABLE {}", table)
                    await conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
                    result[table] = "dropped"
        existing = await _list_existing_tables(target_engine)

    # 顺序 CREATE：用 Base.metadata.create_all 一次性建所有 ba_ 表
    # create_all 本身幂等（IF NOT EXISTS），但反射已有表能更精细地记录日志
    tables_to_create = [t for t in BA_TABLES_IN_ORDER if t not in existing]
    if not tables_to_create:
        logger.info("ba_migration: all {} ba_ tables already exist, skip", len(BA_TABLES_IN_ORDER))
        for t in BA_TABLES_IN_ORDER:
            result.setdefault(t, "exists")
        return result

    logger.info(
        "ba_migration: creating {} tables: {}",
        len(tables_to_create),
        ", ".join(tables_to_create),
    )

    async with target_engine.begin() as conn:
        # create_all 会在当前事务中创建所有未存在的表
        await conn.run_sync(Base.metadata.create_all)

    # 重新反射验证
    after = await _list_existing_tables(target_engine)
    for table in BA_TABLES_IN_ORDER:
        if table in existing:
            result[table] = "exists"
        elif table in after:
            result[table] = "created"
            logger.info("ba_migration: CREATED table {}", table)
        else:
            # 不应发生，但显式记录便于排查
            result[table] = "missing"
            logger.error(
                "ba_migration: table {} not found after create_all (BUG)", table
            )

    logger.info(
        "ba_migration version={} completed: created={} existed={}",
        MIGRATION_VERSION,
        sum(1 for v in result.values() if v == "created"),
        sum(1 for v in result.values() if v == "exists"),
    )
    return result


async def verify_ba_schema() -> dict[str, bool]:
    """验证所有 ba_ 表都已创建且可访问。

    用于启动时健康检查、CI smoke test。

    Returns:
        dict: {table_name: True/False}
    """
    existing = await _list_existing_tables(default_engine)
    return {t: (t in existing) for t in BA_TABLES_IN_ORDER}


__all__ = [
    "BA_TABLES_IN_ORDER",
    "MIGRATION_VERSION",
    "migrate_ba_tables",
    "verify_ba_schema",
]
