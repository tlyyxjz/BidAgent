# 任务单：TASK-W3B · 拼装段改用元组解包（并更正 W3A 的机制说法）

> 按 `Desktop/重要/AI协作执行标准_v1.2.md` 第十七章模板。
> 分支 `feature/wb-W3B-tuple-unpack`，从 `feature/wb-W3A-merge-tender-queries` 的 `41da76b` 切出。

## 背景

W3A 合并查询后重跑 T0 分解（`_perf/decompose_request.py`，n=30）：

```
端到端(inproc)       19.62 ms   （改前 23.39 ms）
段1 主查询            6.25 ms   31.8%
段2 字段查询          0.00 ms      —（已合并，1 条 SQL/请求）
段4 序列化            0.91 ms    4.6%
残差(段3+框架)        12.47 ms   63.5%
```

残差成了最大单块。进一步用 `_perf/thread_hop_probe.py`（T5）与 `_perf/assembly_probe.py`（T6）拆开：

| 测点 | 数值 | 口径 |
|---|---|---|
| 跨线程固定税 hop | **0.30 ms** | `SELECT 1` async − sync，占 JOIN 总耗时 4.0% |
| LEFT JOIN execute+all（async） | 7.63 ms | T5，n=150 |
| LEFT JOIN（sync 直连） | 6.54 ms | 同上，两通道各复用同一连接 |
| 拼装：`Row` 属性访问 | **3.67 ms** | T6，n=40，1042 行 |
| 拼装：元组解包 | **1.40 ms** | 同上 |
| 会话创建（`AsyncSessionLocal`） | 0.025 ms | n=200，可忽略 |

**结论一（更正）**：W3A 交付说明「已知限制 #3」与 `real_demo.py` 函数注释里
「两次往返约 9.4ms / 仍要跨线程一次（约 5ms 级）」**是错的**。跨线程单次只有 0.30ms。
W3A 的收益来自**省掉第二段查询自身的 DB 工作量**（约 3.15ms），不是省掉线程跳。
机制说法必须更正，否则后人会朝「换同步驱动去掉线程模型」这个错误方向改。

**结论二**：拼装 3.67ms 是 `Row` 的 7 次属性查找（`_mapping` 逐次查表）造成的，
换成一次元组解包降到 1.40ms，**输出 JSON 全等（含键序）**。

## 目标

`list_tenders` 的拼装循环由 `Row` 属性访问改为元组解包，响应逐字节不变。

## 输入

- 库：`data/bidagent.db`（829 tenders / 582 extracted_fields / JOIN 出 1042 行）
- 现状代码：`app/api/real_demo.py:246-317`

## 允许修改

- `app/api/real_demo.py` 的 `list_tenders` 函数体（拼装循环 + 函数 docstring 里那句错误机制说明）
- `docs/tasks/TASK-W3A-交付说明.md` 的「已知限制 #3」（更正为实测值）
- `tests/test_real_demo_single_query_equiv.py`（若需补一条防解包错位的测试）

## 禁止修改

- SQL 部分（`select(...)` 的列顺序、`outerjoin` 条件、`order_by`）—— 一行都不许动
- 响应体的字段名、字段顺序、空值语义
- `_ok` / `_err` / 其它端点 / 模型 / 迁移
- **不得为了「更快」改成 raw `text()` SQL**：`text()` 不带类型转换，`created_at`
  会变成 str（本任务探针里已踩过一次，报 `'str' object has no attribute 'strftime'`）

## 接口约定

响应 JSON 与 W3A 完全一致。判定标准：同一份库，改前/改后响应 `sha256` 必须相同
（W3A 基线 `b76fd96c409ce023…` / 191064 B / 829 条）。

## 实现要求

1. 迭代写法改为 `for tid, pn, nt, cat, plat, fn, rv in result.all():`
2. `select(...)` 的列顺序即解包顺序，**两者必须同步**；
   错位会立刻抛 `ValueError`，且等价测试会红 —— 这就是防错位的守卫，不再另加断言。
3. `created_at` 仍单独存 `created_map`，不得塞进 item（会改键序）
4. 更正 `list_tenders` docstring 里的机制说明：写实测 hop=0.30ms，
   收益来源是省掉第二段查询的 DB 工作量

## 必须补充的测试

- 复用 `tests/test_real_demo_single_query_equiv.py` 现有 3 条（真实库等价 / 空库 /
  空值回落）；其中 `_new_assembly` 需同步改为元组解包写法，保证测的是线上真实代码

## 冒烟测试要求

- 真服务 + 真库，8011（旧）vs 8013（新）响应 sha256 比对
- 不达标不得写「完成」

## 验收标准

1. `pytest tests/test_real_demo_single_query_equiv.py` 全绿
2. 真服务响应 sha256 与 W3A 基线相同
3. `_perf/assembly_probe.py` 显示 attr→tuple 提速 ≥50%
4. API/Demo 相关 20 个测试文件全绿
5. 全量测试通过数 = W3A 基线 2450（不增不减，除非新增测试）

## 非目标

- 不动覆盖索引（实测 JOIN 那条用不上，优化器改走 `publish_time` 索引 + 临时 B 树，反而更慢：
  3.68 → 4.13ms）
- 不动中间件（剩余约 3.6ms 分散在 `BaseHTTPMiddleware` + SlowAPI，非单点）
- 不动 aiosqlite 线程模型
- 不推远端、不合 main

## 交付要求

按第六章九节写 `docs/tasks/TASK-W3B-交付说明.md`，贴真实输出与 sha256，标注结论强度。
