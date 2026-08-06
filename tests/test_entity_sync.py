"""P0-1：四层实体同步器测试（v4.1 第四章）。"""
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.database import Base
from app.models.organization import Organization, PartyRole
from app.models.tender import Tender
from app.models.tender_project import (
    NoticeParticipant,
    NoticeSource,
    TenderNotice,
    TenderProject,
)
from app.processors.entity_sync import (
    map_notice_type,
    map_platform_type,
    sync_identifiers_from_fields,
    sync_participants_from_fields,
    upsert_tender_entities,
)


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _make_tender(**kw):
    defaults = dict(
        project_name="测试项目",
        tender_org="某市财政局",
        agency="某某招标代理有限公司",
        win_company="某某科技有限公司",
        notice_type="中标",
        source_url="https://example.com/a",
        source_platform="ccgp",
    )
    defaults.update(kw)
    return Tender(**defaults)


async def test_full_chain_created(db):
    t = _make_tender()
    db.add(t)
    await db.flush()

    assert await upsert_tender_entities(db, t) is True
    await db.commit()

    assert (await db.execute(select(func.count(TenderProject.project_id)))).scalar() == 1
    assert (await db.execute(select(func.count(TenderNotice.notice_id)))).scalar() == 1
    assert (await db.execute(select(func.count(NoticeSource.notice_source_id)))).scalar() == 1
    assert (await db.execute(select(func.count(Organization.organization_id)))).scalar() == 3
    assert (await db.execute(select(func.count(PartyRole.id)))).scalar() == 3
    assert (await db.execute(select(func.count(NoticeParticipant.participant_id)))).scalar() == 3

    notice_type = (await db.execute(select(TenderNotice.notice_type))).scalar()
    assert notice_type == "award"  # "中标" 映射为 award


async def test_idempotent(db):
    t = _make_tender()
    db.add(t)
    await db.flush()

    assert await upsert_tender_entities(db, t) is True
    assert await upsert_tender_entities(db, t) is False
    await db.commit()

    assert (await db.execute(select(func.count(TenderProject.project_id)))).scalar() == 1
    assert (await db.execute(select(func.count(Organization.organization_id)))).scalar() == 3


async def test_org_dedup_across_tenders(db):
    t1 = _make_tender(source_url="https://example.com/a")
    t2 = _make_tender(source_url="https://example.com/b", project_name="测试项目2")
    db.add_all([t1, t2])
    await db.flush()

    await upsert_tender_entities(db, t1)
    await upsert_tender_entities(db, t2)
    await db.commit()

    # 同名组织跨公告只建一条 Organization，PartyRole 各 3 条
    assert (await db.execute(select(func.count(Organization.organization_id)))).scalar() == 3
    assert (await db.execute(select(func.count(PartyRole.id)))).scalar() == 6
    assert (await db.execute(select(func.count(TenderProject.project_id)))).scalar() == 2


async def test_missing_org_fields(db):
    t = _make_tender(tender_org=None, agency=None, win_company=None)
    db.add(t)
    await db.flush()

    assert await upsert_tender_entities(db, t) is True
    await db.commit()
    assert (await db.execute(select(func.count(Organization.organization_id)))).scalar() == 0
    assert (await db.execute(select(func.count(PartyRole.id)))).scalar() == 0


async def test_synthetic_url_when_missing(db):
    t = _make_tender(source_url=None)
    db.add(t)
    await db.flush()

    await upsert_tender_entities(db, t)
    await db.commit()
    url = (await db.execute(select(NoticeSource.source_url))).scalar()
    assert url == f"migrated://tender/{t.id}"


def test_map_notice_type():
    assert map_notice_type("中标") == "award"
    assert map_notice_type("招标") == "tender"
    assert map_notice_type(None) == "tender"
    assert map_notice_type("奇葩类型") == "other"


def test_map_platform_type():
    assert map_platform_type("ccgp") == "government"
    assert map_platform_type("ccgp:official_repost") == "government"
    assert map_platform_type("某商业网") == "commercial"
    assert map_platform_type(None) == "unknown"


async def _seed_field(db, tender_id, field_name, raw_value, status="present"):
    from app.models._extracted_field import ExtractedField

    db.add(
        ExtractedField(
            tender_id=tender_id,
            field_name=field_name,
            field_status=status,
            raw_value=raw_value,
            support_level="direct",
        )
    )
    await db.flush()


async def test_sync_participants_from_fields(db):
    t = _make_tender(tender_org=None, agency=None, win_company=None)
    db.add(t)
    await db.flush()
    await upsert_tender_entities(db, t)

    await _seed_field(db, t.id, "purchaser_name", "某大学")
    await _seed_field(db, t.id, "winner_name", "某某科技公司")
    await _seed_field(db, t.id, "winner_name", "其他公司")  # 多值不压平

    n = await sync_participants_from_fields(db, t)
    await db.commit()

    assert n == 3
    assert (await db.execute(select(func.count(Organization.organization_id)))).scalar() == 3
    assert (await db.execute(select(func.count(PartyRole.id)))).scalar() == 3

    # 幂等：重跑不新增
    n2 = await sync_participants_from_fields(db, t)
    await db.commit()
    assert n2 == 3
    assert (await db.execute(select(func.count(PartyRole.id)))).scalar() == 3


async def test_sync_participants_skips_absent(db):
    t = _make_tender(tender_org=None, agency=None, win_company=None)
    db.add(t)
    await db.flush()
    await upsert_tender_entities(db, t)

    await _seed_field(db, t.id, "purchaser_name", "无值单位", status="absent")
    n = await sync_participants_from_fields(db, t)
    await db.commit()
    assert n == 0
    assert (await db.execute(select(func.count(Organization.organization_id)))).scalar() == 0


async def test_sync_participants_no_chain_returns_zero(db):
    t = _make_tender(tender_org=None, agency=None, win_company=None)
    db.add(t)
    await db.flush()
    # 未建四层链
    assert await sync_participants_from_fields(db, t) == 0


async def test_sync_identifiers_from_fields(db):
    t = _make_tender()
    db.add(t)
    await db.flush()
    await upsert_tender_entities(db, t)

    await _seed_field(db, t.id, "project_identifier", "ZB-2026-001")
    n = await sync_identifiers_from_fields(db, t)
    await db.commit()
    assert n == 1

    from app.models.tender_project import ProjectIdentifier

    rows = (await db.execute(select(ProjectIdentifier))).scalars().all()
    assert len(rows) == 1
    assert rows[0].raw_value == "ZB-2026-001"
    assert rows[0].identifier_type == "procurement"

    # 幂等
    n2 = await sync_identifiers_from_fields(db, t)
    await db.commit()
    assert n2 == 1
    assert len((await db.execute(select(ProjectIdentifier))).scalars().all()) == 1
