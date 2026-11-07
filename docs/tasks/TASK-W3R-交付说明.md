# TASK-W3R 交付说明 · W3A/W3B 标准合规返工

> 按 `Desktop/重要/AI协作执行标准_v1.2.md` 第六章模板。
> 任务单：`docs/tasks/TASK-W3R-标准合规返工.md`
> 分支：`feature/wb-W3B-tuple-unpack`（W3A `41da76b` → W3B `c1d797d` → 本返工 `d3c05af`）
> **状态：待人工验收**（铁律 8：模型声称完成不算完成）

---

## 完成内容

本轮不改业务逻辑，只把 W3A/W3B 两轮交付里**不符合标准的部分**补到合格。共 4 项：

### 1. 数据口径收口（假完成第 6 类）

**问题**：仓库内 `data/bidagent.db` 是 **828** 条 tender，工作区是 **829** 条，
而 README 与两份交付说明都写「829 篇」。后果不是"文档写错一个字"，而是：

- 任何人克隆本仓库后跑真实数据测试，拿到的是 828 条；
- 两份交付说明里记的响应 `sha256 b76fd96c…` / 191064 B / 829 条，
  在干净克隆上**无法复现** —— 一条可复现性证据失效。

**处置**：把工作区的 829 条库提交入库，使「仓库 = 工作区 = 文档 = 响应」四者一致。

**为什么选这个方向**（而不是丢弃那 1 条、或把文档改成 828）：

1. 该文件历史上已被提交过 3 次（`560791e` / `292ae5d` / `93c5ada`），
   其中 `93c5ada` 的提交信息明写「库829条」—— 说明 829 才是既定基线，HEAD 的 828 是漏提交；
2. 那 1 条（`id=927`，天津市天津中学食堂服务项目中标公告，`created_at 2026-08-21`）
   是**真实抓取数据**，不是测试造的。删它属于破坏性数据操作，标准不允许我顺手做；
3. 提交后 `git status` 干净，口径永久闭合，不必每次解释"为什么有 1 条未提交"。

### 2. 测试结果补四元组（18.5 硬要求）

原交付只写 `2452 passed` 一个数，按 18.5「只写一个数 ⇒ 假完成第 6 类」不合格。
现补全 collected / passed / failed / skipped / errors 五项（见「测试结果」）。

### 3. 盲区显式声明（铁律 9 / 18.1）

新增「测试能抓什么、抓不住什么」一节 —— 这是铁律 9 的强制项，此前缺。

### 4. 15 类假完成逐条自查（第八章）

逐条给结论，命中项写明处置，不再点状自查。

---

## 修改文件

| 文件 | 变更 | 说明 |
|---|---|---|
| `data/bidagent.db` | 更新（828 → 829 条） | 二进制，`5279744 → 5287936` 字节 |
| `docs/tasks/TASK-W3R-标准合规返工.md` | 新增 | 任务单（第十七章模板） |
| `docs/tasks/TASK-W3R-交付说明.md` | 新增 | 本文件 |
| `_perf/sqlite_leak_probe.py` | 新增 | 第 13 类追查用探针（T8），可复现 |
| `_perf/README.md` | 加 T8 一行 | 记录探针用法与前提（必须带 coverage） |
| `README.md` | 1 行 | 补「W3A/W3B 后为 2452 用例 / 26 分 31 秒」 |
| `ARCHITECTURE.md` | 1 行 | `2447 passed` → `2452 passed` |
| `CONTRIBUTING.md` | 1 行 | `2447 项通过` → `2452 项通过`（覆盖率 88.28% 未变） |
| `docs/tasks/TASK-W3A-交付说明.md` | 加指针 | 标明 `+20.5%/+9.7%` 已被 W3R 取代 |
| `docs/tasks/TASK-W3B-交付说明.md` | 加指针 | 标明合规返工见 W3R、历史数字不再更新 |

**本轮未修改**：`app/` 下任何代码、`tests/` 下任何测试。
核验命令与结果：

```bash
$ git diff --stat c1d797d HEAD -- app/ tests/
（输出为空）
```

⇒ 上一条全量回归 `2452 passed`（在 `c1d797d` 上跑的）对本 HEAD 同样成立。

---

## 新增测试

**0 条。** 本轮不改逻辑，按任务单「非目标」不新增业务用例。

替代验证（一次性校验，不进 CI）：

| 验证 | 命令要点 | 结果 |
|---|---|---|
| 干净克隆口径 | `git show HEAD:data/bidagent.db` → sqlite 计数 | **829** |
| 库完整性 | `pragma integrity_check` / `quick_check` | `ok` / `ok` |
| 响应可复现 | `TestClient` 打真实库 `/api/real/tenders` | `b76fd96c…` / 191064 B / 829 |

---

## 测试结果

### 全量回归（2026-11-07 实测，`c1d797d`，代码与 HEAD 相同）

```
collected = 2452
passed    = 2452
failed    = 0
skipped   = 0
errors    = 0
耗时      = 1591.39s (0:26:31)   exit=0
```

命令：

```bash
CODEBUDDY_SAFE_DELETE_ENABLED=0 python -m pytest -q --no-cov --tb=line -p no:cacheprovider
```

> `CODEBUDDY_SAFE_DELETE_ENABLED=0` 是本机沙箱必需：不设会被批量删除守卫
> 打断成 `49 passed / 2399 errors` 的假崩，**与代码无关**。

### 全量带覆盖率（`ARCHITECTURE.md` / `CONTRIBUTING.md` 里 88.28% 的出处）

```
TOTAL   10336 语句   1211 未覆盖   88%
2452 passed in 1605.17s (0:26:45)
Required test coverage of 40% reached. Total coverage: 88.28%
```

> ⚠️ 这次跑的进程**装了 T8 连接探针**（只替换 `sqlite3.connect` 的 factory，不改断言），
> 用例数与不带探针完全一致（2452）。但它重写了 `Connection.__del__`，
> **会把 sqlite3 原生的 `ResourceWarning` 顶掉** ⇒ 这一跑**不能**用来证明 warning 已清零。

### API/Demo 子集（20 个文件）

```
261 passed in 153.02s (0:02:33)   exit=0
```

### 覆盖率（4.5 条要求：新增代码增量 ≥90%）

```
$ pytest $(ls tests/*.py | grep -E "(demo|api)") -q -o addopts="" \
    --cov=app.api.real_demo --cov-report=json:C:/tmp/cov_rd.json

real_demo.py 总可执行行   98，未覆盖 0         ⇒ 文件覆盖率 100%
W3A/W3B 改动区间内可执行语句 20，已覆盖 20     ⇒ 增量覆盖率 100%
```

改动行号（新增侧）：`26, 250–261, 264–271, 273–319`，其中可执行 20 行全被覆盖。

### 变异测试（18.2）

在 `app/api/real_demo.py` 植入 3 个典型变体，跑 `tests/test_real_demo_single_query_equiv.py`：

| 变体 | 语义 | 结果（2026-11-07 本轮复测） |
|---|---|---|
| V1 `field_keys` 去掉 `amount` | 字段口径缩小 | **3 failed** ✅ 抓到 |
| V2 `plat or ""` → `plat` | 空值语义从 `""` 变 `None` | **1 failed** ✅ 抓到 |
| V3 `order_by(Tender.id, ExtractedField.id)` → `order_by(Tender.id)` | 去掉同名字段"取首条"的确定性 | **0 failed** ⚠️ 未抓到 |

V1 的 3 个红分别是：`test_endpoint_function_matches_legacy_on_real_db`、
`test_null_semantics_and_fallback`、`test_duplicate_non_empty_field_takes_lowest_id`。
**唯独 `test_equivalence_on_real_db` 不红** —— 它比的是两份手抄逻辑，正是下面「盲区 1」。

**基线确认（18.2 要求）**：三个变体测试前，原代码均为 `5 passed` 全绿，判据未被污染。

**V3 未抓到的原因已查清，不是盲区**：

```
有/无 ExtractedField.id 排序，1042 行完整行序列完全相同（逐行 tuple 比对）
```

SQLite 走 `ix_extracted_fields_tender_id` 索引扫描，天然按 `(tender_id, rowid)` 返回，
该子句在本库上是**冗余排序**。因此 V3 在 SQLite 上不产生行为差异，
"测试抓不到"是因为**变异不可观测**，而不是断言缺失。
⇒ **不宣称"排序子句已由测试守卫"**（那是没被证实的）。

---

## 接口影响

**无。** 响应逐字节不变：

```
GET /api/real/tenders
http   200
bytes  191064
sha256 b76fd96c409ce023d3bf73b0a760a4ddd21010c4aafe99a3e300085b328ac59d
count  829
```

字段名、字段顺序、空值语义均与 W3A 基线一致（`created_at` 单独存 `created_map`，
不塞进 item dict，正是为了不改键序）。

---

## 数据迁移

**无 Schema 变更。** 仅数据文件同步：

| 项 | 值 |
|---|---|
| 文件 | `data/bidagent.db` |
| 提交前 sha256（工作区 = HEAD 提交后） | `704d1407a76d317f83195a29057d78af7d7c692c842f6320e51f8057cc4a00f1` |
| `tenders` | 828 → **829** |
| `extracted_fields` | 582（未变） |
| 字节 | 5279744 → 5287936 |
| `integrity_check` / `quick_check` | `ok` / `ok` |

> 该 sha256 `704d1407…` 与 A/B 实验里「两库同源」用的是同一个哈希，
> 即实验所用库与入库库**逐字节相同**。

---

## 已知限制

1. **仓库体积 +5.3MB**：`data/bidagent.db` 是二进制且被 git 跟踪（`.gitignore` 里虽有
   `data/` 与 `*.db`，但该文件在规则之前已被跟踪，跟踪优先于忽略）。
   本次为口径一致选择继续提交。**若要根治，需迁 Git LFS 或把库移出仓库**，
   但那会让 `skipif(not exists)` 的真实数据测试在 CI 上静默跳过 ⇒ 制造假绿，
   属于独立任务，本轮明确不做。
2. **库内存在 7 + 7 + 186 行孤儿引用**（`foreign_key_check` 实测）：

   ```
   extracted_fields  7 行 → tender_id 不存在（106, 353, 387, 410, 498, 581, 582）
   evidence          7 行 → tenders 不存在（110, 357, 391, 414, 502, 585, 586）
   subscriptions   186 行 → users.id=0 不存在
   ```

   影响：列表接口走 `LEFT JOIN tenders`，这些孤儿**不会出现在响应里**，故不影响本任务。
   **未删除** —— 删除属破坏性数据操作，需用户点名（标准：不擅自做破坏性动作）。
3. **`--cov` 下有 1 条 `ResourceWarning: unclosed database`**（4.5 要求本项目 warning 清零）。
   `--no-cov` 全量跑是 **0 warning**，只有 coverage 插桩时出现。**本轮正式追查过，未定位**，
   记为「待查」（第九章），详见下节。**不对外宣称「0 warnings」。**
4. **根因仍在**：`await db.execute` 每次仍跨线程一次，实测 **0.30ms**（不是早期误记的 5ms）。
   拔它上限只有 0.3ms、风险是全局的 ⇒ 已判死。
5. **约 3.6ms 未定位**：`残差 12.47 − 框架 3.76 − 拼装 3.67 ≈ 3.6ms`，
   疑似 `@app.middleware("http")`（BaseHTTPMiddleware）+ SlowAPI。非单点，不动。
6. **性能只在单机回环验证**：并发 3 峰值 `40.17 → 73.53 RPS`（+83.0%，17σ），
   无跨机器证据，不能外推生产。
7. **未在 CI 上跑过**：分支未推送，仅本机 Windows + Python 3.13。
8. **探针脚本含占位密钥**：`_perf/*.py` 里 `SECRET_KEY="a"*64`、`ADMIN_SECRET="test-admin-secret-12345"`
   是为满足 app 配置校验而设的**假值**，不是真实凭证；已按 3.3 跑过密钥扫描（本分支新增行
   无 `api_key/password/secret/token` 明文赋值命中，`.env`/`*.key`/`*.pem` 均未入库）。

---

## 第 13 类追查记录：`--cov` 下的 1 条 ResourceWarning（结论：待查）

按 4.5「本项目代码产生的 warning 必须清零」，本轮专门立项追查，以下是**实际做过的四步**与结果。

### 第 1 步 · 静态清点所有 `sqlite3.connect` 调用点

```bash
$ grep -rn "sqlite3\.connect\|from sqlite3 import" app/ tests/ --include="*.py"
tests/test_demo_trust_replay.py:83      conn.close() ✅
tests/test_evidence_hardening.py:46     conn.close() ✅
tests/test_evidence_hardening.py:55     conn.close() ✅
tests/test_evidence_hardening.py:105    conn.close() ✅
tests/test_evidence_hardening.py:134    conn.close() ✅
tests/test_verify_award.py:156          conn.close() ✅
app/ 下：0 处直接连接；无 `from sqlite3 import connect` 写法
```

⇒ 6 处全部显式 `close()`，`fixture` 是 `yield … finally: conn.close()`。**都不漏。**

### 第 2 步 · 探针追踪（`_perf/sqlite_leak_probe.py`，本轮新增）

做法：包住 `sqlite3.connect`，注入子类，`close()` 出表、GC 未关则打印创建位置。
必须带 coverage 跑（不带就不复现）：

```
[SQLITE-LEAK] probe installed (v2, cheap stack)
2452 passed in 1605.17s (0:26:45)
[SQLITE-LEAK] session 结束仍有 0 个连接未 close
```

⇒ 经 `sqlite3.connect` 创建的连接（**含 aiosqlite 的 `core.py:466` 路径**，因为它是
`sqlite3.connect(loc, **kwargs)`、走模块属性）**全部正常关闭，0 残留**。

### 第 3 步 · 单文件复现尝试

告警 GC 落在 `tests/test_evidence_hardening.py::test_api_lifecycle_chain_requires_number`，
但单独跑该文件（带 `--cov=app`）：

```
13 passed in 9.76s     （无 ResourceWarning）
```

⇒ 说明「GC 落点」只反映**那一刻恰好触发回收**，不是泄漏源头。这条本身也是一个坑：
**按告警落点去改文件，会改错地方。**

### 第 4 步 · 结论

**未定位。登记为「待查」。** 已经排除的：本项目全部 `sqlite3.connect` 调用点、
aiosqlite 路径、单文件复现。**仍未知的**：这条连接由谁创建。
唯一还没排除的候选是 coverage 7.15.0 自身以 SQLite 存 `.coverage` 数据（`CoverageData`），
但**没有任何证据支持** ⇒ 按铁律 11 标为**未知**，不写成"就是 coverage 造成的"。

### 🔴 一个必须记下的方法论坑

探针 v2 为了定位，重写了 `Connection.__del__`，**这会把 sqlite3 原本的
`ResourceWarning` 顶掉** —— 所以「探针跑时告警消失了」**不能当作已修复的证据**，
那是判据污染（标准 22.1）。要证明修好，只能看**不装探针**的全量跑是否还有 warning。

### 待查的解除条件

1. 复现并钉死创建方；
2. 修完后**不装探针**跑一次全量 `--cov`，确认 `warnings summary` 消失；
3. 人工复测通过。

在此之前，README 里那句「不要对外说 0 warnings」保持不变。

---

## 测试能抓什么 / 抓不住什么（铁律 9 / 18.1）

### 能抓

| 能力 | 证据 |
|---|---|
| **线上函数真的变了** | 补了一条直接 `await list_tenders(db)` 打真实库的用例；V1 变异时它变红 |
| 字段口径缩小 | V1 → 2 红 |
| 空值语义变化（`""` ↔ `None`） | V2 → 1 红 |
| JSON 键序变化 | `json.dumps(...)` 全等比对，键序不同即红 |
| `select(...)` 列序与解包顺序错位 | 解包时立刻抛 `ValueError` |
| 空库 / 无字段公告的回落逻辑 | `test_empty_db_still_returns_list_empty` 等用 ORM 播种覆盖 |
| 条数变化 | 断言改为与真实库动态比对，不再写死 829 |

### 抓不住（必须知道的盲区）

1. **"复制品自证"型测试抓不到线上改动** —— 这是本轮修掉的真缺陷：
   原 `test_equivalence_on_real_db` 用测试内复制的 `_legacy_assembly` vs `_new_assembly`，
   **V1 变异下它不红**。它只证明"两份手抄逻辑一致"，不证明"线上代码没坏"。
   ⇒ 已补直接调线上函数的用例；此类结构**以后不要再写**。
2. **不可观测的变异抓不到**：V3（去掉冗余排序）在 SQLite 上无行为差异，
   任何黑盒测试都不可能抓到。要抓只能做 SQL 文本断言，而那会把实现细节焊死。
3. **真实库不含 `source_platform IS NULL` 的行** ⇒ 真实数据等价测试覆盖不到该分支，
   靠单播种子补，属"构造用例"而非"真实数据"。
4. **并发/时序缺陷**：本轮测试全是单请求语义等价，**不测并发正确性**。
   高并发下的正确性只有 A/B 压测的 0 错误率这一条弱证据。
5. **孤儿行不进入响应**，故不会被任何接口级测试发现 —— 只能靠 `foreign_key_check`。

---

## 15 类假完成自查（第八章）

| # | 类型 | 结论 | 依据 |
|---|---|---|---|
| 1 | 函数 stub | 未命中 | 无 stub，`list_tenders` 完整实现 |
| 2 | 测试不足 | 未命中 | 正常/空库/空值/重复字段/真实库 5 条 |
| 3 | 用 mock 数据 | 未命中 | 冒烟打真实 829 条库 + 真服务 |
| 4 | 占位实现 | 未命中 | 无 TODO/pass/NotImplemented |
| 5 | 硬编码 | 未命中 | 测试里 `== 829` 已移除，改动态比对 |
| 6 | 统计口径不一致 | **命中 → 已修** | 仓库 828 / 本地 829 / 文档 829 → 统一 829 |
| 7 | 迁移验收不完整 | 未命中 | 无 Schema 变更；库完整性 `ok` |
| 8 | 真实 LLM 未验证 | 不适用 | 本任务不涉及 LLM 调用 |
| 9 | 真实数据未验证 | 未命中 | 真实库 829 条 + sha 逐字节比对 |
| 10 | ID 生成不合格 | 未命中 | 未生成业务 ID |
| 11 | 约束冲突 | 未命中 | 无多值写入 |
| 12 | 算法错误 | 未命中 | 分组逻辑有等价测试锁死 |
| 13 | warnings 未清理 | **待查** | `--no-cov` 全量 0 warning；`--cov` 下 1 条。四步追查未定位、已排除项见「第 13 类追查记录」 |
| 14 | 测试无鉴别力 | **命中 → 已修** | V1 曾被"复制品自测"放过，已补直调线上函数的用例 |
| 15 | 提议代替执行 | 未命中 | 本轮是执行，不是选项；不再以"要不要我做 X"结尾 |

**结论**：2 条命中已修，1 条待查（#13），其余未命中或不适用。

---

## 验收方法

可复现，逐条照抄即可。

```bash
cd "C:/Users/Lenovo/Desktop/Agent项目/BidAgent"

# 1 仓库内库口径（应为 829）
git show HEAD:data/bidagent.db > /tmp/head.db
python -c "import sqlite3;print(sqlite3.connect('/tmp/head.db').execute('select count(*) from tenders').fetchone())"

# 2 工作区库与仓库库是否一致（应打印 True）
python -c "import subprocess,hashlib;b=subprocess.run(['git','show','HEAD:data/bidagent.db'],capture_output=True).stdout;print(b==open('data/bidagent.db','rb').read())"

# 3 等价性测试（应 5 passed）
CODEBUDDY_SAFE_DELETE_ENABLED=0 python -m pytest tests/test_real_demo_single_query_equiv.py -q --no-cov

# 4 全量回归（约 26 分钟，应 2452 passed）
CODEBUDDY_SAFE_DELETE_ENABLED=0 python -m pytest -q --no-cov --tb=line -p no:cacheprovider

# 5 响应一致性（应为 b76fd96c… / 191064 B）
#    起服务后：curl.exe -s http://127.0.0.1:8011/api/real/tenders | sha256sum
```

**不通过判据**：任一条与上述数值不符，即判「待查」，不得调整基线迁就结果。

---

## Git 信息

| 项 | 值 |
|---|---|
| 分支 | `feature/wb-W3B-tuple-unpack` |
| 本返工提交 | `d3c05af` chore(data): 同步评测库到 829 条，消除仓库/本地/文档三处口径分叉 |
| 上游链 | `41da76b`（W3A）→ `c1d797d`（W3B 末尾）→ `d3c05af` |
| 是否推送 | **未推送**（标准 23.5：推送/合并需点名确认） |
| 是否合 main | **未合并，也不得由我合并**（第一章：AI 不拥有合并权） |

### 遗漏与偏离的显式登记（23.x）

1. **W3B 任务单第 69 行**原写「等价测试会红 = 守卫，不再另加断言」，
   已被变异测试推翻。**未静默改写**，在任务单末尾加「执行后批注」记录偏离。
2. **本返工没有独立 feature 分支**：因为 W3A/W3B 都未推送未合并，
   在 `feature/wb-W3B-tuple-unpack` 上追加收口提交，避免制造 W3B → W3R 的叠层分支。
   若后续要求严格"一任务一分支"，可把这些提交 cherry-pick 到 `fix/W3R-*`。
3. **`data/bidagent.db` 提交超出 `.gitignore` 的意图**，已在「已知限制 1」写明代价与备选方案。

---

## 状态

**待人工验收。**

按铁律 8，我不宣称"完成"。人工复测上面的「验收方法」五条通过后，才可改为「完成」。
