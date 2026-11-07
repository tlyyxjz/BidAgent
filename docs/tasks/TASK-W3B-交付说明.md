# 交付说明：TASK-W3B（拼装段改元组解包 + 更正 W3A 的机制说法）

> 按 `Desktop/重要/AI协作执行标准_v1.2.md` 第六章模板。任务单见 `docs/tasks/TASK-W3B-元组解包拼装.md`。
> 分支 `feature/wb-W3B-tuple-unpack`，从 `41da76b`（W3A 交付 HEAD）切出。

## 完成内容

两件事，一大一小：

1. **拼装段由 `Row` 属性访问改为元组解包**（`list_tenders` 唯一的循环）
   - 改前：`for r in result.all(): ... r.id / r.project_name / r.notice_type / r.created_at / r.source_platform / r.field_name / r.raw_value`（每行 7 次 `Row._mapping` 查表）
   - 改后：`for tid, pn, nt, cat, plat, fn, rv in result.all():`（每行一次解包）
2. **更正 W3A 的机制说法**：`real_demo.py` 函数注释与 W3A 交付说明「已知限制 #3」
   里写的「两次往返约 9.4ms / 跨线程约 5ms 级」是**错的推断**，实测跨线程只有 0.30ms。
   W3A 的收益来自省掉第二段查询自身的 DB 工作量，不是省掉线程跳。

## 修改文件

| 文件 | 改动 |
|---|---|
| `app/api/real_demo.py` | `list_tenders` 拼装循环 + docstring 机制说明更正 |
| `tests/test_real_demo_single_query_equiv.py` | `_new_assembly` 同步改为元组解包（保证测的是线上真实写法） |
| `docs/tasks/TASK-W3B-元组解包拼装.md` | 任务单 |
| `docs/tasks/TASK-W3A-交付说明.md` | 「已知限制 #3」更正为实测值，新增第 5 条 |
| `_perf/thread_hop_probe.py` | 新增（T5 跨线程成本探针） |
| `_perf/assembly_probe.py` | 新增（T6 拼装写法对比探针） |
| `docs/tasks/TASK-W3B-交付说明.md` | 本文件 |

**未触碰**：SQL 部分（`select(...)` 列顺序 / `outerjoin` 条件 / `order_by`）一行没动；`_ok` / `_err`；
其它端点；模型；迁移；中间件；驱动。

## 新增测试

没有新增用例，`tests/test_real_demo_single_query_equiv.py` 仍是 3 条。

理由：这次改的是**消费方式**，不是口径。3 条既有用例（真实库 829 条等价 / 空库 / 空值回落）
已经覆盖「输出必须一模一样」这一唯一契约。**而且它们同时是解包错位的守卫** ——
`select(...)` 列顺序与解包顺序错位会立刻抛 `ValueError`，测试直接红。

## 测试结果

### 等价性（真实库 829 条）

```
$ pytest tests/test_real_demo_single_query_equiv.py -q --no-cov
3 passed in 1.43s
```

### 冒烟测试（真服务 + 真库 + 逐字节比对）

8011 = 改前代码（进程启动时已加载旧实现，全程未重启）；
8013 = W3A+W3B 代码；两者打同一份 `data/bidagent.db`。

```
port 8011 http=200 bytes=191064
port 8013 http=200 bytes=191064
b76fd96c409ce023d3bf73b0a760a4ddd21010c4aafe99a3e300085b328ac59d  resp_8011.json
b76fd96c409ce023d3bf73b0a760a4ddd21010c4aafe99a3e300085b328ac59d  resp_8013.json
IDENTICAL
count 829 829
keys same: True ['id','name','type','amount','purchaser','publish_date','platform']
```

与 W3A 基线 sha256 完全相同 ⇒ 连续两次改动后响应仍逐字节不变。

### 性能 A/B（同轮交替，各 3 轮 × 15s，全档 0 错误）

| 并发 | 端口 | 三轮 RPS | 均值 | 区间 |
|---|---|---|---|---|
| 1 | 8011（改前） | 30.3 / 29.1 / 24.9 | 28.10 | 24.9 – 30.3 |
| 1 | 8013（W3A+W3B） | 37.3 / 35.6 / 36.0 | **36.30** | 35.6 – 37.3 |
| 10 | 8011（改前） | 20.8 / 30.8 / 26.3 | 25.97 | 20.8 – 30.8 |
| 10 | 8013（W3A+W3B） | 34.8 / 35.0 / 35.4 | **35.07** | 34.8 – 35.4 |

延迟（同一轮采样）：

| 并发 | 改前 P50 / P95 | 改后 P50 / P95 |
|---|---|---|
| 1 | 32–42 / 45–53 ms | **30–31 / 36–37 ms** |
| 10 | 315–489 / 413–650 ms | **279–283 / 344–349 ms** |

### 结论强度（按标准 24.3 的 σ 规则）

- **「W3A + W3B 合计相对改前」：已证实**
  - 并发 1：+29.2%，区间完全不重叠（改前最大 30.3 < 改后最小 35.6）
  - 并发 10：+35.0%，区间完全不重叠（改前最大 30.8 < 改后最小 34.8）
- **「W3B 单独贡献」：仅倾向**
  - W3A 当时并发 1 实测 32.87 → 本次 36.30，**+10.4%**；
    探针预测 2.27ms / 19.62ms = **11.6%**，两者一致。
  - 但 32.87 是**今天早些时候、另一个进程（8012）**测的，不是同轮交替 ⇒
    跨时间比较，按 24.3 只能标「倾向」。

### 回归测试

API/Demo 相关全部 20 个文件：

```
259 passed in 152.61s (0:02:32)
```

与 W3A 的 259 一致（本次未新增用例）。

全量：

```
（见下方「全量回归」）
```

## 探针数据（本次新增的两个脚本，可复现）

`_perf/thread_hop_probe.py`（同一段 SQL 只换执行通道，两通道各复用同一条连接，n=150）：

```
  SQL       通道             mean      p50      p95
  SELECT 1  async         0.31     0.26     0.51
  SELECT 1  sync          0.00     0.00     0.00
  LEFT JOIN async         7.63     7.74    10.87
  LEFT JOIN sync          6.54     6.52     7.84
  ⇒ 跨线程固定税 hop ≈ 0.30 ms，占 LEFT JOIN 总耗时 4.0%
```

`_perf/assembly_probe.py`（1042 行，n=40）：

```
  写法           min     med     p95  (ms)
  attr        3.59    3.67    3.78
  tuple       1.34    1.40    1.57
  两份输出 json 全等（含键序）：True  条数 829
  元组解包相对属性访问：+61.9%
```

## 接口影响

**无变化**，有逐字节比对背书（sha256 `b76fd96c409ce023…`，与 W3A 基线相同）。

## 数据迁移

无。

## 已知限制

1. **解包顺序与 `select(...)` 列顺序强耦合**。将来往 select 里加列必须同步改解包，
   否则该端点直接 500（`ValueError: too many values to unpack`）。
   这是有意的取舍：换来每次请求约 2.3ms。守卫是 3 条等价测试。
2. **W3B 单独贡献只是倾向**（跨时间比较），对外只能说「W3A+W3B 合计比改前快 29%（并发 1）」。
3. **残余约 3.6ms 未定位**：残差 12.47ms − 框架 3.76ms − 拼装 3.67ms ≈ 3.6ms，
   分散在 `@app.middleware("http")`（BaseHTTPMiddleware，每请求起一个任务）+ SlowAPI 中间件上。
   非单点，动中间件的风险远大于收益，本轮明确不做。
4. **改前那 28.10 RPS 的三轮波动很大**（24.9–30.3），说明本机负载本身不稳；
   所以这里的结论只用「区间是否重叠」判，不用均值差硬算倍数。
5. **未在 CI 上跑过**（分支未推），仅本机 Windows + Python 3.13 验证。

## 验收方法

```bash
# 1 等价性
CODEBUDDY_SAFE_DELETE_ENABLED=0 python -m pytest tests/test_real_demo_single_query_equiv.py -q --no-cov

# 2 跨线程成本（应看到 hop ≈ 0.3ms，而不是 5ms）
CODEBUDDY_SAFE_DELETE_ENABLED=0 python _perf/thread_hop_probe.py -n 150

# 3 拼装写法对比（应看到 tuple 比 attr 快 50%+ 且 json 全等）
CODEBUDDY_SAFE_DELETE_ENABLED=0 python _perf/assembly_probe.py -n 40

# 4 响应比对（旧码在 8011，新码在 8013）
curl.exe -s http://127.0.0.1:8011/api/real/tenders | sha256sum
curl.exe -s http://127.0.0.1:8013/api/real/tenders | sha256sum   # 两值必须相同

# 5 A/B
python _perf/ladder.py --host http://127.0.0.1:8011 -l 1,10 -t 15
python _perf/ladder.py --host http://127.0.0.1:8013 -l 1,10 -t 15
```

## Git 信息

- 分支：`feature/wb-W3B-tuple-unpack`（从 `41da76b` 切出，**未合并、未推**）
- 提交：`perf(real_demo): 拼装段改元组解包，并把跨线程成本更正为实测 0.30ms`
