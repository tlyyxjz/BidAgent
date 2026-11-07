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
from sqlalchemy import and_, func, select
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

            db_count = (
                await db.execute(select(func.count()).select_from(Tender))
            ).scalar_one()
    finally:
        await engine.dispose()

    expected = _legacy_assembly(old_rows, old_fields)
    actual = _new_assembly(new_rows)

    # ⚠️ 不写死「829」：库条数随抓取增长，写死会让任何一次数据更新都误报为代码缺陷。
    #    这里只要求「两条路条数一致，且都等于库里真实条数」。
    assert len(actual) == db_count, f"新实现条数与库不一致：{len(actual)} vs {db_count}"
    assert len(expected) == db_count, f"旧实现条数与库不一致：{len(expected)} vs {db_count}"
    assert db_count > 0, "真实库是空的，这条测试失去意义"
    assert json.dumps(expected, ensure_ascii=False) == json.dumps(actual, ensure_ascii=False), (
        "新单查询实现与旧双查询实现不等价 —— 字段口径被改动了"
    )
    # 键序单独再钉一次（json.dumps 已经覆盖，这里给出可读失败信息）
    assert list(actual[0].keys()) == KEYS


@pytest.mark.asyncio
@requires_db
async def test_endpoint_function_matches_legacy_on_real_db():
    """直接调用**线上** `list_tenders`（不是复制品），拿真实库与旧实现逐字节比对。

    为什么必须有这一条（2026-11-07 自查发现）：
      上面 `test_equivalence_on_real_db` 里的 `_new_assembly` 是**照抄**的一份逻辑。
      如果线上代码改了、这份抄件忘了改，那条测试仍然全绿 —— 它测的是"抄件 vs 抄件"，
      不是"线上 vs 旧实现"。这条直接打真实函数，才是能拦住「改代码不改测试」的守卫。
    """
    from app.api.real_demo import list_tenders
    from app.models.evidence import ExtractedField
    from app.models.tender import Tender

    engine = create_async_engine(f"sqlite+aiosqlite:///{REAL_DB}")
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as db:
            # 旧实现的两段查询（口径与改前代码一致）
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
            # 线上真实函数
            resp = await list_tenders(db)
    finally:
        await engine.dispose()

    payload = json.loads(resp.body)
    actual = payload["data"]["tenders"]
    expected = _legacy_assembly(old_rows, old_fields)

    assert len(actual) == len(expected), f"条数不一致：{len(actual)} vs {len(expected)}"
    assert json.dumps(expected, ensure_ascii=False) == json.dumps(actual, ensure_ascii=False), (
        "线上 list_tenders 与旧双查询实现不等价 —— 说明代码改了但口径变了"
    )
    assert list(actual[0].keys()) == KEYS, "线上实现的 JSON 键序被改了"


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


@pytest.mark.asyncio
async def test_duplicate_non_empty_field_takes_lowest_id(client):
    """同名重复且**都非空**时，必须取 `extracted_fields.id` 最小的那条。

    为什么补这一条：旧实现的取值条件是 `if name not in d and f.raw_value` —— 空值被跳过，
    所以"空值在前还是非空在前"结果一样，原有测试测不出顺序。真实库里有 **6 组**
    「同名且多个不同非空值」，这个语义必须被钉住。

    🔴 2026-11-07 变异测试的实测结论（不许美化）：
      **删掉 `order_by` 里的 `ExtractedField.id` 后，这条测试和原有 4 条仍然全绿。**
      已查明原因：SQLite 走 `ix_extracted_fields_tender_id` 索引扫描，天然按
      `(tender_id, rowid)` 顺序返回，`rowid` 就是 `id`，因此**有无该排序子句，1042 行
      完整序列完全相同**（已用脚本逐行比对确认）。
      ⇒ 这条测试守的是「最终取值对不对」，**不能**用来检测排序子句缺失。
        排序子句的必要性靠 docstring 与「换库/换执行计划会变」的提示保证，不靠测试。
      ⇒ 不要在这里写"已由测试守卫排序"——那是没被证实的。
    """
    from app.models.database import AsyncSessionLocal
    from app.models.evidence import ExtractedField
    from app.models.tender import Tender

    async with AsyncSessionLocal() as db:
        db.add(Tender(
            id=3, project_name="重复字段公告", notice_type="招标公告",
            created_at=datetime(2026, 3, 7, 10, 0, 0),
            updated_at=datetime(2026, 3, 7, 10, 0, 0), source_platform="ccgp",
        ))
        # 先插的 id 小；两个值都非空且不同 ⇒ 必须取「100 万元」
        db.add_all([
            ExtractedField(tender_id=3, field_name="amount", raw_value="100 万元"),
            ExtractedField(tender_id=3, field_name="amount", raw_value="200 万元"),
        ])
        await db.commit()

    r = await client.get("/api/real/tenders")
    assert r.status_code == 200
    t3 = [t for t in r.json()["data"]["tenders"] if t["id"] == 3]
    assert len(t3) == 1
    assert t3[0]["amount"] == "100 万元", (
        "同名重复字段应取 id 最小的非空值；取到别的说明 order_by 丢了 ExtractedField.id"
    )
