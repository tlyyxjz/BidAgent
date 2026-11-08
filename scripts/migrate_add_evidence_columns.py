"""大件五（证据再加固·快照）数据迁移：tenders 表新增 SHA-256 存证列。

新增字段：
- content_sha256 (CHAR(64), NULLABLE)：core_content 的 SHA-256 指纹
- raw_text_sha256 (CHAR(64), NULLABLE)：source_raw_text 的 SHA-256 指纹

入库时间戳复用既有 created_at（入库即写入），不新增列。

迁移策略：
- 幂等：列已存在则跳过
- 安全：不修改现有数据（存量哈希由 scripts/backfill_evidence_hashes.py 补齐）
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
        "name": "content_sha256",
        "definition": "CHAR(64)",
        "check_exists": (
            "SELECT COUNT(*) FROM pragma_table_info('tenders')"
            " WHERE name='content_sha256'"
        ),
    },
    {
        "name": "raw_text_sha256",
        "definition": "CHAR(64)",
        "check_exists": (
            "SELECT COUNT(*) FROM pragma_table_info('tenders')"
            " WHERE name='raw_text_sha256'"
        ),
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
                text(
                    f"ALTER TABLE tenders ADD COLUMN {col['name']} {col['definition']}"
                )
            )
            added.append(col["name"])
        await db.commit()

    print(
        f"大件五存证迁移完成: 新增 {len(added)} 列 {added}, "
        f"跳过 {len(skipped)} 列 {skipped}"
    )


if __name__ == "__main__":
    asyncio.run(migrate())
