"""打标入库集成测试：service 单元测试（LLM 全部 mock，不发真实请求）。"""
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models.database import AsyncSessionLocal
from app.models.notice_tag import NoticeTag
from app.models.tender import Tender
from app.tagging import service

pytestmark = pytest.mark.asyncio


async def _seed_tenders(n: int = 2) -> None:
    async with AsyncSessionLocal() as db:
        for i in range(1, n + 1):
            db.add(Tender(project_name=f"测试项目{i}", core_content=f"内容{i}"))
        await db.commit()


async def _tag_count() -> int:
    async with AsyncSessionLocal() as db:
        rows = await db.execute(select(NoticeTag.id))
        return len(rows.fetchall())


def _fixed_tags(industry="IT", region="北京", notice_type="招标"):
    async def _fake_tag(notices, concurrency=5):
        return [
            {**n, "industry": industry, "region": region, "notice_type": notice_type}
            for n in notices
        ]
    return _fake_tag


async def test_tag_pending_tenders_writes_rows_and_is_idempotent():
    await _seed_tenders(2)
    with patch.object(service, "tag_many_concurrent", side_effect=_fixed_tags()):
        async with AsyncSessionLocal() as db:
            summary = await service.tag_pending_tenders(db, limit=10)
    assert summary == {"tagged": 2, "errors": 0}
    assert await _tag_count() == 2

    # 幂等：已打标的公告不再重复打标
    with patch.object(service, "tag_many_concurrent", side_effect=_fixed_tags()):
        async with AsyncSessionLocal() as db:
            summary2 = await service.tag_pending_tenders(db, limit=10)
    assert summary2 == {"tagged": 0, "errors": 0}
    assert await _tag_count() == 2


async def test_tag_pending_tenders_restricted_to_tender_ids():
    await _seed_tenders(3)
    with patch.object(service, "tag_many_concurrent", side_effect=_fixed_tags()):
        async with AsyncSessionLocal() as db:
            summary = await service.tag_pending_tenders(db, limit=10, tender_ids=[2])
    assert summary["tagged"] == 1
    async with AsyncSessionLocal() as db:
        rows = await db.execute(select(NoticeTag.tender_id))
        assert [r[0] for r in rows.fetchall()] == [2]


async def test_tag_pending_tenders_survives_llm_failure():
    """LLM 批次整体失败：返回 errors、不上抛异常（主链路不能崩）。"""
    await _seed_tenders(1)

    async def _boom(notices, concurrency=5):
        raise RuntimeError("rate limited")

    with patch.object(service, "tag_many_concurrent", side_effect=_boom):
        async with AsyncSessionLocal() as db:
            summary = await service.tag_pending_tenders(db, limit=10)
    assert summary["errors"] >= 1
    assert summary["tagged"] == 0
    assert await _tag_count() == 0


async def test_tag_pending_tenders_skips_malformed_items():
    """单条返回非 dict（脏数据）只计 errors，不拖垮批次。"""
    await _seed_tenders(2)

    async def _dirty(notices, concurrency=5):
        return [None, {**notices[1], "industry": "医疗", "region": "上海", "notice_type": "中标"}]

    with patch.object(service, "tag_many_concurrent", side_effect=_dirty):
        async with AsyncSessionLocal() as db:
            summary = await service.tag_pending_tenders(db, limit=10)
    assert summary["tagged"] == 1
    assert summary["errors"] == 1
    assert await _tag_count() == 1


async def test_tag_pending_tenders_untagged_query_failure_safe():
    """查询阶段失败：返回 errors、不上抛。"""

    class _BoomSession:
        async def execute(self, *a, **kw):
            raise RuntimeError("db down")

    summary = await service.tag_pending_tenders(_BoomSession(), limit=10)
    assert summary["errors"] >= 1
    assert summary["tagged"] == 0
