"""四层实体回填：把现有 tenders 表数据回填到 v4.1 四层实体表。

对应总规划 v4.1 第四章四层聚合结构（P0-1 审计修复）。
幂等：以 NoticeSource.source_url 为去重键，可重复运行。

用法: python scripts/backfill_entities.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select


async def main() -> None:
    from app.models.database import AsyncSessionLocal, init_database
    from app.models.organization import Organization, PartyRole
    from app.models.tender_project import (
        NoticeParticipant,
        NoticeSource,
        NoticeVersion,
        TenderNotice,
        TenderProject,
    )
    from app.utils.data_migration import (
        migrate_participants_from_fields,
        migrate_tender_to_four_layer,
    )

    await init_database()
    async with AsyncSessionLocal() as db:
        result = await migrate_tender_to_four_layer(db)
        result2 = await migrate_participants_from_fields(db)
        print(f"参与方回填: {result2}")
        counts = {}
        for name, model in [
            ("tender_projects", TenderProject),
            ("tender_notices", TenderNotice),
            ("notice_sources", NoticeSource),
            ("notice_versions", NoticeVersion),
            ("organizations", Organization),
            ("party_roles", PartyRole),
            ("notice_participants", NoticeParticipant),
        ]:
            pk = list(model.__table__.primary_key.columns)[0]
            counts[name] = (await db.execute(select(func.count(pk)))).scalar()

    print(f"回填结果: {result}")
    for name, n in counts.items():
        print(f"  {name}: {n}")


if __name__ == "__main__":
    asyncio.run(main())
