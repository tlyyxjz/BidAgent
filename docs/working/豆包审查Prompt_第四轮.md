# 豆包代码审查 · 第四轮

## 背景

这是 ScrapeFlow 项目（超聚变命题 · 招投标信息聚合工具）的**第四轮审查**。

- **第一轮**：你给出 4 Critical + 8 Major + 9 Minor
- **第二轮**：你给出 8 个新引入问题
- **第三轮（本轮）**：GLM-5.2 修复了第二轮全部 8 个问题，并新增了几个模块（push.py / utils.py / url_safety.py），同时从早期完整版 `scrapeflow-complete` 借鉴了架构思路

请基于本轮**新增/修改的代码**做第四轮审查，重点关注：
1. 修复是否引入新 bug
2. 新模块是否达成命题硬要求
3. 是否存在之前没发现的隐患

命题硬要求（6+1 项）：
1. LLM 意图解析 5 槽位（关键词/地区/金额/时间/来源）
2. ≥2 网站 + ≥1 登录态采集
3. SimHash 内容去重
4. 5 字段汇总 + Word 命名规则
5. cron 定时执行
6. 增量推送（已推送内容不重复）
7. 反幻觉校验（生成内容必须有原文支撑）

---

## 本轮修改清单（共 11 项）

### 修改 1：`app/scheduler/subscription.py` 重写（360→294 行）

**目的**：拆分大文件，工具函数移到 `utils.py`，推送移到 `push.py`

```python
"""定时订阅 + 增量推送调度。

命题硬要求：
- 支持每日/每周定时推送（cron 表达式到期才推送）
- 已推送内容不重复推送（PushLog 表 + SQL NOT EXISTS 过滤）
"""

from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.config import settings
from app.llm.parser import parse_query
from app.llm.schemas import ParsedFilters
from app.models.database import AsyncSessionLocal
from app.models.subscription import (
    Subscription, PushLog, TRIGGER_IMMEDIATE, TRIGGER_SCHEDULED,
)
from app.models.tender import Tender
from app.report.docx_generator import generate_report
from app.scheduler.push import push_to_channels
from app.scheduler.utils import escape_like, is_cron_due, utc_now
from app.utils.logger import get_logger

logger = get_logger("scheduler")


async def trigger_subscription(subscription_id: int, *, force: bool = False) -> dict[str, Any]:
    """触发一次订阅推送。force=True 时跳过 cron 检查。"""
    async with AsyncSessionLocal() as db:
        sub = await db.get(Subscription, subscription_id)
        if not sub:
            return {"ok": False, "error": "subscription not found"}

        now = utc_now()
        if not force and not is_cron_due(sub.frequency_cron, sub.last_pushed_at, now):
            return {"ok": False, "skipped": "cron_not_due"}

        filters = await _resolve_filters(sub)
        unpushed = await _fetch_unpushed(db, sub, filters)
        if not unpushed:
            return {"ok": True, "pushed": 0, "reason": "no_new_tenders"}

        # C-2 修复：组装 source_texts {source_url: source_raw_text}
        source_texts: dict[str, str] = {}
        for t in unpushed:
            if t.source_url and t.source_raw_text:
                source_texts[t.source_url] = t.source_raw_text

        report_path = await generate_report(
            filters, items,
            job_id=f"sub_{subscription_id}",
            source_texts=source_texts or None,
        )

        push_logs = [
            PushLog(
                subscription_id=subscription_id,
                tender_id=t.id,
                trigger_type=TRIGGER_IMMEDIATE if force else TRIGGER_SCHEDULED,
            )
            for t in unpushed
        ]
        db.add_all(push_logs)  # 命题要求：批量 add_all 而非单个 add
        sub.last_pushed_at = now  # C-3：成功推送后更新时间戳
        await db.commit()

        await push_to_channels(
            subscription=sub,
            report_path=report_path,
            tender_count=len(unpushed),
        )
        return {"ok": True, "pushed": len(unpushed), "report": str(report_path)}


async def _fetch_unpushed(db, sub, filters):
    """SQL NOT EXISTS 过滤已推送内容。"""
    stmt = select(Tender).where(
        Tender.source_platform == sub.target_platform or True,
    )
    # 关键词 LIKE（escape 防 % _ 注入）
    if filters.keywords:
        kw = escape_like(filters.keywords[0])
        stmt = stmt.where(Tender.project_name.like(f"%{kw}%"))
    # 增量过滤：NOT EXISTS in PushLog
    stmt = stmt.where(
        ~PushLog.__table__.c.tender_id.in_(
            select(PushLog.tender_id).where(PushLog.subscription_id == sub.id)
        )
    )
    result = await db.execute(stmt)
    return result.scalars().all()
```

**审查重点**：
- `~PushLog.__table__.c.tender_id.in_(select(...))` 这个 NOT EXISTS 写法 SQLAlchemy 是否正确？
- `sub.last_pushed_at = now` 在 `db.add_all(push_logs)` 之后是否有顺序问题？
- `or True` 这种 fallback 是否会被 SQLAlchemy 解析为恒真？

---

### 修改 2：`app/scheduler/utils.py` 新建

```python
"""调度工具函数。"""
from __future__ import annotations
from datetime import datetime, timezone
from croniter import croniter


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def escape_like(value: str) -> str:
    """转义 LIKE 查询的 % 和 _ 防通配符注入。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def is_cron_due(cron_expr: str, last_run: datetime | None, now: datetime) -> bool:
    """判断 cron 表达式自上次运行后是否到了触发时间。

    **None 语义**：last_run=None 表示"从未推送过"，本函数返回 False。
    调用方若希望新订阅立即触发，应在调用前用 `sub.created_at` 兜底；
    若希望"立即触发一次"，调用方应直接调 trigger_subscription(force=True)。
    """
    if not cron_expr:
        return False
    if cron_expr.startswith("once:"):
        return True
    try:
        if last_run is None:
            return False
        base = last_run
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
        itr = croniter(cron_expr, base)
        next_run = itr.get_next(datetime)
        return next_run <= now
    except Exception:
        return False
```

**审查重点**：
- `escape_like` 转义后是否需要配套 `escape` 参数？SQLAlchemy `like(pattern, escape="\\")` 是否漏了？
- `last_run is None → False` 会让新订阅永远不触发，调用方必须自己兜底，这个契约是否清晰？

---

### 修改 3：`app/scheduler/push.py` 新建（占位）

```python
"""推送通道（邮件/Webhook 占位，待 Sol 接入 email_sender.py）。"""
from __future__ import annotations
from pathlib import Path
from typing import Any
from app.models.subscription import Subscription
from app.utils.logger import get_logger

logger = get_logger("push")


async def push_to_channels(
    subscription: Subscription,
    report_path: Path,
    tender_count: int,
) -> dict[str, Any]:
    """将报告推送到订阅配置的通道。
    
    当前实现：仅日志占位。
    TODO（Sol 移植）：接入 app/core/email_sender.py 实现 SMTP 真实邮件推送。
    """
    logger.info(
        "push placeholder: sub_id=%s tenders=%d report=%s",
        subscription.id, tender_count, report_path,
    )
    return {"channel": "log", "ok": True}
```

**审查重点**：占位是否会导致命题"增量推送"被判定不达标？

---

### 修改 4：`app/scheduler/collector.py` 重写（两阶段架构）

```python
"""采集调度器。
新-2 修复：并发抓取 + 串行入库，避免 SQLite 并发写锁。
"""
from __future__ import annotations
import asyncio
from typing import Any
from app.config import settings
from app.core.scraper import Scraper
from app.llm.schemas import ParsedFilters, ScrapeRequest
from app.models.database import AsyncSessionLocal
from app.processors.tender_ingestor import ingest_scrape_result
from app.utils.logger import get_logger

logger = get_logger("collector")

PLATFORMS = ["ccgp", "chinabidding"]  # m-8: 移除 ggzy（无登录态采集价值）


async def _scrape_one_platform(platform, filters, semaphore):
    """新-2：只负责抓取，不入库。"""
    async with semaphore:
        scraper = Scraper()
        request = ScrapeRequest(
            url=settings.PLATFORM_URLS[platform],
            template=platform,
            filters=filters,
        )
        result = await scraper.scrape(request)
        return {"platform": platform, "result": result}


async def collect_new_tenders(sub, filters: ParsedFilters) -> dict[str, Any]:
    """采集所有平台新标讯。
    
    阶段 1：并发抓取所有平台（Semaphore 限并发=3）
    阶段 2：串行入库（避免 SQLite WAL 模式下的写锁竞争）
    """
    semaphore = asyncio.Semaphore(3)
    tasks = [_scrape_one_platform(p, filters, semaphore) for p in PLATFORMS]
    scrape_results = await asyncio.gather(*tasks, return_exceptions=False)

    inserted = 0
    duplicates = 0
    async with AsyncSessionLocal() as db:
        for r in scrape_results:
            if isinstance(r, Exception):
                logger.warning("scrape failed: %s", r)
                continue
            stats = await ingest_scrape_result(
                scrape_result=r["result"],
                source_url=r["result"].source_url,
                source_platform=r["platform"],
                db=db,
            )
            inserted += stats["inserted"]
            duplicates += stats["duplicates"]
        await db.commit()
    return {"inserted": inserted, "duplicates": duplicates}
```

**审查重点**：
- `asyncio.gather(return_exceptions=False)` 一个失败会怎样？是否应该改成 `True` + 单独处理？
- `db` 在循环外创建，循环内多次 `ingest_scrape_result` 共用，事务边界是否正确？

---

### 修改 5：`app/core/scraper.py` 加 SSRF 重定向防护

```python
async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
    """M-7 + 新-4：初始 URL 校验 + page.route 拦截重定向到内网。"""
    from app.utils.url_safety import is_safe_url
    
    safe, reason = is_safe_url(request.url)
    if not safe:
        raise ScrapeError(f"URL 不安全: {reason}")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=settings.PLAYWRIGHT_HEADLESS)
        context = await browser.new_context()
        page = await context.new_page()
        
        # 新-4：拦截所有请求，重定向到内网时 abort
        async def _ssrf_guard(route):
            req_url = route.request.url
            safe, reason = is_safe_url(req_url)
            if not safe:
                await route.abort("blockedbyclient")
            else:
                await route.continue_()
        
        await page.route("**/*", _ssrf_guard)
        await page.goto(request.url, wait_until="domcontentloaded", timeout=30000)
        # ... 后续解析
```

**审查重点**：
- `page.route("**/*", _ssrf_guard)` 是否会拦截 `page.goto` 自身？需不需要排除主请求？
- `is_safe_url` 在 route 回调中每次调用，性能是否可接受？是否有缓存必要？
- 重定向到内网时 `route.abort`，但 Playwright 的 `page.goto` 会抛异常，是否需要 try/except 包装？

---

### 修改 6：`app/utils/url_safety.py` 新建

```python
"""URL 安全校验，防 SSRF。"""
from __future__ import annotations
import ipaddress
import socket
from urllib.parse import urlparse


_BLOCKED_HOSTS = {"localhost", "metadata.google.internal", "metadata"}

def is_safe_url(url: str) -> tuple[bool, str]:
    """校验 URL 是否安全（非内网/非保留地址）。
    
    Returns:
        (True, "") 表示安全
        (False, reason) 表示不安全
    """
    try:
        parsed = urlparse(url)
    except Exception as e:
        return False, f"parse_error: {e}"
    
    if parsed.scheme not in ("http", "https"):
        return False, f"scheme_not_allowed: {parsed.scheme}"
    
    hostname = parsed.hostname
    if not hostname:
        return False, "no_hostname"
    
    if hostname in _BLOCKED_HOSTS:
        return False, f"blocked_host: {hostname}"
    
    try:
        # 解析所有 A 记录（一个域名可能多个 IP）
        addrs = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False, f"dns_resolve_failed: {hostname}"
    
    for addr in addrs:
        ip = ipaddress.ip_address(addr[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False, f"private_ip: {ip}"
    
    return True, ""
```

**审查重点**：
- TOCTOU 风险：DNS 解析时安全，但实际请求时 DNS rebinding 到内网 IP，这个能否防范？
- `socket.getaddrinfo` 是同步阻塞调用，在 async 路由中是否会阻塞事件循环？应该用 `asyncio.to_thread` 包装吗？
- IPv6 mapped IPv4 地址（如 `::ffff:127.0.0.1`）能否绕过校验？

---

### 修改 7：`app/models/database.py` 加 SQLite 迁移 + WAL

```python
"""数据库初始化。新-1：SQLite 轻量迁移；新-2：WAL + busy_timeout。"""

_SQLITE_MIGRATIONS: list[tuple[str, str, str]] = [
    ("tenders", "source_raw_text", "TEXT"),
]


async def _run_sqlite_migrations() -> None:
    """检测并添加缺失的列（SQLite 不支持完整 ALTER，仅 ADD COLUMN）。"""
    if not settings.DATABASE_URL.startswith("sqlite"):
        return
    async with engine.begin() as conn:
        for table, column, col_def in _SQLITE_MIGRATIONS:
            result = await conn.execute(text(f"PRAGMA table_info({table})"))
            existing_cols = {row[1] for row in result.fetchall()}
            if column not in existing_cols:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}"))
                logger.info("migration: added %s.%s", table, column)


async def _set_sqlite_pragmas() -> None:
    """新-2：WAL 模式 + busy_timeout 缓解并发写锁。"""
    if not settings.DATABASE_URL.startswith("sqlite"):
        return
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.execute(text("PRAGMA busy_timeout=30000"))
        await conn.execute(text("PRAGMA synchronous=NORMAL"))


async def init_database() -> None:
    """启动时调用：设置 PRAGMA → 建表 → 迁移。"""
    await _set_sqlite_pragmas()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _run_sqlite_migrations()


# engine 初始化
if settings.DATABASE_URL.startswith("sqlite"):
    _connect_args = {"check_same_thread": False, "timeout": 30}
else:
    _connect_args = {}

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args=_connect_args,
    pool_size=10,
    max_overflow=20,
    pool_recycle=3600,
)
```

**审查重点**：
- `PRAGMA journal_mode=WAL` 是数据库级别还是连接级别？每次启动都设置是否合理？
- SQLite 的 `pool_size=10, max_overflow=20` 在 aiosqlite 下是否生效？SQLite 本身是单写的，多连接写还是会冲突
- `_run_sqlite_migrations` 用 `f"ALTER TABLE {table}"` 拼接，虽然 table 来自常量，但格式是否应该用绑定参数？

---

### 修改 8：`app/main.py` 加 `_validate_data_dir`

```python
def _validate_data_dir(configured_path: str, name: str) -> None:
    """校验配置的目录路径必须在 data/ 范围内，防路径穿越。"""
    target = Path(configured_path).resolve()
    data_root = DATA_DIRECTORY.resolve()
    try:
        target.relative_to(data_root)
    except ValueError:
        raise RuntimeError(
            f"{name}='{configured_path}' 不在 data/ 目录范围内，"
            f"实际路径={target} data_root={data_root}"
        ) from None
    target.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    DATA_DIRECTORY.mkdir(parents=True, exist_ok=True)
    _validate_data_dir(settings.REPORT_OUTPUT_DIR, "REPORT_OUTPUT_DIR")
    _validate_data_dir(settings.ATTACHMENT_DIR, "ATTACHMENT_DIR")
    _validate_data_dir(settings.COOKIE_DIR, "COOKIE_DIR")
    await init_database()  # 新-1/新-2：替代直接 create_all
    yield
    await engine.dispose()
```

**审查重点**：
- `_validate_data_dir` 在 `DATA_DIRECTORY.mkdir` 之后调用，但 `DATA_DIRECTORY` 是硬编码 `Path("data")`，如果用户改 `REPORT_OUTPUT_DIR` 为绝对路径如 `D:/reports`，会被 `resolve()` 解析后相对 `data/` 失败，是否合理？
- 路径穿越防护是否足够？符号链接能否绕过？

---

### 修改 9：`app/processors/tender_ingestor.py` 三阶段批量去重

```python
async def ingest_scrape_result(
    scrape_result, source_url, source_platform, db
) -> dict[str, int]:
    """新-5：三阶段批量处理，根治 N+1。"""
    items = scrape_result.items
    if not items:
        return {"inserted": 0, "duplicates": 0}
    
    # 阶段 1：批量计算所有 simhash（CPU 密集 → to_thread 并发）
    simhash_values: list[int | None] = []
    for item in items:
        text = " ".join([
            item.get("project_name", ""),
            item.get("core_content", ""),
            item.get("bid_number", ""),
        ])
        if not text.strip():
            simhash_values.append(None)
            continue
        sh = await asyncio.to_thread(simhash_computer, text)
        simhash_values.append(sh)
    
    # 阶段 2：一次查询候选集（按 source_platform 过滤，限制 2000 条）
    candidates: list[tuple[int, int]] = []
    if any(sh is not None for sh in simhash_values):
        stmt = select(Tender.id, Tender.simhash).where(Tender.simhash.is_not(None))
        if source_platform:
            stmt = stmt.where(Tender.source_platform == source_platform)
        stmt = stmt.order_by(Tender.id.desc()).limit(2000)
        result = await db.execute(stmt)
        candidates = [(row.id, row.simhash) for row in result.all()]
    
    # 阶段 3：Python 层批量比对 + 入库
    inserted, duplicates = 0, 0
    inserted_ids: list[int] = []
    for idx, item in enumerate(items):
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
        await db.flush()  # 拿到 tender.id
        inserted_ids.append(tender.id)
        inserted += 1
        if simhash_value is not None:
            candidates.append((tender.id, simhash_value))  # 同批次去重
    
    return {"inserted": inserted, "duplicates": duplicates}
```

**审查重点**：
- `await db.flush()` 在循环内，N 条数据会触发 N 次 flush，性能是否可接受？
- 候选集 limit 2000 是否足够？数据量超过 2000 后旧记录的重复能否被检测到？
- 同批次去重 `candidates.append((tender.id, simhash_value))` 在 flush 后 ID 已生成，逻辑是否正确？

---

### 修改 10：`app/processors/hallucination_checker.py` 金额/日期正则增强

```python
# 新-3 修复：金额必须带单位（万元/亿元/亿/万/元），纯数字不归一化
_AMOUNT_RE = re.compile(r"\s*(\d+(?:\.\d+)?)\s*(亿元|亿|万元|万|元)\s*")

def _normalize_amount(value: str) -> str | None:
    m = _AMOUNT_RE.match(value)
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2)
    if "亿" in unit:
        num *= 100_000_000
    elif "万" in unit:
        num *= 10_000
    if num == int(num):
        return str(int(num))
    return f"{num:.2f}".rstrip("0").rstrip(".")

# 新-6 修复：日期支持点号分隔（2024.05.01）
_DATE_RE = re.compile(r"\s*(\d{4})[年\-/.](\d{1,2})[月\-/.](\d{1,2})日?\s*")

def _normalize_date(value: str) -> str | None:
    m = _DATE_RE.match(value)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return datetime(y, mo, d).strftime("%Y-%m-%d")
    except ValueError:
        return None

def _fact_in_source(fact: str, source: str) -> bool:
    """事实是否在原文中（归一化后比对）。"""
    for _, pattern in [("金额", _AMOUNT_RE), ("日期", _DATE_RE)]:
        fact_match = pattern.match(fact)
        if fact_match:
            for src_match in pattern.finditer(source):
                if _normalize_amount(fact) == _normalize_amount(src_match.group(0)) if pattern == _AMOUNT_RE \
                else _normalize_date(fact) == _normalize_date(src_match.group(0)):
                    return True
    return fact in source  # fallback 字面匹配
```

**审查重点**：
- `_fact_in_source` 的三元嵌套表达式 `if pattern == _AMOUNT_RE else` 是否可读？应该重构吗？
- `_AMOUNT_RE.match` 只匹配字符串**开头**，如果金额在句子中间（如"预算100万元"）会漏匹配，应该用 `search` 吗？
- `_normalize_amount` 返回 `"100"` 对应原文 "100万元"，但原文中可能是 "1e6" 或 "100.00万元"，归一化比对是否覆盖？

---

### 修改 11：`app/processors/simhash.py` 清理冗余

```python
"""SimHash 64位自实现。新-7：删除 compute_simhash_async，统一用 asyncio.to_thread。"""
import jieba
from collections.abc import Iterable

SIMHASH_BITS = 64
SIMHASH_HAMMING_THRESHOLD = 3


def _jieba_tokenizer(text: str) -> list[str]:
    """jieba 分词（m-2：从 lambda 改为函数，便于 pickle）。"""
    return [w for w in jieba.cut(text) if w.strip()]


def compute_simhash(text: str) -> int:
    """计算 64 位 SimHash。"""
    tokens = _jieba_tokenizer(text)
    if not tokens:
        return 0
    weights = [0] * SIMHASH_BITS
    for token in tokens:
        h = _hash64(token)
        for i in range(SIMHASH_BITS):
            if h & (1 << i):
                weights[i] += 1
            else:
                weights[i] -= 1
    return sum(1 << i for i in range(SIMHASH_BITS) if weights[i] > 0)


def find_duplicate_in_iter(
    target: int, candidates: Iterable[tuple[int, int]], threshold: int = SIMHASH_HAMMING_THRESHOLD
) -> tuple[int, int] | None:
    """在候选集中找重复。返回 (id, simhash) 或 None。"""
    for cid, csh in candidates:
        if bin(target ^ csh).count("1") <= threshold:
            return cid, csh
    return None
```

**审查重点**：
- `SIMHASH_HAMMING_THRESHOLD = 3` 在 64-bit 下是否过于严格？实测误判率多少？
- `_hash64` 用 Python 内置 `hash()` 还是 `hashlib.md5`？前者跨进程不稳定
- `find_duplicate_in_iter` O(N) 扫描，数据量大时性能堪忧，是否该上 LSH？

---

## 命题覆盖预估（请验证）

| 命题硬要求 | 当前覆盖 | 证据 |
|---|---|---|
| 1. LLM 意图解析 5 槽位 | ✅ | `app/llm/parser.py` |
| 2. ≥2 网站 + ≥1 登录态 | ⚠️ 部分 | ccgp+chinabidding 已有；qianlima 登录态采集待 Sol 实现 |
| 3. SimHash 去重 | ✅ | `app/processors/simhash.py` + `tender_ingestor.py` |
| 4. 5 字段汇总 + Word 命名 | ✅ | `app/report/docx_generator.py` |
| 5. cron 定时执行 | ✅ | `app/scheduler/subscription.py` + `is_cron_due` |
| 6. 增量推送 | ⚠️ 部分 | PushLog 已实现，但 `push.py` 仅日志占位，邮件推送待 Sol 移植 |
| 7. 反幻觉校验 | ✅ | `app/processors/hallucination_checker.py` |

---

## 输出格式要求

请按以下格式输出审查结果：

```
## 第四轮审查报告

### 评分（0-10）
- 代码质量：X.X
- 命题覆盖：X.X
- 企业级成熟度：X.X

### Critical（必须修复）
- [C-X] 文件:行号 — 问题描述 + 修复建议

### Major（建议修复）
- [M-X] 文件:行号 — 问题描述 + 修复建议

### Minor（可选优化）
- [m-X] 文件:行号 — 问题描述 + 修复建议

### 命题覆盖验证
- 哪些硬要求已达成
- 哪些未达成，差什么

### 总结
本轮修复是否引入新问题的整体评价
```

---

## 当前测试状态

```
90 passed, 30 warnings in 335.42s（5:35）
```

所有现有测试通过。本轮新增/修改的代码**未补单元测试**，请评估这是否影响命题覆盖判定。
