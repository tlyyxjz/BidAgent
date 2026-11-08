#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""T5 · 量清 aiosqlite 跨线程跳一次到底值多少毫秒。

为什么要有这个脚本（`压测W1_跑什么与为什么_20261106.md` §7.7）：
  W3A 交付说明「已知限制 #3」写了一句「合并后每次请求仍要跨线程一次（约 5ms 级）」。
  那是一句**没量过的推断** —— 按标准铁律 11 不许当事实写。本脚本把它变成实测。

做法（同进程、同一份库、同一段 SQL，只换执行通道）：
  A  async   SQLAlchemy AsyncSession + aiosqlite  （= 线上真实通道，含跨线程）
  B  sync    sqlite3 直连（同进程、常驻连接、无跨线程）
  ⇒ 同一段 SQL 的 (A - B) 就是**跨线程 + SQLAlchemy 包装**的成本。

⚠️ 口径说明（不写清就不许引用这两个数）：
  - 两种通道都**复用同一条连接**，冷连接建立成本已被 warmup 排除；
  - A 侧不含 session 构造/提交开销（session 在循环外创建）；
  - B 侧是同步阻塞调用，A 侧是 await 一次线程池往返；
  - 本机 Windows + Python 3.13，绝对值不可搬到别的机器，只看量级与占比。

用法：
    python _perf/thread_hop_probe.py
    python _perf/thread_hop_probe.py -n 200
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "data" / "bidagent.db"
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
os.chdir(REPO)  # 相对路径的 DATABASE_URL 依赖 cwd

# 与 decompose_request.py 同款环境变量（缺一个 import app 就炸）
os.environ.setdefault("SECRET_KEY", "a" * 64)
os.environ.setdefault("ADMIN_SECRET", "test-admin-secret-12345")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/bidagent.db")
os.environ.setdefault("FREE_TIER_DAILY_LIMIT", "10000000")

TRIVIAL = "SELECT 1"
JOIN_SQL = """
SELECT t.id, t.project_name, t.notice_type, t.created_at, t.source_platform,
       e.field_name, e.raw_value
  FROM tenders t
  LEFT JOIN extracted_fields e
         ON e.tender_id = t.id
        AND e.field_name IN ('amount', 'purchaser_name', 'publish_date')
 ORDER BY t.id, e.id
"""


def _stat(xs: list[float]) -> tuple[float, float, float]:
    xs = sorted(xs)
    n = len(xs)
    p50 = statistics.median(xs)
    p95 = xs[min(n - 1, int(round(0.95 * (n - 1))))]
    return statistics.mean(xs), p50, p95


async def _run_async(n: int, warmup: int) -> dict[str, float]:
    from sqlalchemy import text

    from app.models.database import AsyncSessionLocal

    out: dict[str, float] = {}
    async with AsyncSessionLocal() as db:
        for _ in range(warmup):
            await db.execute(text(TRIVIAL))
            (await db.execute(text(JOIN_SQL))).all()

        for label, sql in (("trivial", TRIVIAL), ("join", JOIN_SQL)):
            samples: list[float] = []
            for _ in range(n):
                t0 = time.perf_counter()
                res = await db.execute(text(sql))
                res.all()
                samples.append((time.perf_counter() - t0) * 1000)
            out[label] = _stat(samples)
    return out


def _run_sync(n: int, warmup: int) -> dict[str, float]:
    out: dict[str, float] = {}
    conn = sqlite3.connect(str(DB), check_same_thread=False)
    try:
        cur = conn.cursor()
        for _ in range(warmup):
            cur.execute(TRIVIAL).fetchall()
            cur.execute(JOIN_SQL).fetchall()

        for label, sql in (("trivial", TRIVIAL), ("join", JOIN_SQL)):
            samples: list[float] = []
            for _ in range(n):
                t0 = time.perf_counter()
                cur.execute(sql).fetchall()
                samples.append((time.perf_counter() - t0) * 1000)
            out[label] = _stat(samples)
    finally:
        conn.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=100, help="每种通道每种 SQL 的采样次数")
    ap.add_argument("--warmup", type=int, default=10)
    args = ap.parse_args()

    if not DB.exists():
        print(f"[ERR] 库不存在：{DB}")
        return 2

    a = asyncio.run(_run_async(args.n, args.warmup))
    b = _run_sync(args.n, args.warmup)

    print(f"\n[T5] aiosqlite 跨线程探针 · n={args.n} warmup={args.warmup} · 库 {DB.name}")
    print("  同一段 SQL 只换执行通道，两通道各自复用同一条连接\n")
    print(f"  {'SQL':<10}{'通道':<10}{'mean':>9}{'p50':>9}{'p95':>9}")
    print("  " + "-" * 47)
    for label, cn in (("trivial", "SELECT 1"), ("join", "LEFT JOIN")):
        for tag, d in (("async", a), ("sync", b)):
            m, p50, p95 = d[label]
            print(f"  {cn:<10}{tag:<10}{m:>8.2f}{p50:>9.2f}{p95:>9.2f}")

    print("  " + "-" * 47)
    print("  跨线程 + SQLAlchemy 包装（async − sync，按 mean）：")
    for label, cn in (("trivial", "SELECT 1"), ("join", "LEFT JOIN")):
        delta = a[label][0] - b[label][0]
        ratio = (a[label][0] / b[label][0]) if b[label][0] else float("inf")
        print(f"    {cn:<10} {delta:+.2f} ms   (async 是 sync 的 {ratio:.1f} 倍)")

    hop = a["trivial"][0] - b["trivial"][0]
    join_total = a["join"][0]
    print("\n  判据：")
    print(f"    跨线程固定成本 hop ≈ {hop:.2f} ms（用 SELECT 1 隔离掉查询本身的工作量）")
    if join_total:
        print(f"    它占一次 LEFT JOIN 总耗时 {join_total:.2f} ms 的 {hop / join_total * 100:.1f}%")
    print("    ⇒ hop 是**每次 await db.execute 都要付**的固定税，付几次就是几倍；")
    print("      但单次 hop 若远小于查询自身工作量，去掉它收益有限 —— 别为它动线程模型。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
