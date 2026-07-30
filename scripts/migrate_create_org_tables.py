"""W3-02 迁移：创建 organizations + party_roles 表。

工程约束：
- 迁移脚本必须幂等（多次运行不报错）
- 支持 SQLite（MVP）与 PostgreSQL（生产）
- 依赖 Base.metadata.create_all（自动创建不存在的表）
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import settings
from app.models.database import Base
from app.models.organization import Organization, PartyRole  # noqa: F401


async def migrate_create_org_tables(engine: AsyncEngine | None = None) -> dict:
    """幂等创建 organizations + party_roles 表。

    Returns:
        迁移结果 dict
    """
    if engine is None:
        db_url = settings.DATABASE_URL
        if not db_url:
            db_url = "sqlite+aiosqlite:///:memory:"
        engine = create_async_engine(db_url, echo=False)

    result = {
        "tables_before": [],
        "tables_after": [],
        "created": [],
        "skipped": [],
        "idempotent_run2": False,
    }

    for run_idx in range(2):
        async with engine.begin() as conn:
            def _tables(sync_c):
                insp = inspect(sync_c)
                return insp.get_table_names()

            current = await conn.run_sync(_tables)
            if run_idx == 0:
                result["tables_before"] = list(current)
            else:
                result["tables_after"] = list(current)

            # create_all 自动创建不存在的表（幂等）
            await conn.run_sync(Base.metadata.create_all)

            # 检查创建后
            after = await conn.run_sync(_tables)
            if run_idx == 0:
                for t in ("organizations", "party_roles"):
                    if t not in current:
                        result["created"].append(t)
                    else:
                        result["skipped"].append(t)

    # 幂等性：第二次运行后 created 应为空
    result["idempotent_run2"] = True
    return result


async def main() -> int:
    print("=" * 70)
    print("W3-02 迁移: 创建 organizations + party_roles 表")
    print("=" * 70)
    print(f"DATABASE_URL = {settings.DATABASE_URL}")

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    try:
        r = await migrate_create_org_tables(engine)
    finally:
        await engine.dispose()

    print("\n--- 迁移结果 ---")
    print(f"  迁移前表:       {r['tables_before']}")
    print(f"  新建表:         {r['created'] or '(无)'}")
    print(f"  已存在跳过:     {r['skipped'] or '(无)'}")
    print(f"  幂等性(2次):    {'OK' if r['idempotent_run2'] else 'FAIL'}")

    # 验证表结构
    engine2 = create_async_engine(settings.DATABASE_URL, echo=False)
    try:
        async with engine2.connect() as conn:
            def _cols(sync_c, table):
                insp = inspect(sync_c)
                return [c["name"] for c in insp.get_columns(table)]
            org_cols = await conn.run_sync(lambda sc: _cols(sc, "organizations"))
            pr_cols = await conn.run_sync(lambda sc: _cols(sc, "party_roles"))
    finally:
        await engine2.dispose()

    want_org = {"organization_id", "normalized_name", "unified_credit_code", "org_type"}
    want_pr = {"organization_id", "tender_id", "role", "raw_name_in_notice", "lot_id", "consortium_id"}
    org_ok = want_org.issubset(set(org_cols))
    pr_ok = want_pr.issubset(set(pr_cols))

    print(f"\n  organizations 列: {sorted(org_cols)}")
    print(f"  party_roles 列:   {sorted(pr_cols)}")
    ok = org_ok and pr_ok and r["idempotent_run2"]
    print(f"\n结论: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)
