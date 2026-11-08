#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""T1 · 压测期间的 CPU 核分布采样（判 H1：进程模型 vs IO 等待）。

为什么不用 `uvicorn --workers 4` 重压（文档 §3 修正二）：
  本机是 Windows，uvicorn 多进程依赖 fork，跑不起来；而且 --workers 只回答
  「加进程有没有用」，不回答「为什么只有一个核在干活」。看 CPU 分布两个问题一起答。

用法（**另开一个窗口**跑，不要和 locust 抢同一个终端）：
    python _perf/sample_cpu.py -s 60            # 压测进行中采 60 秒
    python _perf/sample_cpu.py -s 60 --pid 1234 # 顺带盯住服务进程自身的 CPU

判据（文档 §2 T1）：
    单核 ≥85% 且其余核 ≈0   -> H1 成立：被一个核卡住，加进程立刻见效
    全部核都 <40%           -> H1 排除：CPU 在等 IO（DB/文件/回环）
    多核各 20–40% 且都不满   -> 线程在跑但被 GIL/锁挡住，转 H3
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time

import psutil


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-s", "--seconds", type=int, default=60, help="采样总时长（默认 60s）")
    ap.add_argument("-i", "--interval", type=float, default=1.0, help="采样间隔（默认 1s）")
    ap.add_argument("--pid", type=int, default=0, help="可选：同时记录该进程的 CPU 占用")
    args = ap.parse_args()

    n_cores = psutil.cpu_count(logical=True)
    print(f"[T1] {n_cores} 个逻辑核 · 每 {args.interval}s 采一次 · 共 {args.seconds}s")
    print("     现在去另一个窗口跑压测；本脚本只读计数器，不干扰被测进程。")

    proc = None
    if args.pid:
        try:
            proc = psutil.Process(args.pid)
            proc.cpu_percent(None)  # 第一次调用是基线，必须丢弃
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 取不到 pid={args.pid} 的进程：{e}；只采系统级")
            proc = None

    samples: list[list[float]] = []
    proc_samples: list[float] = []
    psutil.cpu_percent(percpu=True)  # 基线，丢弃

    t_end = time.time() + args.seconds
    while time.time() < t_end:
        time.sleep(args.interval)
        samples.append(psutil.cpu_percent(percpu=True))
        if proc is not None:
            proc_samples.append(proc.cpu_percent(None))

    if not samples:
        print("ABORT: 没采到样本（seconds 太小？）")
        return 2

    per_core = [statistics.mean(s[i] for s in samples) for i in range(n_cores)]
    total_avg = statistics.mean(per_core)
    hottest = max(range(n_cores), key=lambda i: per_core[i])

    print()
    print("  每核平均占用：")
    bar_unit = "█"
    for i, v in enumerate(per_core):
        bar = bar_unit * int(round(v / 5.0))
        mark = "  <== 最热" if i == hottest else ""
        print(f"    core{i:>2} {v:>6.1f}%  {bar}{mark}")
    print()
    print(f"  全核平均 {total_avg:.1f}% · 最热核 core{hottest} {per_core[hottest]:.1f}% · "
          f"样本 {len(samples)} 个")
    if proc is not None and proc_samples:
        print(f"  进程 {args.pid} 平均 {statistics.mean(proc_samples):.1f}% "
              f"（psutil 口径：单核满载 = 100%，多核可 >100%）")

    # ---- 判据 ----
    others = [v for i, v in enumerate(per_core) if i != hottest]
    print()
    if per_core[hottest] >= 85.0 and (statistics.mean(others) if others else 0.0) < 15.0:
        print(f"  判据：单核 {per_core[hottest]:.0f}% 打满、其余均值 "
              f"{(statistics.mean(others) if others else 0):.0f}% ⇒ **H1 成立**：进程模型限制，"
              f"加进程有效")
    elif max(per_core) < 40.0:
        print(f"  判据：全部核 <40%（最高 {per_core[hottest]:.0f}%）⇒ **H1 排除**：CPU 在等 IO，"
              f"加进程没用，去查 DB/文件/回环")
    else:
        print(f"  判据：多核在动但都不满（最高 {per_core[hottest]:.0f}%，全核均 {total_avg:.0f}%）"
              f"⇒ 线程在跑但被 GIL/锁挡住，转向 **H3**")
    print("     备注：本机还有别的进程（浏览器/编辑器）会抬高全核均值 —— 采样本时尽量别开重活，"
          "并结合 --pid 的进程数一起看。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
