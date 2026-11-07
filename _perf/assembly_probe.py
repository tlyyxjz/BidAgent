#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""T6 · 拼装段到底烧在哪：Row 属性访问 vs 元组解包。

为什么要有这个脚本（`压测W1_跑什么与为什么_20261106.md` §7.7）：
  W3A 之后重跑 T0，端到端 19.62ms，其中 SQL 只占 6.25ms（31.8%），
  **残差 12.47ms（63.5%）** 反而比改前更大。残差 = 拼装 + 框架(3.76ms)。
  ⇒ 拼装约 8.7ms，成了最大单块。本脚本验证它是不是 SQLAlchemy `Row` 属性访问的锅。

做法：同一条 SQL、同一份结果集，只换消费写法：
  A  attr   逐字段 `r.id` / `r.project_name` …（= 线上当前写法）
  B  tuple  `for tid, pn, nt, cat, plat, fn, rv in rows`（一次解包）
  两份输出必须 `json.dumps` 全等（含键序），否则提速无意义。

⚠️ 口径：
  - 必须用 ORM `select(...)` 而不是 `text(...)`，否则 `created_at` 会以 str 返回、
    与线上类型不符（这个坑已踩过一次）；
  - 只量**拼装**，不含 SQL 与序列化；
  - 本机 Windows + Python 3.13，看倍数不看绝对值。

用法：
    python _perf/assembly_probe.py
    python _perf/assembly_probe.py -n 50
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
os.chdir(REPO)

os.environ.setdefault("SECRET_KEY", "a" * 64)
os.environ.setdefault("ADMIN_SECRET", "test-admin-secret-12345")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/bidagent.db")
os.environ.setdefault("FREE_TIER_DAILY_LIMIT", "10000000")

KEYS = ("amount", "purchaser_name", "publish_date")


def asm_attr(rows):
    by_id: dict = {}
    created: dict = {}
    tenders: list = []
    for r in rows:
        item = by_id.get(r.id)
        if item is None:
            item = {
                "id": r.id,
                "name": r.project_name,
                "type": r.notice_type,
                "amount": "",
                "purchaser": "",
                "publish_date": "",
                "platform": r.source_platform or "",
            }
            by_id[r.id] = item
            created[r.id] = r.created_at
            tenders.append(item)
        n = r.field_name
        if n in KEYS and r.raw_value:
            k = "purchaser" if n == "purchaser_name" else n
            if not item[k]:
                item[k] = r.raw_value
    for it in tenders:
        if not it["publish_date"]:
            ca = created.get(it["id"])
            it["publish_date"] = ca.strftime("%Y-%m-%d") if ca else ""
    return tenders


def asm_tup(rows):
    by_id: dict = {}
    created: dict = {}
    tenders: list = []
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
            created[tid] = cat
            tenders.append(item)
        if fn in KEYS and rv:
            k = "purchaser" if fn == "purchaser_name" else fn
            if not item[k]:
                item[k] = rv
    for it in tenders:
        if not it["publish_date"]:
            ca = created.get(it["id"])
            it["publish_date"] = ca.strftime("%Y-%m-%d") if ca else ""
    return tenders


async def _main(n: int) -> int:
    from sqlalchemy import and_, select

    from app.models.database import AsyncSessionLocal
    from app.models.evidence import ExtractedField
    from app.models.tender import Tender

    stmt = (
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
                ExtractedField.field_name.in_(list(KEYS)),
            ),
        )
        .order_by(Tender.id, ExtractedField.id)
    )

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(stmt)).all()

    print(f"\n[T6] 拼装段探针 · rows={len(rows)} · n={n}")
    print(f"  {'写法':<8}{'min':>8}{'med':>8}{'p95':>8}  (ms)")
    print("  " + "-" * 34)
    res: dict[str, float] = {}
    for fn, tag in ((asm_attr, "attr"), (asm_tup, "tuple")):
        for _ in range(5):
            fn(rows)
        ts = []
        for _ in range(n):
            t0 = time.perf_counter()
            fn(rows)
            ts.append((time.perf_counter() - t0) * 1000)
        ts.sort()
        med = statistics.median(ts)
        p95 = ts[min(len(ts) - 1, int(round(0.95 * (len(ts) - 1))))]
        res[tag] = med
        print(f"  {tag:<8}{ts[0]:>8.2f}{med:>8.2f}{p95:>8.2f}")

    same = json.dumps(asm_attr(rows), ensure_ascii=False) == json.dumps(
        asm_tup(rows), ensure_ascii=False
    )
    print("  " + "-" * 34)
    print(f"  两份输出 json 全等（含键序）：{same}  条数 {len(asm_attr(rows))}")
    if res["attr"]:
        print(f"  元组解包相对属性访问：{(1 - res['tuple'] / res['attr']) * 100:+.1f}%")
    print(
        "  判据：若 tuple 明显更快且 json 全等 ⇒ 拼装的真实成本是 Row 的 7 次属性查找，\n"
        "        与 SQL 无关；这是一处纯消费侧改动，不改语义。"
    )
    return 0 if same else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=30)
    args = ap.parse_args()
    sys.exit(asyncio.run(_main(args.n)))
