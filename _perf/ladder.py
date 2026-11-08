#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""T3 + T4 · 阶梯压测（零第三方依赖，备用 locust 跑不了时用这个）。

为什么留这个备用件：2026-11-06 实测本机 pip 走默认源（清华镜像）返回 403、
换 pypi.org 能通但很慢，locust 装了一半就断。T3 是 W1 里唯一能给出「第二个拐点」
和合格 P99 的测试，不能卡在一个装不上的包上。本脚本只用标准库。

用法：
    python _perf/ladder.py --host http://127.0.0.1:8011 -l 1,10,50,100 -t 180
    python _perf/ladder.py -l 10 -t 60 --path /api/real/tenders      # 单档快跑

输出：每档 RPS / P50 / P95 / P99 / 错误率 / MB/s，并写 `_perf/out/ladder_<时间戳>.csv`。

⚠️ 口径与 locust 的差别（写报告时必须说）：
  本脚本是**闭环（closed-loop）**压测：每个虚拟用户拿到响应才发下一个请求。
  真实用户不会这样，所以稳态高负载下 P99 会偏乐观（coordinated omission）。
  用它回答「拐点在哪、每档样本够不够」，不要拿它和 locust 的绝对值混着比。
"""
from __future__ import annotations

import argparse
import csv
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _pct(sorted_vals: list[float], q: float) -> float:
    """最近秩法（nearest-rank）百分位。样本 <100 时 P99 就等于最大值 —— 这正是要补样本的原因。"""
    if not sorted_vals:
        return 0.0
    k = max(1, int(round(q / 100.0 * len(sorted_vals))))
    return sorted_vals[min(k, len(sorted_vals)) - 1]


def _one_get(url: str, timeout: float) -> tuple[float, int, int]:
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read()
            return (time.perf_counter() - t0) * 1000.0, resp.status, len(body)
    except urllib.error.HTTPError as e:
        return (time.perf_counter() - t0) * 1000.0, e.code, 0
    except Exception:  # noqa: BLE001
        return (time.perf_counter() - t0) * 1000.0, 0, 0


def _warmup(url: str, n: int, timeout: float) -> None:
    for i in range(n):
        ms, code, size = _one_get(url, timeout)
        print(f"    [warmup] {i + 1}/{n}  {code}  {ms:.1f} ms  {size / 1024:.1f} KB")


def _level(url: str, users: int, seconds: float, timeout: float) -> dict:
    lat: list[float] = []
    codes: dict[int, int] = {}
    nbytes = 0
    lock = threading.Lock()
    stop_at = time.perf_counter() + seconds

    def worker() -> None:
        nonlocal nbytes
        local_lat, local_codes, local_bytes = [], {}, 0
        while time.perf_counter() < stop_at:
            ms, code, size = _one_get(url, timeout)
            local_lat.append(ms)
            local_codes[code] = local_codes.get(code, 0) + 1
            local_bytes += size
        with lock:
            lat.extend(local_lat)
            for k, v in local_codes.items():
                codes[k] = codes.get(k, 0) + v
            nbytes += local_bytes

    t0 = time.perf_counter()
    threads = [threading.Thread(target=worker, daemon=True) for _ in range(users)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - t0

    ok = codes.get(200, 0)
    total = sum(codes.values()) or 1
    lat.sort()
    return {
        "users": users,
        "seconds": round(elapsed, 1),
        "requests": total,
        "ok": ok,
        "rps": total / elapsed,
        "p50": _pct(lat, 50),
        "p95": _pct(lat, 95),
        "p99": _pct(lat, 99),
        "max": lat[-1] if lat else 0.0,
        "err_pct": (total - ok) / total * 100.0,
        "mbps": nbytes / elapsed / (1024 * 1024),
        "codes": codes,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="http://127.0.0.1:8011")
    ap.add_argument("--path", default="/api/real/tenders")
    ap.add_argument("-l", "--levels", default="1,10,50,100")
    ap.add_argument("-t", "--seconds", type=float, default=180, help="每档时长（默认 180s = 3min）")
    ap.add_argument("--warmup", type=int, default=5, help="每档先打几个丢弃请求（默认 5）")
    ap.add_argument("--timeout", type=float, default=60.0)
    args = ap.parse_args()

    url = args.host.rstrip("/") + args.path
    levels = [int(x) for x in args.levels.split(",") if x.strip()]
    print(f"[T3] {url}  档位 {levels}  每档 {args.seconds:.0f}s  预热 {args.warmup}")

    # 先确认服务真的在这儿（端口写错会压出 100% 错误率还以为是代码问题）
    ms, code, size = _one_get(url, args.timeout)
    print(f"     冒烟：status={code} {ms:.1f} ms {size / 1024:.1f} KB")
    if code != 200:
        print("ABORT: 冒烟没过。先确认服务起在 8011（scripts/demo_web.py 里写的是 8001）"
              "且限流已关。")
        return 2

    rows = []
    for u in levels:
        print(f"\n  ---- 并发 {u} ----")
        _warmup(url, args.warmup, args.timeout)
        r = _level(url, u, args.seconds, args.timeout)
        rows.append(r)
        print(f"    {r['requests']} 请求 / {r['seconds']}s  ⇒ {r['rps']:.1f} RPS · "
              f"错误 {r['err_pct']:.1f}% · {r['mbps']:.2f} MB/s")
        print(f"    P50 {r['p50']:.0f} · P95 {r['p95']:.0f} · P99 {r['p99']:.0f} · "
              f"max {r['max']:.0f} ms · codes={r['codes']}")

    print()
    print(f"  {'并发':>4} {'RPS':>7} {'P50':>7} {'P95':>7} {'P99':>7} {'错误%':>6} {'MB/s':>6} {'样本':>6}")
    print("  " + "-" * 56)
    for r in rows:
        print(f"  {r['users']:>4} {r['rps']:>7.1f} {r['p50']:>7.0f} {r['p95']:>7.0f} "
              f"{r['p99']:>7.0f} {r['err_pct']:>6.1f} {r['mbps']:>6.2f} {r['requests']:>6}")

    outdir = ROOT / "_perf" / "out"
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"ladder_{time.strftime('%Y%m%d_%H%M')}.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\n  已写 {out}")

    # T4 判据：把「带宽」和「CPU」分开记，别把带宽瓶颈记成业务瓶颈
    top = max(rows, key=lambda r: r["rps"])
    print(f"  吞吐峰值在并发 {top['users']}（{top['rps']:.1f} RPS，{top['mbps']:.2f} MB/s）"
          f"⇒ 后面档位 RPS 不涨就是越过拐点")
    print(f"  若某档 MB/s 已接近本机回环上限而 RPS 不再涨 ⇒ 瓶颈是带宽/序列化，不是数据库")
    print("  ⚠️ 样本 <100 的档位不要报 P99（那就是最大值）；这也是要每档 3 分钟的原因")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
