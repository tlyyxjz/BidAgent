# 交付说明：TASK-W3A（招投标列表接口：两条查询合并为一条 LEFT JOIN）

> 按 `Desktop/重要/AI协作执行标准_v1.2.md` 第六章模板。任务单见 `docs/tasks/TASK-W3A-合并列表查询.md`。

## 完成内容

`GET /api/real/tenders` 的取数从「两段查询 + Python 合并」改为**一条 LEFT JOIN + 一次分组**：

- 改前：`select(Tender…)` 一次往返 → `select(ExtractedField where tender_id in (829 ids))` 第二次往返 → 内存合并
- 改后：`select(Tender…, ExtractedField.field_name, ExtractedField.raw_value).outerjoin(…)…order_by(Tender.id, ExtractedField.id)` 一次往返

为什么改这条：压测 W1 实测两次 `await db.execute` 含 aiosqlite 跨线程开销约 9.4ms（单请求 23.4ms 的 40%），
且 10 并发时进程烧到 306% CPU、每请求 121ms CPU，判定瓶颈是**同进程内跨线程往返**而不是算力。

## 修改文件

| 文件 | 改动 |
|---|---|
| `app/api/real_demo.py` | `list_tenders` 函数体（仅此函数）+ 顶部 import 加 `and_` |
| `tests/test_real_demo_single_query_equiv.py` | 新增（3 个用例） |
| `docs/tasks/TASK-W3A-合并列表查询.md` | 任务单 |
| `docs/tasks/TASK-W3A-交付说明.md` | 本文件 |

`git diff --stat`（业务代码部分）：

```
 app/api/real_demo.py | 88 ++++++++++++++++++++++++++++++++--------------------
```

未触碰：`_ok`/`_err`、其它端点、`app/models/*` Schema、数据库迁移、`tests/fixtures/gold/*`。

## 新增测试

`tests/test_real_demo_single_query_equiv.py`（3 个）

1. `test_equivalence_on_real_db`：真实库 829 条，旧双查询实现 vs 新单 JOIN 实现，
   **整段 JSON 字符串相等**（含键序）+ 条数必须都是 829
2. `test_empty_db_still_returns_list_empty`：空库返回 `[]`，不是 `null`
3. `test_null_semantics_and_fallback`：无字段公告三字段为空串；`publish_date` 回落 `created_at`；
   同名字段「空 raw_value 不占位、取后一条非空」；`source_platform` 为 NULL 时输出 `""`；键序固定

## 测试结果

### 单元/等价测试

```
$ pytest tests/test_real_demo_single_query_equiv.py -q --no-cov
3 passed in 1.39s
```

### 冒烟测试（真实数据 + 真服务，不是 fixture）

**响应等价（最强证据）**：8011＝改前代码（进程启动时已加载旧实现），8012＝改后代码，同一份 `data/bidagent.db`。

```
old sha b76fd96c409ce023 191064      (8011)
new sha b76fd96c409ce023 191064      (8012)
IDENTICAL
```

829 条、191064 字节，**逐字节相同**。

**性能 A/B（同轮交替，各 3 轮 × 15s，0 错误）**：

| 并发 | 端口 | 三轮 RPS | 均值 | 标准差 |
|---|---|---|---|---|
| 1 | 8011（旧） | 27.6 / 27.3 / 26.9 | 27.27 | 0.35 |
| 1 | 8012（新） | 33.1 / 30.7 / 34.8 | **32.87** | 2.06 |
| 10 | 8011（旧） | 29.4 / 29.2 / 25.7 | 28.10 | 2.08 |
| 10 | 8012（新） | 31.4 / 31.7 / 29.4 | **30.83** | 1.25 |

延迟（单轮采样，仅作参照）：

| 并发 | 旧 P50 / P95 | 新 P50 / P95 |
|---|---|---|
| 1 | 40 / 50 ms | **30 / 41 ms** |
| 10 | 332 / 431 ms | **315 / 381 ms** |

**结论强度（按标准 24.3 的 σ 规则）**：
- 并发 1：+20.5%（+5.60 RPS），两组区间完全不重叠（旧最大 27.6 < 新最小 30.7）⇒ **已证实**
- 并发 10：+9.7%（+2.73 RPS），区间重叠（旧 29.4 = 新 29.4）⇒ **倾向，未达确证**，需再跑多轮才能升级

> 🔴 **2026-11-07 后续更正（读本节前必看）**
> 上面这组的对照组 **8011 是 11-04 起、已连跑三天的老进程**，实验组 8012 是刚起的
> ⇒「进程年龄」与「代码改动」两个解释同时成立，按标准第二十二章判据无效。
> 后来在 `docs/tasks/TASK-W3B-交付说明.md`「性能 A/B 第三轮」里用
> **同轮交替 + 顺序翻转**重做：W3A+W3B **合计** 并发 1 **+24.4%**、并发 10 **+84.3%**，
> 且每请求 CPU 降 **38%~41%**。
> ⇒ **本节的 +20.5% / +9.7% 作废为方向性证据，不要对外引用具体数字。**
> （W3A 单独的贡献至今没有单独测过；能站住的只有「合计」。）

### 回归测试

```
$ pytest tests/test_real_demo_api.py tests/test_real_demo_extra.py \
         tests/test_real_demo_single_query_equiv.py tests/test_demo_pages_smoke.py -q --no-cov
77 passed in 43.98s
```

API/Demo 相关**全部文件**（`ls tests/*.py | grep -E "(demo|api)"`，共 20 个文件）：

```
259 passed in 165.12s (0:02:45)
```

⇒ 改前这些文件全绿，改后仍全绿，通过数 259（含新增 3 个）。

> 📌 **2026-11-07 后续**：`tests/test_real_demo_single_query_equiv.py` 在自查中又补了 2 条守卫
> （线上函数等价 / 同名重复取最小 id），该子集现为 **261 passed**；全量由 2450 → **2452 passed**。
> 本节 259 / 2450 是 W3A 当时的时点值，保留不改。

### 全量回归（功能分支 HEAD `f3820db`）

```
$ CODEBUDDY_SAFE_DELETE_ENABLED=0 python -m pytest -q --no-cov --tb=line -p no:cacheprovider
2450 passed in 1583.65s (0:26:23)     exit=0
```

核对：改动前本地全量为 **2447** passed，本分支新增 3 个等价用例 ⇒ 期望 **2450**，实测正是 2450，
**0 failed / 0 error**。数字能对上，不是"跑完了就行"。

> 必须带 `CODEBUDDY_SAFE_DELETE_ENABLED=0`：不带时安全删除垫片对同一文件删超 50 次会抛
> `SystemExit(1)`（继承 `BaseException`，`except Exception` 抓不住），表现为
> `49 passed / 2399 errors` 的假崩，与代码无关。

## 接口影响

**无变化**。响应体的字段名、字段顺序、空值语义（`""` 而非 `null`）、排序（`tender.id` 升序）全部不变，
且有逐字节比对背书（sha256 `b76fd96c409ce023…`）。

唯一可观察的差别是**快**：并发 1 时 P50 从 40ms 降到 30ms。

## 数据迁移

无。未改表结构、未改索引、未改数据。

## 已知限制

1. **JOIN 结果集仍是 829 × 3 列展开**（约 1200 行），只是不再多跑一次 SQL。若将来字段列扩到 6 列，行数会翻倍——那时要改用「两段查询 + 更强缓存」，不是继续 JOIN。
2. **并发 10 的 +9.7% 只是倾向**，不满足确证门槛；对外只能说「并发 1 提升已证实，高并发待复测」。
3. **根因未完全拔掉**：合并成一条 SQL 后，每次请求仍要 `await db.execute` 一次。
   ~~真正的地板是 aiosqlite 的线程模型，跨线程约 5ms 级。~~
   **⚠️ 2026-11-07 更正（TASK-W3B 实测）**：这句推断是错的。`_perf/thread_hop_probe.py` 用
   「同一段 SQL 只换执行通道」量出跨线程固定税 **只有 0.30ms**，占一次 LEFT JOIN 的 4.0%。
   本任务的收益来自**省掉第二段查询自身的 DB 工作量**（约 3.15ms），不是省掉线程跳。
   据此已否决「换同步驱动 / 去掉 aiosqlite 线程模型」这条路：收益对不上，风险大得多。
4. 本改动**未在 CI 上跑过**（分支未推），仅本机 Windows + Python 3.13 验证。
5. **残余约 3.6ms 未定位**（残差 12.47 − 框架 3.76 − 拼装 3.67），分散在
   `@app.middleware("http")`（BaseHTTPMiddleware）+ SlowAPI 上，不是单点，本轮不动。

## 验收方法

```bash
# 1 等价性 + 语义
CODEBUDDY_SAFE_DELETE_ENABLED=0 python -m pytest tests/test_real_demo_single_query_equiv.py -q --no-cov

# 2 回归（API/Demo 相关全部文件）
CODEBUDDY_SAFE_DELETE_ENABLED=0 python -m pytest \
  $(ls tests/*.py | grep -E "(demo|api)" | tr '\n' ' ') -q --no-cov

# 3 响应逐字节比对（需两个服务：旧代码在 8011、新代码在 8012）
curl.exe -s http://127.0.0.1:8011/api/real/tenders | sha256sum
curl.exe -s http://127.0.0.1:8012/api/real/tenders | sha256sum   # 两值必须相同

# 4 A/B 压测（同轮交替，各 3 轮）
python _perf/ladder.py --host http://127.0.0.1:8011 -l 1,10 -t 15
python _perf/ladder.py --host http://127.0.0.1:8012 -l 1,10 -t 15
```

## Git 信息

- 分支：`feature/wb-W3A-merge-tender-queries`（**未合并、未推 main**，合并权在人工）
- 提交：`perf(real_demo): 招投标列表两条查询合并为一条 LEFT JOIN`
