# -*- coding: utf-8 -*-
"""BidAgent 预压探测：只读 /api/real/tenders，小规模阶梯，拿初步 P50/P95。
不写库、不调 LLM。任何一档错误率 >5% 立即停止。"""
import time, json, statistics, urllib.request, threading

BASE = "http://127.0.0.1:8011"
PATH = "/api/real/tenders"
TOTAL = 20          # 每档总请求数
CONCS = [1, 5, 10]  # 并发档位

def one(lat_out, err_out):
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(BASE + PATH, headers={"User-Agent": "bidagent-probe/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
        lat_out.append(time.perf_counter() - t0)
        return len(body)
    except Exception as e:
        err_out.append(str(e))
        return 0

def pct(v, p):
    if not v: return float("nan")
    k = (len(v) - 1) * p
    f = int(k)
    c = min(f + 1, len(v) - 1)
    return v[f] + (v[c] - v[f]) * (k - f)

print("目标 %s%s  每档 %d 请求" % (BASE, PATH, TOTAL))
print("%-6s %-8s %-9s %-9s %-9s %-8s %-7s %s" % ("并发", "RPS", "P50(ms)", "P95(ms)", "P99(ms)", "均KB", "错误", "备注"))
results = []
for c in CONCS:
    lat, err, sizes = [], [], []
    lock = threading.Lock()
    per = max(1, TOTAL // c)

    def worker():
        for _ in range(per):
            n = one(lat, err)
            if n: sizes.append(n)

    t0 = time.perf_counter()
    ths = [threading.Thread(target=worker) for _ in range(c)]
    [t.start() for t in ths]; [t.join() for t in ths]
    dur = time.perf_counter() - t0

    lat.sort()
    errrate = len(err) / max(1, len(lat) + len(err))
    rps = len(lat) / dur
    line = "%-6d %-8.1f %-9.1f %-9.1f %-9.1f %-8.0f %-7.1f%%" % (
        c, rps, pct(lat, .5) * 1000, pct(lat, .95) * 1000, pct(lat, .99) * 1000,
        (statistics.mean(sizes) / 1024 if sizes else 0), errrate * 100)
    print(line, ("" if not err else str(err[0])[:40]))
    results.append(dict(conc=c, rps=round(rps, 1), p50=round(pct(lat, .5) * 1000, 1),
                        p95=round(pct(lat, .95) * 1000, 1), p99=round(pct(lat, .99) * 1000, 1),
                        kb=round(statistics.mean(sizes) / 1024) if sizes else 0,
                        err=round(errrate * 100, 1), n=len(lat)))
    if errrate > 0.05:
        print("  错误率超过 5%%，停止升档")
        break
    time.sleep(1)

json.dump(results, open("_probe_baseline.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("\nsaved _probe_baseline.json")
