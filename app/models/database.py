"""SQLAlchemy 异步数据库引擎与 session。

MVP 使用 SQLite + aiosqlite，生产可换 PostgreSQL（改 DATABASE_URL 即可）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger("database")


class Base(DeclarativeBase):
    """所有 ORM 模型的 declarative base."""


# SQLite 需要禁用 check_same_thread；其他后端不需要该参数
_connect_args: dict[str, object] = {}
_engine_kwargs: dict[str, object] = {"echo": False}

if settings.DATABASE_URL.startswith("sqlite"):
    # 新-2 修复：timeout=30s 让并发写等待而非立即报 database is locked
    _connect_args = {"check_same_thread": False, "timeout": 30}
else:
    # m-4 修复：非 SQLite 后端显式配置连接池（SQLite 不支持 pool_size）
    # m-2 修复（第四轮）：pool_pre_ping 移到非 SQLite 分支，SQLite 无意义
    _engine_kwargs.update(
        pool_size=10,
        max_overflow=20,
        pool_recycle=3600,  # 1h 回收，避免长连接被 DB 断开
        pool_pre_ping=True,  # MySQL/PG 长连接保活检测
    )

engine = create_async_engine(
    settings.DATABASE_URL,
    connect_args=_connect_args,
    **_engine_kwargs,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


# 新-1 修复：SQLite 旧库新增字段迁移
# 当 ORM 模型新增 nullable 列时，create_all 不会 ALTER TABLE，需要手动检测 + 添加
_SQLITE_MIGRATIONS: list[tuple[str, str, str]] = [
    # (table, column, column_def)
    ("tenders", "source_raw_text", "TEXT"),
    # Sol S-10/S-15：subscription 推送目标字段
    ("subscriptions", "notify_email", "VARCHAR(255)"),
    ("subscriptions", "webhook_url", "VARCHAR(500)"),
    # M-2 修复：PushLog content_hash 幂等去重字段
    ("push_logs", "content_hash", "VARCHAR(64)"),
]


async def _run_sqlite_migrations() -> None:
    """SQLite 轻量迁移：检测缺失列并 ALTER TABLE ADD COLUMN。

    新-1 修复：create_all 不会对已存在的表添加新列，旧库升级后查询会报
    "no such column" 错误。启动时检测并补列。
    """
    if not settings.DATABASE_URL.startswith("sqlite"):
        return

    async with engine.begin() as conn:
        for table, column, col_def in _SQLITE_MIGRATIONS:
            # 检测列是否存在
            result = await conn.execute(text(f"PRAGMA table_info({table})"))
            existing_cols = {row[1] for row in result.fetchall()}
            if column not in existing_cols:
                logger.info(
                    "migration: add column %s.%s (%s)", table, column, col_def
                )
                await conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
                )


async def _set_sqlite_pragmas() -> None:
    """新-2 修复：SQLite WAL 模式 + busy_timeout，缓解并发写锁。"""
    if not settings.DATABASE_URL.startswith("sqlite"):
        return
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.execute(text("PRAGMA busy_timeout=30000"))  # 30s
        await conn.execute(text("PRAGMA synchronous=NORMAL"))


async def init_database() -> None:
    """初始化数据库：设置 PRAGMA → 建表 → 轻量迁移。

    四层实体表（tender_projects / tender_notices / notice_sources /
    notice_versions / notice_participants / project_identifiers）通过
    导入 tender_project 模块注册到 Base.metadata，由 create_all 一并创建。
    现有 Tender 表保留（向后兼容）。
    """
    # 导入四层实体模型 + organization（FK 依赖），确保表注册到 Base.metadata
    from app.models import organization, tender_project  # noqa: F401

    await _set_sqlite_pragmas()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _run_sqlite_migrations()
    logger.info("database initialized (pragmas + create_all + migrations)")


async def get_db() -> AsyncIterator[AsyncSession]:
    """yield 一个异步数据库 session。"""
    async with AsyncSessionLocal() as session:
        yield session
