# BidAgent 压测：怎么跑、怎么读

> 目的不是「证明系统快」，是**能答出「瓶颈在哪一行、为什么、修完涨了多少」**。
> 三条验收：① 别人拿到能复现 ② 有一条延迟 vs 并发曲线 ③ 能指到具体代码行。

---

## 0 先关掉限流（否则压出来的是假曲线）

免费套餐 **5 次/天**，按 `SHA256(api_key)` 或 IP 计。不关，第 6 个请求开始全 429，曲线会是「一条断崖」。

**不用改代码**，改环境变量即可（`app/config.py:49` 读的是 settings）：

```bash
# Linux / macOS（Git Bash 也算这个）
export FREE_TIER_DAILY_LIMIT=1000000
```
```powershell
# Windows PowerShell —— 本机是 win32，`export` 在这里无效（2026-11-06 实测踩过）
$env:FREE_TIER_DAILY_LIMIT="1000000"
```
```cmd
:: Windows CMD
set FREE_TIER_DAILY_LIMIT=1000000
```

⚠️ 环境变量只在**当前窗口**有效 —— 启动服务必须用同一个窗口。
⚠️ 改完必须**重启服务**，`settings` 是启动时读的。
⚠️ 压测完记得改回来 —— 限流本身是产品特性，别让它跟着提交进仓库。

---

## 1 可压的接口（都是只读、零 LLM 成本）

| 接口 | 响应体 | 单发延迟 | 说明 |
|---|---|---|---|
| `GET /api/real/tenders` | **186.6 KB** | 37 ms | 829 条全量 + 360 行字段拼装（三列；全表 582 行），**无分页** ← 主战场 |
| `GET /api/real/tenders/{id}/detail` | 5.8 KB | 18 ms | 点查，作对照 |

⛔ **不要压 `POST /extract/tasks` / `POST /due-diligence` / `/pipeline/start`**：
- 会调 LLM（烧钱）
- 会写 SQLite（污染基线 + 污染数据）
- `api_keys` 表现在 0 条，鉴权接口本来就是 401，压出来全是错误

⛔ 路径别搞错：`real_demo_router` 的 prefix 是 **`/api/real`**，不是 `/api/demo`；
`/api/tenders` 走的是另一个挂了 API key 鉴权的 router，会 401。

---

## 2 跑

**快速探测**（无依赖，20 秒出数）：
```bash
python _perf/probe_baseline.py
```

### 2.1 W1 五个测试（2026-11-06 补齐脚本，全部已本机跑通）

| # | 命令 | 出什么数 |
|---|---|---|
| **T0** 单请求分解 | `python _perf/decompose_request.py -n 20 --warmup 5` | 四段 ms + 残差占比；判 H2 / H3 / **H4** |
| | 带真 HTTP 对照：`python _perf/decompose_request.py --url http://127.0.0.1:8011` | inproc 与真 HTTP 之差 = uvicorn+网络栈 |
| **T1** CPU 核分布 | 另开窗口：`python _perf/sample_cpu.py -s 60 --pid <服务进程pid>` | 每核平均占用 + 最热核；判 H1 |
| **T2** 预热隔离 | 已内置进 `locustfile.py`（`WARMUP=5`，`BIDAGENT_WARMUP=0` 可关） | 冷启动样本不进统计 |
| **T3** 12 分钟阶梯 | `python _perf/ladder.py -l 1,10,50,100 -t 180`（零依赖，**首选**） | 1→10→50→100 的完整曲线，每档 ≈5000 样本 |
| | 或用 locust（需自己装，见下）：`locust -f _perf/locustfile.py --headless -u 100 -r 10 -t 12m ...` | 同上，locust 口径更标准 |
| **T4** 带宽记账 | 上面两条命令都会打 **MB/s**（ladder 直接给，locust 看 CSV 的 `Total Average Content Size` × RPS） | MB/s；区分「被 CPU 卡」还是「被回环卡」 |
| **T5** 跨线程成本 | `python _perf/thread_hop_probe.py -n 150` | 同一 SQL 换执行通道（async vs sync）之差 = 跨线程固定税，**实测 0.30ms** |
| **T6** 拼装写法 | `python _perf/assembly_probe.py -n 40` | `Row` 属性访问 vs 元组解包（3.67 → 1.40 ms），输出 json 须全等 |
| **T7** 工具开销 / 预热 / 同轮交替 | `python _perf/harness_overhead.py --host http://127.0.0.1:8016 -l 10 -T 30 -r 5` | 客户端 CPU 核（判「压测工具是不是瓶颈」）、服务端 CPU 核、线程数、RSS；连续多轮判预热 |

🔴 **T7 的核心结论（2026-11-07 实测，直接影响怎么报数）**：
- 客户端只吃 **0.09~0.10 核** ⇒ 压测工具不是瓶颈，别再拿「GIL 卡住并发」解释增速慢
- 服务端**没有预热爬升**（首轮即高点）⇒ 「进程刚起所以慢」不成立
- **同一份代码并发 10 的绝对 RPS，跨次可差 48%**（35.07 / 33.27 / 39.00 / 47.7 四次实测）
  ⇒ **绝对 RPS 一律不引用**；只认同轮交替 + 顺序翻转下的**相对差**，以及**每请求 CPU** 这种比值指标

⚠️ **为什么 T3 首选自写的 `ladder.py`**：2026-11-06 实测本机 pip 默认源（清华镜像）对 `/simple/` 返回 **403**，
换 `-i https://pypi.org/simple` 能通但极慢（4 分钟只下完元数据），locust 没装成。
`ladder.py` 只用标准库，已对着 8011 上的真实服务跑通（1/2 并发各 8s：28.4 / 43.8 RPS，0 错误）。
口径差别写在脚本头部：闭环压测，P99 偏乐观，别和 locust 的绝对值混比。

⚠️ **T0 必须早于 T1**：`--workers 4` 的结果有歧义（不涨可能是 H1 不成立，也可能是 4 进程抢同一把 SQLite 锁），
而 T0 的分解不受进程模型影响，先拿到占比。详见 `求职雷达/压测W1_跑什么与为什么_20261106.md` §3。

**正式阶梯**：
```bash
mkdir -p _perf/out
locust -f _perf/locustfile.py --headless -u 100 -r 10 -t 12m \
       --host http://127.0.0.1:8011 --csv _perf/out/run_$(date +%Y%m%d_%H%M)
```

⚠️ 本机没把 locust 装进项目 Python（**也没有全局装**）。已备好隔离环境：
```bash
C:/Users/Lenovo/.workbuddy/binaries/python/envs/locust/Scripts/locust.exe \
  -f _perf/locustfile.py --headless -u 100 -r 10 -t 12m \
  --host http://127.0.0.1:8011 --csv _perf/out/run_20261107
```

⚠️ 服务实际端口是 **8011**，但 `scripts/demo_web.py:177` 里写的是 8001。压测前先确认：
```bash
curl.exe -o NUL -s -w "%{http_code} %{size_download}\n" http://127.0.0.1:8011/api/real/tenders
# 期望 200 191064（= 186.6 KB）；拿到 000 就是服务没起在 8011
```

每次压测前**重启服务**，否则连接池/进程内缓存会带着上一轮的状态。

---

## 3 首轮基线（2026-11-04 19:0x，单 worker，20 请求/档）

| 并发 | RPS | P50 | P95 | P99 | 错误率 |
|---|---|---|---|---|---|
| 1 | 26.3 | 36.9 ms | 52.9 ms | 68.1 ms | 0% |
| 5 | **32.6** | 149.7 ms | 184.8 ms | 199.8 ms | 0% |
| 10 | 21.6 | **443.5 ms** | 555.7 ms | 571.8 ms | 0% |

### 读数：系统在并发 5~10 之间已经越过拐点

> 🔴 **2026-11-07 补测 T3 后修正本节结论（原文保留在下方）**
> 当时只测了 1 / 5 / 10，**没测 2 和 3**，因此把峰值误认在 5。补测后实测：
>
> | 并发 | 1 | 2 | **3** | 5 | 10 |
> |---|---|---|---|---|---|
> | RPS | 27.1 | 40.6 | **42.4（峰值）** | 31.2 | 19.2 |
>
> ① **拐点在并发 3**，并发 5 已经在下降沿上。
> ② 下面第 3 条「1 并发说明完全没有并行」**是错的**：1→2 吞吐涨 50%，
> 说明服务端能并行吃 2~3 个请求。并发 1 的 RPS 只是 `1/P50`，是**单用户视角，不是容量**。
> ⇒ 完整阶梯与新旧对比见 `求职雷达/压测W1_跑什么与为什么_20261106.md` §7.10。

1. **延迟随并发线性涨，且略超线性**：P50 从 36.9 → 443.5 ms（**12.0 倍**），并发只涨 10 倍。超出的 2 倍是排队与上下文切换。
2. **吞吐在并发 5 达峰后反而下降**：32.6 → 21.6 RPS（**-34%**）。**加并发不再换吞吐，只换延迟** —— 这就是拐点。
3. 1 并发 26.3 RPS ≈ 1/38ms，说明**完全没有并行**：一条请求从头占到尾。

### 由此推出的三个瓶颈假设（待 W3 验证，不要现在下结论）

| # | 假设 | 怎么验 | 预期证据 |
|---|---|---|---|
| H1 | **单 worker 只吃一个核**（`uvicorn` 没带 `--workers`） | 压测时采进程 CPU，看是否顶到 ~100% 单核 | CPU ≈ 100% 而 RPS 不涨 |
| H2 | **`/api/real/tenders` 的 Python 拼装是 CPU 热点**（829 次循环 + `field_map` 字典 + 187KB JSON 序列化） | 用 `py-spy dump --pid <pid>` 在压测中采样栈 | 栈顶落在 `json`/`orjson` 序列化或该函数循环里 |
| H3 | **aiosqlite 线程池串行**（SQLite 默认单写多读，但 aiosqlite 用固定线程池跑每条语句） | 把 `get_db` 换成直连并计时，或看线程数 | DB 时间占比高但 CPU 不高 |

⚠️ **H1 和 H2 会互相伪装**：单核打满时，代码再快也是线性排队。**必须先排除 H1**（试 `--workers 4` 看 RPS 是否近 4 倍），再谈 H2/H3。

---

## 4 已知的坑（踩过，别重踩）

| 坑 | 后果 | 处理 |
|---|---|---|
| `FREE_TIER_DAILY_LIMIT=5` | 第 6 个请求起 429，曲线是假的 | 先调环境变量并重启 |
| `_EXTRACT_TASKS` 惰性清理（`_TASK_TTL=3600`，只在**创建新任务时**触发） | 压 POST 接口时字典随请求增长；压 GET 不受影响 | 压读接口无此问题；若真压 POST，同时采 `len(_EXTRACT_TASKS)` |
| 响应体 186.6 KB × 32 RPS ≈ **6.1 MB/s** | 高并发时会先打满本机回环/序列化，而不是数据库 | 报告里必须把「带宽」和「CPU」分开，别把带宽瓶颈记成业务瓶颈 |
| 首次请求 111 ms vs 稳定态 37 ms | 冷启动样本会污染 P99 | 每档先预热 5 个请求，再开始计时 |
