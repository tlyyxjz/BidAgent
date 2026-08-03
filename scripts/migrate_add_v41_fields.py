"""v4.1 §4.8 数据迁移：ExtractedField 表新增 5 个三维质量维度字段。

新增字段：
- cross_verify_status (String(32), NOT NULL, default='single_source')
- source_quality_snapshot (String(30), NULLABLE)
- field_type (String(20), NULLABLE)
- semantic_role (String(50), NULLABLE)
- value_count (Integer, NOT NULL, default=1)

迁移策略：
- 幂等：列已存在则跳过
- 安全：不修改现有数据
- 向后兼容：保留 cross_verified 布尔字段
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到 sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from app.models.database import AsyncSessionLocal


NEW_COLUMNS = [
    {
        "name": "cross_verify_status",
        "definition": "VARCHAR(32) NOT NULL DEFAULT 'single_source'",
        "check_exists": "SELECT COUNT(*) FROM pragma_table_info('extracted_fields') WHERE name='cross_verify_status'",
    },
    {
        "name": "source_quality_snapshot",
        "definition": "VARCHAR(30)",
        "check_exists": "SELECT COUNT(*) FROM pragma_table_info('extracted_fields') WHERE name='source_quality_snapshot'",
    },
    {
        "name": "field_type",
        "definition": "VARCHAR(20)",
        "check_exists": "SELECT COUNT(*) FROM pragma_table_info('extracted_fields') WHERE name='field_type'",
    },
    {
        "name": "semantic_role",
        "definition": "VARCHAR(50)",
        "check_exists": "SELECT COUNT(*) FROM pragma_table_info('extracted_fields') WHERE name='semantic_role'",
    },
    {
        "name": "value_count",
        "definition": "INTEGER NOT NULL DEFAULT 1",
        "check_exists": "SELECT COUNT(*) FROM pragma_table_info('extracted_fields') WHERE name='value_count'",
    },
]


async def migrate() -> None:
    """执行迁移。"""
    async with AsyncSessionLocal() as db:
        added = []
        skipped = []
        for col in NEW_COLUMNS:
            exists = (await db.execute(text(col["check_exists"]))).scalar()
            if exists:
                skipped.append(col["name"])
                continue
            await db.execute(
                text(f"ALTER TABLE extracted_fields ADD COLUMN {col['name']} {col['definition']}")
            )
            added.append(col["name"])
        await db.commit()

    print(f"v4.1 §4.8 迁移完成: 新增 {len(added)} 列 {added}, 跳过 {len(skipped)} 列 {skipped}")


if __name__ == "__main__":
    asyncio.run(migrate())
