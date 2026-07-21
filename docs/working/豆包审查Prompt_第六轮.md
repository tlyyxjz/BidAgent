# 豆包代码审查 · 第六轮

## 背景

这是 ScrapeFlow 项目（超聚变命题 · 招投标信息聚合工具）的**第六轮审查**。

- **第五轮**：你给出 0 Critical + 3 Major + 3 Minor，评分 8.8/8.5/7.8
- **第六轮（本轮）**：GLM-5.2 修复了第五轮全部 6 个问题（3M + 3m）

请基于本轮**新增/修改的代码**做第六轮审查，重点关注：
1. 第五轮 3 个 Major 是否真正修复
2. SAVEPOINT 方案是否引入新问题
3. SSRF hostname 缓存是否有缓存失效/中毒风险
4. 修复是否引入新 bug

命题硬要求（6+1 项）：
1. LLM 意图解析 5 槽位（关键词/地区/金额/时间/来源）
2. ≥2 网站 + ≥1 登录态采集
3. SimHash 内容去重
4. 5 字段汇总 + Word 命名规则
5. cron 定时执行
6. 增量推送（已推送内容不重复）
7. 反幻觉校验（生成内容必须有原文支撑）

---

## 本轮修改清单（共 6 项）

### 修改 1：M-1 修复 — collector.py 用 SAVEPOINT 实现部分成功语义

**问题回顾**：第五轮报告称"循环中 rollback 会丢失前面所有平台已 add 的数据"。

**修复方案**：用 `db.begin_nested()` SAVEPOINT 包裹每个平台的入库，失败时只回滚当前平台：

```python
# app/scheduler/collector.py
async with AsyncSessionLocal() as db:
    for r in scrape_results:
        platform = r["platform"]
        if "error" in r:
            per_platform.append({"platform": platform, "error": r["error"]})
            continue

        # M-1 修复（第五轮）：用 SAVEPOINT 实现部分成功语义
        # - ingest_scrape_result 内部 flush 失败会抛 IntegrityError，事务进入 poisoned 状态
        # - 如果直接 rollback 会清掉前面平台已 add 的数据（原 bug）
        # - 如果不 rollback 后续平台操作会全部失败
        # - SAVEPOINT（begin_nested）只回滚当前平台，保留外层事务
        try:
            async with db.begin_nested():
                ingest = await ingest_scrape_result(
                    scrape_result=r["result"],
                    template=platform,
                    simhash_computer=compute_simhash,
                    db=db,
                )
            total_collected += ingest["total"]
            total_inserted += ingest["inserted"]
            ...
        except Exception as exc:  # noqa: BLE001
            # begin_nested 异常时 savepoint 已自动回滚，外层事务仍可用
            logger.exception("ingest failed platform=%s", platform)
            per_platform.append({"platform": platform, "error": str(exc)})

    # 所有平台统一 commit（部分成功语义：成功的平台数据落盘）
    await db.commit()
```

**审查重点**：
- `db.begin_nested()` 在 aiosqlite + SQLAlchemy 2.x async 下是否真的创建 SAVEPOINT？
- `begin_nested()` 上下文管理器退出时是 RELEASE SAVEPOINT（成功）还是 ROLLBACK TO SAVEPOINT（异常）？
- ingest_scrape_result 内部已经 `await db.flush()`，begin_nested 包裹后 flush 会触发 SAVEPOINT 内的写操作，异常时 SAVEPOINT 回滚是否能干净清理？
- 多个 SAVEPOINT 嵌套（SQLite SAVEPOINT 命名）是否会冲突？
- 测试环境能否验证：平台 1 入库成功 → 平台 2 故意触发 IntegrityError → 平台 3 入库成功 → 最终 commit 只有平台 1 + 3 的数据？

---

### 修改 2：M-2 修复 — SSRF page.route 加 hostname LRU 缓存 + document 严格校验

**问题回顾**：第五轮报告称"page.route 拦截所有请求都做 DNS 解析，30-50 个子资源导致 30-50 次 DNS 往返，抓取耗时翻倍"。

**修复方案**：
1. document 请求严格校验（防 302 重定向到内网）
2. 子资源请求用 hostname LRU 缓存（同域名只校验一次）
3. 缓存大小 64，LRU 淘汰策略

```python
# app/core/scraper.py
from urllib.parse import urlparse
from collections import OrderedDict

_hostname_cache: OrderedDict[str, tuple[bool, str]] = OrderedDict()
_HOSTNAME_CACHE_LIMIT = 64

def _cache_get(hostname: str) -> tuple[bool, str] | None:
    if hostname in _hostname_cache:
        result = _hostname_cache.pop(hostname)
        _hostname_cache[hostname] = result  # move to end (LRU)
        return result
    return None

def _cache_set(hostname: str, value: tuple[bool, str]) -> None:
    if hostname in _hostname_cache:
        _hostname_cache.pop(hostname)
    _hostname_cache[hostname] = value
    while len(_hostname_cache) > _HOSTNAME_CACHE_LIMIT:
        _hostname_cache.popitem(last=False)

async def _ssrf_guard(route):
    req_url = route.request.url
    try:
        hostname = urlparse(req_url).hostname or ""
    except Exception:
        hostname = ""
    hostname_lower = hostname.lower()

    # document 请求严格校验（防 302 重定向到内网）
    if route.request.resource_type == "document":
        safe, reason = await is_safe_url_async(req_url)
        if not safe:
            await route.abort("blockedbyclient")
            return
        # 缓存 hostname 校验结果，供子资源复用
        if hostname_lower:
            _cache_set(hostname_lower, (True, ""))
        await route.continue_()
        return

    # 子资源请求：用 hostname 缓存校验，未命中则做一次完整校验
    if hostname_lower:
        cached = _cache_get(hostname_lower)
        if cached is not None:
            if not cached[0]:
                await route.abort("blockedbyclient")
                return
            await route.continue_()
            return
        # 未命中缓存：做一次校验并缓存
        safe, reason = await is_safe_url_async(req_url)
        _cache_set(hostname_lower, (safe, reason))
        if not safe:
            await route.abort("blockedbyclient")
            return
    await route.continue_()

await page.route("**/*", _ssrf_guard)
```

**审查重点**：
- `_hostname_cache` 是闭包内局部变量，每次 `scrape()` 调用都新建一份，没有跨请求共享。这样设计对吗？还是应该移到类实例属性或模块级？
- **缓存中毒风险**：如果第一次访问 evil.com 的子资源时 DNS 解析到公网 IP（safe=True），缓存后 evil.com 切换 DNS 到 127.0.0.1，后续子资源请求会因缓存命中而放行。这个 TOCTOU 风险是否可接受？
- **缓存污染**：document 请求成功后强制写入 `(True, "")`，但如果后续 document 请求被 302 到内网呢？document 请求是严格校验的，应该没问题，但需要确认。
- LRU 实现是否正确？`pop(hostname)` + 重新赋值 = move-to-end，这个 SQLAlchemy OrderedDict 用法是否标准？
- 缓存大小 64 是否合理？过小会导致频繁 DNS 解析，过大可能缓存陈旧 IP
- `route.request.resource_type` 的可能取值有哪些？document/stylesheet/script/image/font/media/other？只拦截 document 是否足够防 SSRF？

---

### 修改 3：M-3 验证 — 全局 LIKE 查询覆盖度确认

**问题回顾**：第五轮报告称"safe_contains 只在 subscription.py 使用，tender.py 等模块的 LIKE 查询是否覆盖"。

**本轮验证**：用 grep 全局搜索 `.contains(` / `.like(` / `.ilike(`，结果如下：

```
app/scheduler/utils.py:48:    return column.like(escape_like(value), escape=LIKE_ESCAPE)
app/scheduler/utils.py:56:    return column.contains(escape_like(value), escape=LIKE_ESCAPE)
```

**唯一 LIKE 使用点**就是 `safe_like` / `safe_contains` 工具函数内部。

调用方：
- `app/scheduler/subscription.py` 两处 LIKE → 已用 `safe_contains`
- `app/api/tender.py` 两处 LIKE → 已用 `safe_contains`

其他 `.startswith(` / `.endswith(` 匹配都是 Python 字符串方法（非 SQL 查询）。

**结论**：M-3 覆盖完整，全项目 LIKE 查询 100% 走 safe_contains。

**审查重点**：
- 验证以上 grep 结果是否完整
- 是否有动态拼接 SQL 字符串的地方绕过了 safe_contains？（如 `text("... LIKE ...")`）
- 是否有 raw SQL 查询用了 LIKE？

---

### 修改 4：m-1 修复 — 删除 tender_utils.py 死代码 _hamming_distance

```python
# 删除前
def _hamming_distance(a: int, b: int) -> int:
    """计算两个 64 位 SimHash 的汉明距离。"""
    return bin(a ^ b).count("1")

# 删除后：tender_utils.py 只保留 5 个函数
# _hash_contact / _parse_decimal / _parse_datetime / _infer_platform / _build_tender
```

**审查重点**：tender_ingestor.py 是否真的没 import `_hamming_distance`？

---

### 修改 5：m-2 修复 — scraper.py import 提到顶部

```python
# 修改前（闭包内延迟 import）
async def _ssrf_guard(route):
    from app.utils.url_safety import is_safe_url_async  # 每次调用都查 sys.modules
    ...

# 修改后（顶部 import）
from app.utils.url_safety import is_safe_url, is_safe_url_async  # 顶部一次性 import

async def _ssrf_guard(route):
    safe, reason = await is_safe_url_async(req_url)  # 直接使用
    ...
```

**审查重点**：是否会导致循环 import？url_safety.py 是否 import 了 scraper.py？

---

### 修改 6：m-3 修复 — url_safety.py IP 检测逻辑去重

```python
# 修改前（两次 ip_address 解析）
blocked, reason = _is_ip_blocked(hostname_lower)  # 内部 ipaddress.ip_address(hostname)
if blocked:
    return False, reason
try:
    ipaddress.ip_address(hostname_lower)  # 重复解析
except ValueError:
    # 是域名，不是 IP
    blocked, reason = _check_dns_records(hostname_lower)

# 修改后（一次 ip_address 解析）
is_ip = False
try:
    ipaddress.ip_address(hostname_lower)
    is_ip = True
except ValueError:
    is_ip = False

if is_ip:
    blocked, reason = _is_ip_blocked(hostname_lower)
    if blocked:
        return False, reason
else:
    blocked, reason = _check_dns_records(hostname_lower)
    if blocked:
        return False, reason
```

**审查重点**：
- `_is_ip_blocked` 内部也会做 `ipaddress.ip_address(hostname)`，是否仍然是重复解析？
- 如果 hostname 是 IP，外层先解析一次判断 is_ip=True，内层 _is_ip_blocked 又解析一次，等于还是两次。是否应该重构 _is_ip_blocked 接收 ip 对象而非字符串？

---

## 命题覆盖预估（请验证）

| 命题硬要求 | 当前覆盖 | 证据 |
|---|---|---|
| 1. LLM 意图解析 5 槽位 | ✅ | `app/llm/parser.py` |
| 2. ≥2 网站 + ≥1 登录态 | ⚠️ 部分 | ccgp+chinabidding 已有；qianlima 登录态采集待 Sol 移植 session_manager |
| 3. SimHash 去重 | ✅ | 三阶段批量去重 + SAVEPOINT 事务保护 |
| 4. 5 字段汇总 + Word 命名 | ✅ | `app/report/docx_generator.py` |
| 5. cron 定时执行 | ✅ | `is_cron_due` + croniter |
| 6. 增量推送 | ⚠️ 部分 | PushLog + SQL NOT EXISTS 完整；push.py 仅日志占位，待 Sol 移植 email_sender |
| 7. 反幻觉校验 | ✅ | 金额/日期归一化比对 + 正则单一来源 |

---

## 输出格式要求

请按以下格式输出审查结果：

```
## 第六轮审查报告

### 评分（0-10）
- 代码质量：X.X（上轮 8.8）
- 命题覆盖：X.X（上轮 8.5）
- 企业级成熟度：X.X（上轮 7.8）

### Critical（必须修复）
- [C-X] 文件:行号 — 问题描述 + 修复建议

### Major（建议修复）
- [M-X] 文件:行号 — 问题描述 + 修复建议

### Minor（可选优化）
- [m-X] 文件:行号 — 问题描述 + 修复建议

### 第五轮问题修复验证
- M-1 collector rollback 语义 — SAVEPOINT 是否真正解决问题
- M-2 SSRF DNS 性能 — hostname 缓存是否有效
- M-3 LIKE 全局覆盖 — 验证 grep 结果是否完整
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
90 passed, 30 warnings in 277.33s（4:37）
```

所有现有测试通过。本轮新增的 SAVEPOINT 事务逻辑、SSRF hostname 缓存仍未补单元测试。
