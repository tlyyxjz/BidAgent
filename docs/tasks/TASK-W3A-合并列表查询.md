# 任务单：TASK-W3A

## 任务名称
招投标列表接口：两条查询合并为一条 LEFT JOIN（不改接口契约）

## 负责人
WorkBuddy 执行模型（Claude）；人工负责人＝徐浚钊（拥有最终合并权）

## 背景
压测 W1（`压测W1_跑什么与为什么_20261106.md` §7.4 / §7.5）实测结论：
- 单请求 23.4ms，其中 **84% 在端点函数内**；
- 两段 SQL 游标执行 7.1ms，但 `await db.execute` 实含 **aiosqlite 线程跳**，真实 9.4ms；
- 10 并发时进程烧到 **306%（3.06 核）**，每请求 CPU **121ms**、墙钟仅 23.4ms ⇒ 线程争用是主因；
- 10 并发 P50 从 23ms 涨到 352ms（15 倍），而 CPU 只用 3/32 核 ⇒ 同一进程内串行化。

⇒ 最省的改动不是「加进程」（H1 已排除）、也不是「优化 Python 循环」（拼装仅 2.9ms），
而是**把每请求 2 次跨线程 DB 往返压成 1 次**。

## 目标
`GET /api/real/tenders` 的数据库往返从 2 次降为 1 次，**响应 JSON 与改前逐字节一致**，
并在同轮对照下证明单请求时延与 10 并发吞吐的变化（或证明与噪声不可区分）。

## 输入
- `app/api/real_demo.py`：`list_tenders`（L246-287）
- `app/models/tender.py`、`app/models/evidence.py`
- 真实库 `data/bidagent.db`：829 条 tender、582 行 extracted_fields（该接口取三列 367 行）
- 压测脚本：`_perf/decompose_request.py`、`_perf/ladder.py`

## 允许修改
- `app/api/real_demo.py` 中 `list_tenders` 一个函数体
- 新增 `tests/test_real_demo_single_query_equiv.py`
- `_perf/` 下测量记录

## 禁止修改
- 响应 JSON 的字段名、字段顺序、空值语义（`""` 而非 `null`）
- `_ok` / `_err` 助手、其他端点、`app/models/*` 的 Schema
- 数据库迁移、`tests/fixtures/gold/*`（冻结集）
- 端口 8011 上正在运行的服务进程（它就是对照组）

## 接口约定
`GET /api/real/tenders → 200 {"code":200,"data":{"tenders":[{id,name,type,amount,purchaser,publish_date,platform}, ...]},"msg":"ok"}`
- 顺序：按 `tender.id` 升序
- 每个字段取「该 tender 下同名字段中 id 最小且 raw_value 非空」的值，无则 `""`
- `publish_date` 缺失时回落到 `created_at.strftime("%Y-%m-%d")`，`created_at` 为 None 时 `""`

## 实现要求
单条 `SELECT ... FROM tenders LEFT JOIN extracted_fields ON (tender_id = tenders.id AND field_name IN (...)) ORDER BY tenders.id, extracted_fields.id`，
内存里按 tender_id 分组、每个字段名取首次出现的非空值（与旧实现同序，因 order by id）。

## 必须补充的测试
- 等价性：新实现输出 vs 旧两查询实现的输出，**逐字节比对**（真实库 829 条）
- 空值：无字段的 tender 必须给 `""` 而不是 `null`/缺失
- 边界：某 tender 同名字段有多行时取 id 最小且非空者
- 异常：`extracted_fields` 表为空时仍返回 829 条且全空字段
- 回归：`tests/test_real_demo_api.py`、`tests/test_real_demo_extra.py` 全绿

## 冒烟测试要求
- 真实数据：`data/bidagent.db`（829 条），不是 fixture
- 记录指标：单请求 P50、10 并发 RPS / P50、响应字节数、错误率，**同轮新旧交替各 3 轮**
- 失败处理：任一响应体与对照组不一致 ⇒ 立即回滚，不提交

## 验收标准
1. 等价性测试通过，且 829 条真实数据的响应 SHA256 与改前相同
2. 现有相关测试全绿（不得少于改前通过数）
3. A/B 数据同轮采集，且结论按 1σ 规则表述（小于噪声的说「与噪声不可区分」）
4. 未改任何禁止清单里的文件（`git diff --stat` 可核）

## 非目标
- 不动分页/缓存（那会改接口契约，属 W4，需人工决策）
- 不动 `_ok`/序列化路径
- 不动其它端点、不动数据

## 交付要求
按第六章模板：完成内容 / 修改文件 / 新增测试 / 测试结果（贴真实输出）/ 接口影响 / 数据迁移 / 已知限制 / 验收方法 / Git 信息。
分支 `feature/wb-W3A-merge-tender-queries`，**不合并、不推 main**。
