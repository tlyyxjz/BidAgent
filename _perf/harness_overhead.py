#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""T7 · 压测工具自身开销 + 服务端预热效应。

为什么要这个脚本（2026-11-07）：
    §7.8 的受控实验里出现一个没解释的现象 —— 同一份「改前代码」，
    跑了三天的进程在并发 10 上有 25.97 RPS，冷启动的只有 18.07，差 44%。
    方向明确（老进程更快），原因没查清。而它直接决定了「该拿哪个数当基线」。

    本机 32 逻辑核 / 16 物理核 ⇒「客户端和服务端抢核」这个解释不成立
    （服务端只用到约 3 核）。剩下两个候选解释，本脚本一次量掉：

    Q1 客户端自己吃掉多少 CPU？
       ladder.py 的 10 个 worker 都是 Python 线程，受同一把 GIL 串行化。
       若客户端 CPU 时间≈墙钟（即占满 1 核），则它很可能就是并发档位的天花板，
       此时「服务端 RPS 掉了」其实是「客户端发不动了」。
    Q2 服务端是「进程冷」还是「代码慢」？
       同一进程内连续跑 r 轮，不重启。若第 1 轮明显低于第 r 轮并收敛，
       说明存在预热效应（线程池 / 页缓存 / 解释器 specializing），
       那 §7.8 的冷进程基线本身就低估了旧代码，对照不公平。

输出口径（写报告必须照这句）：
    client_cores / server_cores = 该轮内 进程 CPU 秒 ÷ 墙钟秒，1.0 = 吃满一颗逻辑核。
    ⚠️ 这是**进程级** CPU（含所有线程），不是单核占用率；Windows 上 >1.0 正常。

⚠️ 已知局限：本脚本仍是闭环（closed-loop）压测，同 ladder.py，
   不能拿它的绝对值跟 locust 混比，只能用于「同脚本内横向对照」。

用法：
    python _perf/harness_overhead.py --host http://127.0.0.1:8016 -l 10 -T 30 -r 5
    python _perf/harness_overhead.py --host http://127.0.0.1:8016 -l 1,10 -T 20 -r 3 --prewarm 60
"""
from __future__ import annotations

import argparse
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    import psutil
except ImportError:
    print("需要 psutil：pip install psutil")
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parent.parent


def find_pid_by_port(port: int) -> int | None:
    """按监听端口反查 PID。Windows 上 psutil.net_connections 可能需要管理员权限，
    失败时回退到 Get-NetTCPConnection（由调用方提示用户传 --pid）。"""
    try:
        for c in psutil.net_connections(kind="inet"):
            if not c.laddr:
                continue
            if c.laddr.port == port and c.status == psutil.CONN_LISTEN and c.pid:
                return c.pid
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] net_connections 失败：{e}")
    return None


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


class ServerSampler:
    """后台每 0.4s 采一次服务端进程指标。"""

    def __init__(self, proc: psutil.Process) -> None:
        self.proc = proc
        self.samples: list[dict] = []
        self._stop = threading.Event()
        self._th = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                ct = self.proc.cpu_times()
                m = self.proc.memory_info()
                self.samples.append({
                    "t": time.perf_counter(),
                    "cpu": ct.user + ct.system,
                    "threads": self.proc.num_threads(),
                    "rss_mb": m.rss / 1048576,
                })
            except psutil.Error:
                break
            self._stop.wait(0.4)

    def start(self) -> None:
        self._th.start()

    def stop(self) -> dict:
        self._stop.set()
        self._th.join(timeout=2)
        if len(self.samples) < 2:
            return {}
        s = self.samples
        cpu0, cpu1 = s[0]["cpu"], s[-1]["cpu"]
        wall = s[-1]["t"] - s[0]["t"]
        return {
            "server_cores": (cpu1 - cpu0) / wall if wall > 0 else 0.0,
            "threads_max": max(x["threads"] for x in s),
            "threads_first": s[0]["threads"],
            "rss_first": s[0]["rss_mb"],
            "rss_max": max(x["rss_mb"] for x in s),
        }


def run_round(url: str, users: int, seconds: float, timeout: float) -> dict:
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

    c0 = time.process_time()
    t0 = time.perf_counter()
    threads = [threading.Thread(target=worker, daemon=True) for _ in range(users)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - t0
    client_cpu = time.process_time() - c0

    ok = codes.get(200, 0)
    total = sum(codes.values()) or 1
    lat.sort()
    pct = lambda q: lat[min(max(1, int(round(q / 100 * len(lat)))), len(lat)) - 1] if lat else 0.0  # noqa: E731
    return {
        "users": users,
        "requests": total,
        "rps": total / elapsed,
        "p50": pct(50),
        "p95": pct(95),
        "p99": pct(99),
        "err_pct": (total - ok) / total * 100.0,
        "client_cores": client_cpu / elapsed,
        "elapsed": elapsed,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="http://127.0.0.1:8016")
    ap.add_argument("--path", default="/api/real/tenders")
    ap.add_argument("-l", "--levels", default="10")
    ap.add_argument("-T", "--seconds", type=float, default=30.0, help="每轮时长")
    ap.add_argument("-r", "--rounds", type=int, default=5, help="同进程连续轮数")
    ap.add_argument("--prewarm", type=float, default=0.0, help="正式量之前先打多久预热（秒）")
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--pid", type=int, default=0, help="服务端 PID（自动探测失败时手填）")
    args = ap.parse_args()

    url = args.host.rstrip("/") + args.path
    levels = [int(x) for x in args.levels.split(",") if x.strip()]

    ms, code, size = _one_get(url, args.timeout)
    print(f"冒烟：status={code} {ms:.1f} ms {size / 1024:.1f} KB")
    if code != 200:
        print("ABORT: 冒烟没过")
        return 2

    pid = args.pid or find_pid_by_port(int(args.host.rsplit(":", 1)[-1]))
    if not pid:
        print("ABORT: 没能自动找到服务端 PID，请用 --pid 指定")
        return 2
    proc = psutil.Process(pid)
    print(f"服务端 PID={pid}  cmd={' '.join(proc.cmdline()[-2:])}")

    if args.prewarm > 0:
        print(f"\n[预热] 先打 {args.prewarm:.0f}s 再开始计时（模拟「进程已经跑热」）")
        r = run_round(url, max(levels), args.prewarm, args.timeout)
        print(f"   预热结果 → {r['rps']:.1f} RPS，client_cores={r['client_cores']:.2f}")

    print(f"\n{'轮':>3} {'并发':>4} {'RPS':>7} {'P50':>7} {'P95':>7} "
          f"{'err%':>5} {'cli核':>6} {'srv核':>6} {'线程':>5} {'RSS MB':>7}")
    print("-" * 74)
    results: list[dict] = []
    for u in levels:
        for i in range(args.rounds):
            sampler = ServerSampler(proc)
            sampler.start()
            r = run_round(url, u, args.seconds, args.timeout)
            s = sampler.stop()
            r.update(s)
            results.append(r)
            print(f"{i + 1:>3} {u:>4} {r['rps']:>7.1f} {r['p50']:>7.0f} {r['p95']:>7.0f} "
                  f"{r['err_pct']:>5.1f} {r['client_cores']:>6.2f} "
                  f"{s.get('server_cores', 0):>6.2f} {s.get('threads_max', 0):>5} "
                  f"{s.get('rss_max', 0):>7.1f}")

    print("\n==== 判据 ====")
    for u in levels:
        rs = [r for r in results if r["users"] == u]
        if not rs:
            continue
        rpss = [r["rps"] for r in rs]
        first, last = rpss[0], rpss[-1]
        tail = rpss[1:] if len(rpss) > 1 else rpss
        print(f"[并发 {u}] RPS 首轮 {first:.1f} → 末轮 {last:.1f} "
              f"（{(last / first - 1) * 100:+.1f}%）；后 {len(tail)} 轮均值 "
              f"{statistics.mean(tail):.1f} ± {statistics.pstdev(tail):.1f}")
        print(f"         客户端 {statistics.mean([r['client_cores'] for r in rs]):.2f} 核 / "
              f"服务端 {statistics.mean([r.get('server_cores', 0) for r in rs]):.2f} 核")
        if last / first - 1 > 0.10:
            print("         ⇒ 存在明显预热效应，首轮不可当基线")
        if statistics.mean([r["client_cores"] for r in rs]) > 0.85:
            print("         ⇒ 🔴 客户端已接近占满 1 核（GIL），并发档位可能被压测工具本身卡住")
    print("\n⚠️ 闭环压测，绝对值不可跨工具比较；只看本脚本内的横向差。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
