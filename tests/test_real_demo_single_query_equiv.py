# -*- coding: utf-8 -*-
"""W3A 守卫测试：`GET /api/real/tenders` 的单查询实现必须与旧双查询实现等价。

为什么要有这个测试（任务单 TASK-W3A）：
  这次改动只允许改「怎么取数」，不允许改「取到什么」。任何字段口径漂移
  （取哪一行、空值算不算、publish_date 回不回落）都必须在这里红。

三层：
  A 真实库等价性（data/bidagent.db，829 条）：旧双查询 vs 新单 JOIN，逐条逐键比对
  B 空值/回落语义（测试库种子数据，走真实 HTTP 接口）
  C 键序：JSON 里键的先后不同也算改契约（前端按固定键序渲染）
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

REPO = Path(__file__).resolve().parent.parent
REAL_DB = REPO / "data" / "bidagent.db"
TEST_DB = REPO / "data" / "test_bidagent.db"
requires_db = pytest.mark.skipif(not REAL_DB.exists(), reason="bidagent.db 不存在")

FIELDS = ["amount", "purchaser_name", "publish_date"]
KEYS = ["id", "name", "type", "amount", "purchaser", "publish_date", "platform"]


def _legacy_assembly(rows, fields) -> list[dict]:
    """旧实现（两段查询 + field_map）的纯 Python 部分，逐字照抄改前逻辑。"""
    field_map: dict[int, dict] = {}
    for f in fields:  # 已按 extracted_fields.id 升序
        d = field_map.setdefault(f.tender_id, {})
        if f.field_name not in d and f.raw_value:
            d[f.field_name] = f.raw_value

    out = []
    for r in rows:
        fm = field_map.get(r.id, {})
        out.append({
            "id": r.id,
            "name": r.project_name,
            "type": r.notice_type,
            "amount": fm.get("amount", ""),
            "purchaser": fm.get("purchaser_name", ""),
            "publish_date": fm.get("publish_date", "")
            or (r.created_at.strftime("%Y-%m-%d") if r.created_at else ""),
            "platform": r.source_platform or "",
        })
    return out


def _new_assembly(rows) -> list[dict]:
    """新实现（单条 LEFT JOIN + 分组）的纯 Python 部分，必须与 _legacy_assembly 等价。

    W3B：与 `real_demo.list_tenders` 一致，用元组解包而不是 Row 属性访问
    （实测 1042 行 3.67ms → 1.40ms）。解包顺序 = select 列顺序，错位会抛 ValueError。
    """
    by_id: dict[int, dict] = {}
    created_map: dict[int, object] = {}
    out: list[dict] = []
    for tid, pn, nt, cat, plat, fn, rv in rows:
        item = by_id.get(tid)
        if item is None:
            item = {
                "id": tid,
                "name": pn,
                "type": nt,
                "amount": "",
                "purchaser": "",
                "publish_date": "",
                "platform": plat or "",
            }
            by_id[tid] = item
            created_map[tid] = cat
            out.append(item)
        if fn in FIELDS and rv:
            key = "purchaser" if fn == "purchaser_name" else fn
            if not item[key]:
                item[key] = rv
    for item in out:
        if not item["publish_date"]:
            ca = created_map.get(item["id"])
            item["publish_date"] = ca.strftime("%Y-%m-%d") if ca else ""
    return out


@pytest.mark.asyncio
@requires_db
async def test_equivalence_on_real_db():
    """829 条真实数据：两条路必须产出完全相同的列表（含键序）。"""
    from app.models.evidence import ExtractedField
    from app.models.tender import Tender

    engine = create_async_engine(f"sqlite+aiosqlite:///{REAL_DB}")
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as db:
            # 旧路：两段
            old_rows = (
                await db.execute(
                    select(
                        Tender.id,
                        Tender.project_name,
                        Tender.notice_type,
                        Tender.created_at,
                        Tender.source_platform,
                    ).order_by(Tender.id)
                )
            ).all()
            ids = [r.id for r in old_rows]
            old_fields = (
                await db.execute(
                    select(ExtractedField)
                    .where(ExtractedField.tender_id.in_(ids))
                    .where(ExtractedField.field_name.in_(FIELDS))
                    .order_by(ExtractedField.id)
                )
            ).scalars().all()

            # 新路：一条 JOIN
            new_rows = (
                await db.execute(
                    select(
                        Tender.id,
                        Tender.project_name,
                        Tender.notice_type,
                        Tender.created_at,
                        Tender.source_platform,
                        ExtractedField.field_name,
                        ExtractedField.raw_value,
                    )
                    .outerjoin(
                        ExtractedField,
                        and_(
                            ExtractedField.tender_id == Tender.id,
                            ExtractedField.field_name.in_(FIELDS),
                        ),
                    )
                    .order_by(Tender.id, ExtractedField.id)
                )
            ).all()
    finally:
        await engine.dispose()

    expected = _legacy_assembly(old_rows, old_fields)
    actual = _new_assembly(new_rows)

    assert len(expected) == len(actual) == 829, f"条数变了：{len(expected)} vs {len(actual)}"
    assert json.dumps(expected, ensure_ascii=False) == json.dumps(actual, ensure_ascii=False), (
        "新单查询实现与旧双查询实现不等价 —— 字段口径被改动了"
    )
    # 键序单独再钉一次（json.dumps 已经覆盖，这里给出可读失败信息）
    assert list(actual[0].keys()) == KEYS


@pytest.mark.asyncio
async def test_empty_db_still_returns_list_empty(client):
    """没有任何 tender 时：返回空列表，不是 null。"""
    r = await client.get("/api/real/tenders")
    assert r.status_code == 200
    assert r.json()["data"]["tenders"] == []


@pytest.mark.asyncio
async def test_null_semantics_and_fallback(client):
    """无字段的公告：三个字段都是空串（不是 null）；publish_date 回落到 created_at。"""
    from app.models.database import AsyncSessionLocal
    from app.models.evidence import ExtractedField
    from app.models.tender import Tender

    async with AsyncSessionLocal() as db:
        db.add_all([
            Tender(
                id=1, project_name="无字段公告", notice_type="招标公告",
                created_at=datetime(2026, 3, 4, 10, 0, 0),
                updated_at=datetime(2026, 3, 4, 10, 0, 0), source_platform="ccgp",
            ),
            Tender(
                id=2, project_name="有字段公告", notice_type="中标公告",
                created_at=datetime(2026, 3, 5, 10, 0, 0),
                updated_at=datetime(2026, 3, 5, 10, 0, 0), source_platform=None,
            ),
        ])
        db.add_all([
            ExtractedField(tender_id=2, field_name="amount", raw_value="123.45 万元"),
            ExtractedField(tender_id=2, field_name="purchaser_name", raw_value=""),
            ExtractedField(tender_id=2, field_name="purchaser_name", raw_value="某某公司"),
            ExtractedField(tender_id=2, field_name="publish_date", raw_value="2026-03-06"),
        ])
        await db.commit()

    r = await client.get("/api/real/tenders")
    assert r.status_code == 200
    tenders = r.json()["data"]["tenders"]
    assert [t["id"] for t in tenders] == [1, 2], "必须按 id 升序"

    t1, t2 = tenders
    assert list(t1.keys()) == KEYS, "键序被改了"
    assert t1["amount"] == "" and t1["purchaser"] == ""
    assert t1["publish_date"] == "2026-03-04", "publish_date 必须回落到 created_at 的日期"
    assert t1["platform"] == "ccgp"

    assert t2["amount"] == "123.45 万元"
    assert t2["purchaser"] == "某某公司", "空 raw_value 不得占位，应取后一条非空值"
    assert t2["publish_date"] == "2026-03-06"
    assert t2["platform"] == "", "source_platform 为 NULL 时应是空串"
