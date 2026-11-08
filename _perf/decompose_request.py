#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""T0 · 单请求分解：把 /api/real/tenders 的耗时拆成四段 + 残差。

为什么要有这个脚本（`压测W1_跑什么与为什么_20261106.md` §2）：
  H2（Python 拼装是 CPU 热点）与 H3（aiosqlite 线程池串行）在压测曲线上长得一模一样，
  只有「拆一次请求」能分开。四段之和如果远小于压测得到的 37ms，说明真正的开销
  不在业务代码里 —— 那是 H4（中间件/鉴权/连接建立/ORM 开销），比 H2/H3 更值得查。

两路对照（同一进程内跑同一请求）：
  A 手写计时  SQLAlchemy 事件钩子量两段 SQL，客户端量端到端，json.dumps 量序列化；
              「段3 拼装」与框架开销合在残差里 —— 残差大本身就是要找的 H4 信号。
  B cProfile  函数级 cumtime Top15，用来交叉验证 A 指出的段。

用法：
    python _perf/decompose_request.py                # 默认 5 次，首次丢弃（冷启动）
    python _perf/decompose_request.py -n 20 --warmup 5
    python _perf/decompose_request.py --url http://127.0.0.1:8011   # 顺带量一次真 HTTP

⚠️ 本脚本 inproc 模式不含 uvicorn 的 HTTP 解析与 socket 拷贝，绝对值会比压测小；
   要看占比，不要拿绝对值去对 37ms。真 HTTP 对照请加 --url。
"""
from __future__ import annotations

import argparse
import cProfile
import io
import json
import os
import pstats
import statistics
import sys
import time
from pathlib import Path

# ---- 导入 app 之前必须设的环境变量（与 tests/conftest.py 对齐，缺一不可） ----
os.environ.setdefault("SECRET_KEY", "a" * 64)
os.environ.setdefault("ADMIN_SECRET", "test-admin-secret-12345")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/bidagent.db")
os.environ.setdefault("FREE_TIER_DAILY_LIMIT", "10000000")

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from sqlalchemy import event  # noqa: E402

_STMTS: list[tuple[str, float]] = []  # (statement, ms)


def _install_sql_timing() -> None:
    from app.models.database import engine as async_engine

    sync_engine = async_engine.sync_engine

    @event.listens_for(sync_engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):
        context._t0 = time.perf_counter()

    @event.listens_for(sync_engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):
        t0 = getattr(context, "_t0", None)
        if t0 is not None:
            _STMTS.append((statement, (time.perf_counter() - t0) * 1000.0))


def _classify(statement: str) -> str:
    s = " ".join(statement.split()).lower()
    if "from tenders" in s:
        return "段1 主查询"
    if "extracted_fields" in s:
        return "段2 字段查询"
    return "其他 SQL"


def _run_inproc(n: int, warmup: int, path: str) -> dict:
    from fastapi.testclient import TestClient

    from app.main import app

    _install_sql_timing()
    client = TestClient(app)

    for _ in range(warmup):
        client.get(path)

    rows = []
    for _ in range(n):
        _STMTS.clear()
        t0 = time.perf_counter()
        resp = client.get(path)
        total_ms = (time.perf_counter() - t0) * 1000.0
        body = resp.content

        # 段4：服务端序列化成本的近似（stdlib json；FastAPI 默认走它）
        payload = json.loads(body)
        t1 = time.perf_counter()
        json.dumps(payload, ensure_ascii=False)
        json_ms = (time.perf_counter() - t1) * 1000.0

        by_seg: dict[str, float] = {}
        for stmt, ms in _STMTS:
            key = _classify(stmt)
            by_seg[key] = by_seg.get(key, 0.0) + ms

        rows.append(
            {
                "status": resp.status_code,
                "total_ms": total_ms,
                "s1": by_seg.get("段1 主查询", 0.0),
                "s2": by_seg.get("段2 字段查询", 0.0),
                "s4": json_ms,
                "bytes": len(body),
                "sql_count": len(_STMTS),
            }
        )
    return {"rows": rows}


def _profile_once(path: str) -> str:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    pr = cProfile.Profile()
    pr.enable()
    client.get(path)
    pr.disable()

    buf = io.StringIO()
    st = pstats.Stats(pr, stream=buf).sort_stats("tottime")
    buf.write("  [B1] 按 tottime 排（真烧 CPU 的函数；cumtime 会把 TestClient 传输层顶到最上面，没用）\n")
    st.print_stats(12)
    buf.write("\n  [B2] 只列 app 目录下的函数 —— 这一段才和四段分解对得上\n")
    try:
        st.print_stats("app", 15)
    except TypeError:
        pass
    return buf.getvalue()


def _http_once(url: str, path: str) -> dict | None:
    try:
        import httpx
    except ImportError:
        return None
    try:
        with httpx.Client(timeout=60.0) as c:
            t0 = time.perf_counter()
            r = c.get(url.rstrip("/") + path)
            ms = (time.perf_counter() - t0) * 1000.0
        return {"status": r.status_code, "total_ms": ms, "bytes": len(r.content)}
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 真 HTTP 对照失败：{e}")
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--n", type=int, default=5, help="稳态采样次数（默认 5）")
    ap.add_argument("--warmup", type=int, default=1, help="先丢弃几次冷启动（默认 1）")
    ap.add_argument("--path", default="/api/real/tenders")
    ap.add_argument("--url", default="", help="如 http://127.0.0.1:8011，量一次真 HTTP 做对照")
    ap.add_argument("--no-profile", action="store_true", help="跳过 cProfile 那一路")
    args = ap.parse_args()

    print(f"[T0] inproc {args.path}  warmup={args.warmup}  n={args.n}")
    out = _run_inproc(args.n, args.warmup, args.path)
    rows = [r for r in out["rows"] if r["status"] == 200]
    if not rows:
        print("ABORT: 没有一次 200，先确认接口与数据库可用（python run_demo.py）")
        return 2

    med_total = statistics.median(r["total_ms"] for r in rows)
    mean = lambda k: statistics.mean(r[k] for r in rows)  # noqa: E731
    s1, s2, s4 = mean("s1"), mean("s2"), mean("s4")
    resid = med_total - s1 - s2 - s4
    size_kb = mean("bytes") / 1024.0

    print()
    print(f"  样本 {len(rows)} 次 · 响应体 {size_kb:.1f} KB · SQL 语句 {rows[0]['sql_count']} 条/请求")
    print(f"  {'段':<14}{'ms':>9}{'占总时延':>10}")
    print(f"  {'-'*33}")
    for name, ms in (
        ("段1 主查询", s1),
        ("段2 字段查询", s2),
        ("段4 序列化(近似)", s4),
        ("残差(段3+框架)", resid),
    ):
        pct = (ms / med_total * 100.0) if med_total else 0.0
        print(f"  {name:<14}{ms:>9.2f}{pct:>9.1f}%")
    print(f"  {'-'*33}")
    print(f"  {'端到端(inproc)':<14}{med_total:>9.2f}{100.0:>9.1f}%")
    print(f"  SQL 合计 {s1 + s2:.2f} ms（{(s1 + s2) / med_total * 100:.1f}%）· "
          f"P50 {statistics.median(r['total_ms'] for r in rows):.2f} ms · "
          f"max {max(r['total_ms'] for r in rows):.2f} ms")

    # ---- 判据（直接照文档 §2 T0 的表） ----
    segs = {"段1 主查询": s1, "段2 字段查询": s2, "段4 序列化": s4, "残差(段3+框架)": resid}
    top_name, top_ms = max(segs.items(), key=lambda kv: kv[1])
    print()
    print("  注意：inproc 的残差里含 TestClient/ASGI 自身的开销，所以它会偏大；"
          "加 --url 跑一次真 HTTP，两者之差才是 uvicorn+网络栈的成本。")
    if resid >= max(s1, s2, s4) and med_total and resid / med_total >= 0.50:
        print(f"  判据：残差 {resid:.2f} ms 占 {resid / med_total * 100:.0f}%，大于任何一个可测段 "
              f"⇒ **H4 优先**：开销在段3 拼装 / 中间件 / 鉴权 / 连接建立 / ORM 里，"
              f"不在两段 SQL 也不在序列化")
        print(f"        下一步：用 cProfile 那一路看残差里是谁在吃 CPU；"
              f"若 cProfile 显示热点是 829 次循环 dict 构建，那就是 H2 而非 H4")
    elif top_ms / med_total >= 0.60:
        print(f"  判据：{top_name} 占 {top_ms / med_total * 100:.0f}% ≥60% ⇒ 主因锁定，直接进这一段")
    else:
        print(f"  判据：最大段是 {top_name}（{top_ms / med_total * 100:.0f}%），未过 60%；"
              f"分解无误，按占比最大那段验 H2/H3")

    if args.url:
        h = _http_once(args.url, args.path)
        if h:
            print(f"\n  真 HTTP 对照（含 uvicorn 解析与 socket）：{h['total_ms']:.2f} ms / "
                  f"{h['bytes'] / 1024:.1f} KB / status={h['status']}")
            print(f"  inproc 与真 HTTP 的差 = {h['total_ms'] - med_total:.2f} ms "
                  f"⇒ 这部分是 uvicorn + 网络栈，不属于四段中任何一段")

    if not args.no_profile:
        print("\n  ---- 路 B：cProfile cumtime Top15（只跑 1 次，绝对值被放大，看排序）----")
        print(_profile_once(args.path))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
