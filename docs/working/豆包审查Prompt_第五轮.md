# 豆包代码审查 · 第五轮

## 背景

这是 ScrapeFlow 项目（超聚变命题 · 招投标信息聚合工具）的**第五轮审查**。

- **第四轮**：你给出 1 Critical + 7 Major + 5 Minor，评分 8.2/8.3/7.3
- **第五轮（本轮）**：GLM-5.2 修复了第四轮全部 9 个真实问题（1C + 6M + 3m），并对 2 个误判项做了说明

请基于本轮**新增/修改的代码**做第五轮审查，重点关注：
1. 第四轮 9 个问题是否真正修复
2. 修复是否引入新 bug
3. 拆分后的 tender_utils.py 是否引入循环依赖
4. 新的 SSRF DNS 解析是否会阻塞事件循环
5. escape_like 的 escape 字符是否在所有 LIKE 查询处都生效

命题硬要求（6+1 项）：
1. LLM 意图解析 5 槽位（关键词/地区/金额/时间/来源）
2. ≥2 网站 + ≥1 登录态采集
3. SimHash 内容去重
4. 5 字段汇总 + Word 命名规则
5. cron 定时执行
6. 增量推送（已推送内容不重复）
7. 反幻觉校验（生成内容必须有原文支撑）

---

## 关于第四轮误判的说明

### M-3（SSRF 重定向防护）— 误判
第四轮报告称"仅校验初始 URL，302 重定向到内网无法拦截"。实际上 `app/core/scraper.py` 第 214-231 行已经有 `page.route("**/*", _ssrf_guard)` 拦截所有请求。本轮保留此实现，并升级为 `is_safe_url_async`（M-2 修复配套）。

### M-2（SSRF DNS 绕过）— 真问题，本轮已修
第四轮报告称"域名形式只拦截 localhost 和 metadata.google.internal 两个硬编码值"。本轮已加 `socket.getaddrinfo` DNS 解析校验所有解析到的 IP。

---

## 本轮修改清单（共 9 项）

### 修改 1：C-1 修复 — 拆分 tender_ingestor.py 到 ≤ 300 行

**新建** `app/processors/tender_utils.py`，抽出纯函数：
- `_hash_contact`: SHA256 联系人
- `_parse_decimal`: 金额解析（万/亿元单位）
- `_parse_datetime`: 多格式日期解析
- `_infer_platform`: URL/模板名推断来源平台
- `_hamming_distance`: 64 位 SimHash 汉明距离
- `_build_tender`: 从采集 item 构建 Tender ORM 对象

**tender_ingestor.py** 主文件 192 行，只保留：
- `ingest_scrape_result`: 入库入口
- `_ingest_with_db`: 核心三阶段批量去重逻辑

```python
# app/processors/tender_ingestor.py 关键变更
from app.processors.tender_utils import _build_tender, _infer_platform

async def ingest_scrape_result(
    scrape_result: dict[str, Any],
    template: str | None = None,
    simhash_computer=None,
    db: AsyncSession | None = None,  # M-7 新增：外部 session
) -> dict[str, Any]:
    if db is not None:
        return await _ingest_with_db(db, scrape_result, template, simhash_computer, commit=False)
    async with AsyncSessionLocal() as new_db:
        return await _ingest_with_db(new_db, scrape_result, template, simhash_computer, commit=True)
```

**审查重点**：
- tender_utils.py 和 tender_ingestor.py 是否有循环 import？
- 主文件 192 行是否真的 ≤ 300？
- 抽出的纯函数是否保持原语义（特别是 `_build_tender` 的字段映射）？

---

### 修改 2：M-1 修复 — escape_like 配套 escape 参数

**新建** `app/scheduler/utils.py` 中的 `safe_like` / `safe_contains` 函数：

```python
LIKE_ESCAPE = "\\"

def safe_like(column, value: str):
    return column.like(escape_like(value), escape=LIKE_ESCAPE)

def safe_contains(column, value: str):
    return column.contains(escape_like(value), escape=LIKE_ESCAPE)
```

**subscription.py** 改用 `safe_contains`：
```python
# 修改前
stmt = stmt.where(Tender.location.contains(escape_like(filters.region)))
# 修改后
stmt = stmt.where(safe_contains(Tender.location, filters.region))
```

**tender.py** 也改用 `safe_contains`：
```python
stmt = stmt.where(safe_contains(Tender.location, region))
stmt = stmt.where(safe_contains(Tender.project_name, topic))
```

**审查重点**：
- `escape="\\"` 是否是 SQLAlchemy 的正确语法？
- 全项目 LIKE/contains 查询是否全部改用 safe_contains？还有没有遗漏？
- LIKE_ESCAPE 常量是否需要在所有调用方一致使用？

---

### 修改 3：M-2 修复 — SSRF 加 DNS 解析 + async 版本

**重写** `app/utils/url_safety.py`，新增：
- `_is_ip_blocked`: IP 内网/保留校验（含 IPv6 mapped IPv4 解包）
- `_check_dns_records`: `socket.getaddrinfo` DNS 解析后校验所有 IP
- `is_safe_url_async`: async 版本，用 `asyncio.to_thread` 包装

```python
def _check_dns_records(hostname: str) -> tuple[bool, str]:
    """M-2 修复：防 localtest.me / nip.io / xip.io 等公共 DNS 解析服务绕过。"""
    try:
        addrs = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        return False, f"dns resolve failed: {hostname} ({exc})"

    for addr in addrs:
        ip_str = addr[4][0]
        ip = ipaddress.ip_address(ip_str)
        blocked, reason = _is_ip_blocked(str(ip))
        if blocked:
            return True, f"dns resolved to {reason}: {hostname} -> {ip}"
        # IPv4-mapped IPv6 二次校验
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            blocked, reason = _is_ip_blocked(str(ip.ipv4_mapped))
            if blocked:
                return True, f"ipv6-mapped {reason}: {hostname} -> {ip}"
    return False, ""
```

**scraper.py** 的 _ssrf_guard 改用 async 版本：
```python
async def _ssrf_guard(route):
    from app.utils.url_safety import is_safe_url_async
    req_url = route.request.url
    safe, reason = await is_safe_url_async(req_url)  # 不阻塞事件循环
    if not safe:
        await route.abort("blockedbyclient")
    else:
        await route.continue_()
```

**审查重点**：
- TOCTOU 风险仍存在（DNS rebinding），文档是否说明清楚？
- `socket.getaddrinfo` 在 Windows 下解析 IPv6 行为是否一致？
- `is_safe_url` 同步版本在测试 conftest 里被 mock，是否影响生产环境？
- IPv6 mapped IPv4 (`::ffff:127.0.0.1`) 的二次校验是否真的生效？

---

### 修改 4：M-4 修复 — 循环内 flush 改为批量 flush

**tender_ingestor.py** 阶段 3 改为：
```python
# 阶段 3：Python 层批量比对 + db.add（M-4：循环内不 flush）
to_insert: list[tuple[Tender, int | None]] = []
for idx, item in enumerate(items):
    try:
        if not isinstance(item, dict):
            errors += 1
            continue
        simhash_value = simhash_values[idx]
        if simhash_value is not None:
            dup_pair = find_duplicate_in_iter(
                simhash_value, candidates, SIMHASH_HAMMING_THRESHOLD
            )
            if dup_pair is not None:
                duplicates += 1
                continue
        tender = _build_tender(item, source_url, source_platform, simhash_value)
        db.add(tender)
        to_insert.append((tender, simhash_value))
    except Exception as exc:
        errors += 1
        logger.exception(...)

# M-4 修复：一次 flush 拿所有 ID（替代循环内 N 次 flush）
if to_insert:
    await db.flush()
    for tender, simhash_value in to_insert:
        inserted_ids.append(tender.id)
        inserted += 1
        if simhash_value is not None:
            candidates.append((tender.id, simhash_value))
```

**审查重点**：
- 同批次去重 `candidates.append((tender.id, simhash_value))` 在 flush 之后，ID 已生成，逻辑正确？
- 如果某条 `_build_tender` 抛异常被 except 捕获，但前面已经 db.add 了其他条，事务会脏吗？
- 批量 flush 后如果 commit 失败，rollback 是否会清理已 add 的对象状态？

---

### 修改 5：M-6 修复 — main.py 补 REPORT_OUTPUT_DIR 校验

```python
# app/main.py lifespan
_validate_data_dir(settings.COOKIE_DIR, "COOKIE_DIR")
_validate_data_dir(settings.ATTACHMENT_DIR, "ATTACHMENT_DIR")
_validate_data_dir(settings.REPORT_OUTPUT_DIR, "REPORT_OUTPUT_DIR")  # M-6 新增
```

**审查重点**：
- 现在三个目录都校验了，是否还有其他配置目录需要校验？
- `Path.resolve()` 在 Windows 下对 UNC 路径（`\\server\share`）的行为？

---

### 修改 6：M-7 修复 — collector 事务统一

**collector.py** 改为共用一个 session：

```python
async with AsyncSessionLocal() as db:
    for r in scrape_results:
        platform = r["platform"]
        if "error" in r:
            per_platform.append({"platform": platform, "error": r["error"]})
            continue
        try:
            # M-7：传入 db 复用事务，ingest_scrape_result 不内部 commit
            ingest = await ingest_scrape_result(
                scrape_result=r["result"],
                template=platform,
                simhash_computer=compute_simhash,
                db=db,
            )
            ...
        except Exception as exc:
            logger.exception("ingest failed platform=%s", platform)
            per_platform.append({"platform": platform, "error": str(exc)})
            # M-7：单个平台失败时 rollback，避免脏数据影响后续平台
            await db.rollback()

    # M-7：所有平台成功后才统一 commit
    await db.commit()
```

**审查重点**：
- 一个平台失败 rollback 后，下一个平台能否继续在同一 session 上工作？rollback 后 session 状态是否干净？
- 如果第一个平台成功 add 但 commit 前第二个平台失败，所有平台的数据都会被 rollback 掉，这个语义对吗？
- ingest_scrape_result 内部的 `await db.flush()` 在 rollback 后是否还能用？

---

### 修改 7：m-1 修复 — simhash.py 末尾空注释清理

删除了 `# 新-7 修复：删除未使用的 compute_simhash_async` 这行孤儿注释。

**审查重点**：现在文件末尾是否干净？

---

### 修改 8：m-2 修复 — database.py pool_pre_ping 移到非 SQLite 分支

```python
# 修改前
_engine_kwargs = {"echo": False, "pool_pre_ping": True}  # SQLite 也有 pool_pre_ping

# 修改后
_engine_kwargs = {"echo": False}
if settings.DATABASE_URL.startswith("sqlite"):
    _connect_args = {"check_same_thread": False, "timeout": 30}
else:
    _engine_kwargs.update(
        pool_size=10,
        max_overflow=20,
        pool_recycle=3600,
        pool_pre_ping=True,  # 移到这里
    )
```

**审查重点**：
- SQLite 分支现在是否真的没有任何 pool_* 参数？
- pool_pre_ping 在 aiosqlite + AsyncSession 下意义如何？

---

### 修改 9：m-3 修复 — hallucination_checker.py 金额正则单一来源

```python
# 修改前：_PATTERNS 和 _AMOUNT_RE 各自定义
_AMOUNT_RE = re.compile(r"\s*(\d+(?:\.\d+)?)\s*(亿元|亿|万元|万|元)\s*")
_PATTERNS = [
    ("金额", re.compile(r"\d+(?:\.\d+)?\s*(?:万元|亿元|元|万|亿)")),  # 重复定义
    ...
]

# 修改后：_PATTERNS 复用 _AMOUNT_RE
_AMOUNT_RE = re.compile(r"\s*(\d+(?:\.\d+)?)\s*(亿元|亿|万元|万|元)\s*")
_PATTERNS = [
    ("金额", _AMOUNT_RE),  # 单一来源
    ...
]
```

**审查重点**：
- `_AMOUNT_RE` 的 `finditer` 行为是否与原 `_PATTERNS` 中的金额正则一致？
- `_AMOUNT_RE` 有 `\s*` 前后缀，会导致 `match.group(0)` 含空白，`extract_facts` 里有 `value = match.group(0).strip()` 兜底，是否足够？
- 现在 `_PATTERNS` 里的金额 match 也能用 `_AMOUNT_RE.match()` 做归一化，逻辑是否一致？

---

## 测试环境 mock 说明

为避免测试做真实 DNS 解析导致不稳定，`tests/conftest.py` 加了 autouse fixture mock `is_safe_url` 和 `is_safe_url_async`：

```python
@pytest.fixture(autouse=True)
async def _reset_db_and_rate_limit(monkeypatch):
    ...
    async def _mock_safe_async(url: str) -> tuple[bool, str]:
        return True, ""

    def _mock_safe(url: str) -> tuple[bool, str]:
        return True, ""

    monkeypatch.setattr("app.utils.url_safety.is_safe_url", _mock_safe)
    monkeypatch.setattr("app.utils.url_safety.is_safe_url_async", _mock_safe_async)
    monkeypatch.setattr("app.core.scraper.is_safe_url", _mock_safe)  # scraper 顶部直接 import 的引用
```

**审查重点**：
- mock 是否覆盖所有 is_safe_url 调用点？还有哪些模块用了 is_safe_url 但没 patch？
- mock 后 SSRF 防护的测试覆盖度是否足够？是否需要单独的 SSRF 单元测试？

---

## 命题覆盖预估（请验证）

| 命题硬要求 | 当前覆盖 | 证据 |
|---|---|---|
| 1. LLM 意图解析 5 槽位 | ✅ | `app/llm/parser.py` |
| 2. ≥2 网站 + ≥1 登录态 | ⚠️ 部分 | ccgp+chinabidding 已有；qianlima 登录态采集待 Sol |
| 3. SimHash 去重 | ✅ | 三阶段批量去重 + 候选集复用 |
| 4. 5 字段汇总 + Word 命名 | ✅ | `app/report/docx_generator.py` |
| 5. cron 定时执行 | ✅ | `is_cron_due` + croniter |
| 6. 增量推送 | ⚠️ 部分 | PushLog + SQL NOT EXISTS 完整；push.py 仅日志占位，待 Sol 移植 email_sender |
| 7. 反幻觉校验 | ✅ | 金额/日期归一化比对 + 正则单一来源 |

---

## 输出格式要求

请按以下格式输出审查结果：

```
## 第五轮审查报告

### 评分（0-10）
- 代码质量：X.X（上轮 8.2）
- 命题覆盖：X.X（上轮 8.3）
- 企业级成熟度：X.X（上轮 7.3）

### Critical（必须修复）
- [C-X] 文件:行号 — 问题描述 + 修复建议

### Major（建议修复）
- [M-X] 文件:行号 — 问题描述 + 修复建议

### Minor（可选优化）
- [m-X] 文件:行号 — 问题描述 + 修复建议

### 第四轮问题修复验证
- C-1 tender_ingestor.py 拆分 — 是否真的 ≤ 300 行
- M-1 escape_like escape 参数 — 是否所有 LIKE 查询处都生效
- M-2 SSRF DNS 解析 — 是否能防 localtest.me 绕过
- M-4 循环内 flush — 是否改为批量 flush
- M-6 REPORT_OUTPUT_DIR 校验 — 是否补上
- M-7 collector 事务统一 — 是否一个 session 共用
- m-1/m-2/m-3 Minor — 是否清理

### 命题覆盖验证
- 哪些硬要求已达成
- 哪些未达成，差什么

### 总结
本轮修复整体评价
```

---

## 当前测试状态

```
90 passed, 30 warnings in 277.18s（4:37）
```

所有现有测试通过。本轮新增 `tender_utils.py` 和 `safe_contains/safe_like` 工具函数仍未补单元测试，请评估这是否影响命题覆盖判定。
