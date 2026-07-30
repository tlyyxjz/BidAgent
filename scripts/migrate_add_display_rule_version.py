"""W3-05 迁移：extracted_fields 表新增 display_rule_version 字段。

工程约束：
- 迁移脚本必须幂等（多次运行不报错）
- 新增字段必须有默认值（便于 ALTER TABLE 填充存量数据）
- 支持 SQLite（MVP）与 PostgreSQL（生产）
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

# 让脚本能从仓库根目录运行
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import settings
from app.models.database import Base
from app.models.evidence import ExtractedField  # noqa: F401 （确保 ORM 已加载）


NEW_COLUMNS = [
    {
        "name": "display_rule_version",
        "sqlite_ddl": "VARCHAR(32) NOT NULL DEFAULT 'v0.1-calib'",
        "pg_ddl": "VARCHAR(32) NOT NULL DEFAULT 'v0.1-calib'",
    },
]


def _is_postgres(db_url: str) -> bool:
    return db_url.startswith("postgresql") or db_url.startswith("postgres")


def _column_exists_sync(sync_conn: Any, table: str, column: str) -> bool:
    insp = inspect(sync_conn)
    cols = [c["name"] for c in insp.get_columns(table)]
    return column in cols


async def migrate_extracted_fields(
    engine: AsyncEngine | None = None,
    *,
    drop: bool = False,
) -> dict:
    """幂等迁移 extracted_fields 表（新增 display_rule_version 列）。

    Args:
        engine: AsyncEngine，不传则用 settings.DATABASE_URL 构造
        drop: True 时先删除列再添加（仅测试用，生产勿用）

    Returns:
        迁移结果 dict
    """
    if engine is None:
        db_url = settings.DATABASE_URL
        if not db_url:
            db_url = "sqlite+aiosqlite:///:memory:"
        engine = create_async_engine(db_url, echo=False)

    result: dict[str, Any] = {
        "table": False,
        "migrated": [],
        "skipped": [],
        "errors": [],
        "idempotent_run2": False,
    }
    is_pg = _is_postgres(str(engine.url))

    # 确保表存在（空数据库场景）
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"create_all 警告: {exc}")

    for run_idx in range(2):
        async with engine.begin() as conn:
            # 检查表存在
            def _chk(sync_c):
                insp = inspect(sync_c)
                return insp.has_table("extracted_fields")
            has_table = await conn.run_sync(_chk)
            if not has_table:
                if run_idx == 0:
                    result["table"] = False
                else:
                    result["idempotent_run2"] = True
                continue

            if run_idx == 0:
                result["table"] = True

            # 逐列处理
            for col in NEW_COLUMNS:
                cname = col["name"]
                exists = await conn.run_sync(
                    lambda sc, c=cname: _column_exists_sync(sc, "extracted_fields", c)
                )
                if drop and run_idx == 0:
                    if exists:
                        await conn.execute(
                            text(f'ALTER TABLE extracted_fields DROP COLUMN "{cname}"')
                        )
                        exists = False

                if exists:
                    if run_idx == 0:
                        result["skipped"].append(cname)
                    continue

                ddl = col["pg_ddl"] if is_pg else col["sqlite_ddl"]
                try:
                    await conn.execute(
                        text(f'ALTER TABLE extracted_fields ADD COLUMN "{cname}" {ddl}')
                    )
                    if run_idx == 0:
                        result["migrated"].append(cname)
                except Exception as exc:  # noqa: BLE001
                    msg = f"ALTER COLUMN {cname} 失败: {exc}"
                    result["errors"].append(msg)
                    if "duplicate" in str(exc).lower() or "already exists" in str(exc).lower():
                        if run_idx == 0 and cname not in result["skipped"]:
                            result["skipped"].append(cname)

        if run_idx == 0:
            result["_migrated_run1"] = list(result["migrated"])

    # 幂等性验证
    if not any("duplicate" in e.lower() or "already" in e.lower()
               for e in result["errors"]) or len(result["errors"]) == 0:
        result["idempotent_run2"] = True
    result["errors"] = [e for e in result["errors"]
                        if "duplicate" not in e.lower() and "already exists" not in e.lower()]
    return result


async def _verify_columns(engine: AsyncEngine):
    """验证迁移后列确实存在。"""
    async with engine.connect() as conn:
        def _cols(sync_c):
            insp = inspect(sync_c)
            return [c["name"] for c in insp.get_columns("extracted_fields")]
        cols = await conn.run_sync(_cols)
    return cols


async def main() -> int:
    print("=" * 70)
    print("W3-05 迁移: extracted_fields 新增 display_rule_version")
    print("=" * 70)
    print(f"DATABASE_URL = {settings.DATABASE_URL}")

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    try:
        r = await migrate_extracted_fields(engine)
    finally:
        await engine.dispose()

    print("\n--- 迁移结果 ---")
    print(f"  表存在:         {r['table']}")
    print(f"  新增列:         {r['migrated'] or '(无)'}")
    print(f"  已存在跳过:     {r['skipped'] or '(无)'}")
    print(f"  幂等性(2次):    {'OK' if r['idempotent_run2'] else 'FAIL'}")
    if r["errors"]:
        print(f"  错误:           {len(r['errors'])} 个")
        for e in r["errors"]:
            print(f"    - {e}")

    # 验证结构
    engine2 = create_async_engine(settings.DATABASE_URL, echo=False)
    try:
        cols = await _verify_columns(engine2)
    finally:
        await engine2.dispose()
    want = {"display_rule_version"}
    missing = want - set(cols)
    ok = not missing and not r["errors"] and r["idempotent_run2"]

    print(f"\n  列结构:         {sorted(cols)}")
    if missing:
        print(f"  缺失列:         {sorted(missing)}")
    print(f"\n结论: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)
