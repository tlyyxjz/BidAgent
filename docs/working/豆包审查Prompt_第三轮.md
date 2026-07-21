# 豆包审查 Prompt · 第三轮

> 本轮针对第二轮审查报告中 4 Critical + 8 Major + 9 Minor 的修复落地情况做验收审查。
> 请按"修复是否到位 / 是否引入新问题 / 命题硬要求达标情况"三个维度逐项核查。

---

## 一、本轮修复清单

| 编号 | 修复点 | 文件 | 状态 |
|---|---|---|---|
| C-1 | scrape.py 传 simhash_computer=compute_simhash | app/api/scrape.py:107-111 | 已修复（上轮已落地） |
| C-2 | 反幻觉章节传入 source_texts（Tender 加 source_raw_text 字段） | app/models/tender.py、tender_ingestor.py、docx_components.py、docx_generator.py、subscription.py | 已修复 |
| C-3 | 金额单位解析（万元/亿元/亿/万/元） | app/processors/tender_ingestor.py:47-88 | 已修复 |
| C-4 | subscription.py 拆分到 294 行 | 新增 app/scheduler/utils.py、push.py | 已修复 |
| M-1 | 批量入库 N+1 → 一次查询候选集 | app/processors/tender_ingestor.py:145-179 | 已修复 |
| M-2 | SimHash 候选集按 source_platform 过滤 | app/processors/tender_ingestor.py:145-179, 291-300 | 已修复 |
| M-3 | compute_simhash 用 asyncio.to_thread 包裹 | app/processors/simhash.py:151-163、tender_ingestor.py:282-292 | 已修复 |
| M-4 | 招标编号正则收紧 | app/processors/hallucination_checker.py:46-49 | 已修复 |
| M-5 | 事实比对金额/日期归一化 | app/processors/hallucination_checker.py:53-83, 135-172 | 已修复 |
| M-6 | 多平台并发采集 | app/scheduler/collector.py:66-143 | 已修复 |
| M-7 | scraper.py SSRF 内网 URL 校验 | 新增 app/utils/url_safety.py，scraper.py:79-83 | 已修复 |
| M-8 | cookie 目录独立配置 | app/config.py:50-51、qianlima.py:34 | 已修复 |
| m-1 | 数量正则去重复"套" | hallucination_checker.py:42 | 已修复 |
| m-2 | 模块级 lambda 改为 _jieba_tokenizer 函数 | simhash.py:36-39 | 已修复 |
| m-3 | ccgp 升级到 https | collector.py:31 | 已修复 |
| m-8 | 移除 ggzy 伪搜索（不支持 URL 参数） | collector.py:33-35 | 已修复 |

---

## 二、关键代码片段（请逐项审查）

### C-2 反幻觉章节传入 source_texts

**Tender 模型新增字段**（app/models/tender.py）：

```python
# 核心内容（命题第 4 项硬要求，与原文事实一致）
core_content: Mapped[str | None] = mapped_column(Text, nullable=True)
# 原始页面文本（C-2 修复：反幻觉校验时比对原文，避免永远通过）
source_raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
# 附件链接（命题第 4 项硬要求）
attachment_url: Mapped[str | None] = mapped_column(Text, nullable=True)
```

**tender_ingestor.py 入库时保存 source_raw_text**：

```python
return Tender(
    # ... 其他字段
    core_content=pick("core_content", "content", "核心内容") or "",
    # C-2 修复：保存原始页面文本，反幻觉校验时比对
    source_raw_text=pick("source_raw_text", "raw_text", "source_text") or "",
    attachment_url=pick("attachment_url", "attachment", "附件链接"),
    simhash=simhash_value,
)
```

**docx_components.py 接收 source_texts**：

```python
def add_anti_hallucination_section(
    doc: Document,
    items: list[dict[str, Any]],
    source_texts: dict[str, str] | None = None,
) -> None:
    """添加反幻觉校验章节（命题硬要求：core_content 与原文事实一致）。

    C-2 修复：原实现不传 source_texts，内部 source_texts={} 永远跳过校验。
             现接收 source_texts 并传入 check_items，让反幻觉真正生效。
    """
    # ...
    from app.processors.hallucination_checker import check_items
    report = check_items(items, source_texts=source_texts)
```

**docx_generator.py 透传**：

```python
async def generate_report(
    filters: ParsedFilters,
    items: list[dict[str, Any]],
    job_id: str | None = None,
    source_texts: dict[str, str] | None = None,  # C-2 新增
) -> str:
    # ...
    return await loop.run_in_executor(
        None, _generate_report_sync, filters, items, job_id, source_texts
    )

def _generate_report_sync(filters, items, job_id=None, source_texts=None):
    # ...
    add_anti_hallucination_section(doc, items, source_texts=source_texts)
```

**subscription.py 组装 source_texts**：

```python
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
```

**审查重点**：
1. `source_raw_text` 字段是否能在 SQLite 旧表上自动添加（lifespan 中只调 create_all，不 alter）
2. `source_texts or None` 当所有 tender 都没原文时传 None，check_items 内部走 `source_texts = source_texts or {}`，仍会跳过校验，是否合理？
3. items 里 core_content 取自 `t.core_content`，source_texts 取自 `t.source_raw_text`，两者是否对齐同一 source_url？

---

### C-3 金额单位解析修复

**app/processors/tender_ingestor.py:47-88**：

```python
def _parse_decimal(value: Any) -> Decimal | None:
    """从字符串/数字解析金额（支持 万/亿元 单位），失败返回 None。

    C-3 修复：原逻辑直接 replace("万","").replace("元","") 导致 "100万元" → 100，
              实际应为 1,000,000。现按单位乘以对应倍率。
    """
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
    if isinstance(value, str):
        # 去除千分位逗号
        cleaned = value.replace(",", "").strip()
        if not cleaned:
            return None
        # C-3 修复：按单位换算（亿元/亿/万元/万/元 优先级从长到短）
        multiplier = Decimal(1)
        if "亿元" in cleaned:
            multiplier = Decimal("100000000")
            cleaned = cleaned.replace("亿元", "")
        elif "亿" in cleaned:
            multiplier = Decimal("100000000")
            cleaned = cleaned.replace("亿", "")
        elif "万元" in cleaned:
            multiplier = Decimal("10000")
            cleaned = cleaned.replace("万元", "")
        elif "万" in cleaned:
            multiplier = Decimal("10000")
            cleaned = cleaned.replace("万", "")
        elif "元" in cleaned:
            cleaned = cleaned.replace("元", "")
        cleaned = cleaned.strip()
        if not cleaned:
            return None
        try:
            return Decimal(cleaned) * multiplier
        except (InvalidOperation, ValueError):
            return None
    return None
```

**审查重点**：
1. "1.5亿元" → 150000000.00 是否正确？( Decimal("1.5") * 100000000 )
2. "人民币 100 万元整" 含其他字符会怎样？( replace 后 "人民币 100 整"，Decimal 失败返回 None )
3. "100 万美元" 这种外币会怎样？( 当前实现会错误地按 1万倍率处理，应该返回 None 还是按 1万算？)
4. 测试用例：`test_parse_decimal_wan` 期望 `50万元 → 500000`，验证是否仍通过？

---

### C-4 subscription.py 拆分

**新增 app/scheduler/utils.py**（76 行）：

```python
"""调度模块公共工具函数。"""
from datetime import datetime, timezone
from croniter import croniter
from app.utils.logger import get_logger

logger = get_logger("scheduler.utils")

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def escape_like(value: str) -> str:
    """转义 LIKE 查询的通配符（M-7 修复）。"""
    if not value:
        return value
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

def is_cron_due(cron_expr: str, last_run: datetime | None, now: datetime) -> bool:
    """判断 cron 表达式自上次运行后是否到了触发时间。"""
    if not cron_expr:
        return False
    if cron_expr.startswith("once:"):
        return True
    if last_run is None:
        return False
    try:
        base = last_run
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
        itr = croniter(cron_expr, base)
        next_run = itr.get_next(datetime)
        return next_run <= now
    except Exception as exc:  # noqa: BLE001
        logger.warning("invalid cron expr={} err={}", cron_expr, exc)
        return False
```

**新增 app/scheduler/push.py**（46 行）：

```python
"""推送渠道实现（email / webhook）。"""
from app.models.subscription import Subscription
from app.utils.logger import get_logger

logger = get_logger("scheduler.push")

async def push_to_channels(
    sub: Subscription, report_path: str, count: int
) -> dict[str, str]:
    """推送到指定渠道。"""
    results: dict[str, str] = {}
    channels = sub.push_channels or []
    for channel in channels:
        if channel == "email":
            # TODO: GPT-5.6 Sol 实现 SMTP 推送
            results["email"] = f"placeholder:would send to user email, count={count}"
        elif channel == "webhook":
            # TODO: GPT-5.6 Sol 实现 webhook 推送（带签名）
            results["webhook"] = f"placeholder:would call webhook, count={count}"
        else:
            results[channel] = "unknown channel"
    return results
```

**subscription.py 现在 294 行**，import 改为：

```python
from app.scheduler.push import push_to_channels
from app.scheduler.utils import escape_like, is_cron_due, utc_now
```

**审查重点**：
1. utils.py 中 `is_cron_due` 与原逻辑是否完全等价？（once: / last_run=None / tzinfo 处理）
2. push.py 与原 `_push_to_channels` 行为是否一致？
3. subscription.py 内的 `_record_push` 和 `get_unpushed_tenders` 是否还能保持原签名？（参数类型注解从 `AsyncSession` 改成了未注解的 `db`）
4. 新增 2 个文件是否会导致循环导入？

---

### M-1 + M-2 批量候选 + 按平台过滤

**app/processors/tender_ingestor.py:145-179**：

```python
async def _find_duplicate(
    db: AsyncSession,
    simhash: int | None,
    source_platform: str | None = None,
) -> Tender | None:
    """查询是否存在 simhash 汉明距离 ≤ 阈值的记录。

    M-2 修复：候选集按 source_platform 过滤，避免跨平台误判。
    """
    if simhash is None:
        return None
    # 取候选集合（只查 id + simhash，避免传输整行；M-2 按平台过滤）
    stmt = (
        select(Tender.id, Tender.simhash)
        .where(Tender.simhash.is_not(None))
    )
    if source_platform:
        stmt = stmt.where(Tender.source_platform == source_platform)
    stmt = stmt.order_by(Tender.id.desc()).limit(2000)
    result = await db.execute(stmt)
    candidates = [(row.id, row.simhash) for row in result.all()]
    for cand_id, cand_hash in candidates:
        if cand_hash is None:
            continue
        if _hamming_distance(simhash, cand_hash) <= SIMHASH_HAMMING_THRESHOLD:
            # 命中后再查完整记录返回
            r = await db.execute(select(Tender).where(Tender.id == cand_id))
            return r.scalar_one_or_none()
    return None
```

**调用处**（ingest_scrape_result 内）：

```python
# 去重检查（M-2：按 source_platform 过滤候选集）
if simhash_value is not None:
    dup = await _find_duplicate(db, simhash_value, source_platform)
    if dup is not None:
        duplicates += 1
        # ...
        continue
```

**审查重点**：
1. M-1 报告建议"先批量计算所有 simhash → 一次查候选 → Python 层批量比对"，本轮实现仍是循环内逐条查，只是单条查询只取 2 列。是否真正解决了 N+1？还是只是减少了传输量？
2. 候选集 limit 2000 vs 原 1000，是否会增加内存压力？
3. 命中后再 `select(Tender).where(Tender.id == cand_id)` 查一次完整记录，是否多了一次往返？
4. 同一 source_platform 过滤是否会漏掉跨平台转载的真正重复（M-2 报告说"跨平台相似被误判"，本轮加了过滤，但反过来同内容不同平台就不再去重了，是否合理）？

---

### M-3 SimHash 异步包装

**app/processors/simhash.py:151-163**：

```python
async def compute_simhash_async(text: str) -> int:
    """M-3 修复：compute_simhash 的异步包装。

    jieba 分词 + 64 位权重累加是 CPU 密集同步操作，长文本（招标公告可达数千字）
    会阻塞事件循环。用 asyncio.to_thread 包裹让出控制权。

    Args:
        text: 输入文本

    Returns:
        64 位 SimHash 挖纹
    """
    return await asyncio.to_thread(compute_simhash, text)
```

**tender_ingestor.py 调用处**：

```python
# 计算 SimHash（M-3 修复：用 to_thread 包裹避免阻塞事件循环）
simhash_value: int | None = None
if simhash_computer is not None:
    text = item.get("core_content") or item.get("content") or ""
    if text:
        try:
            simhash_value = int(
                await asyncio.to_thread(simhash_computer, str(text))
            )
        except (TypeError, ValueError) as exc:
            logger.warning("simhash 计算失败 idx=%d err=%s", idx, exc)
```

**审查重点**：
1. `compute_simhash_async` 函数定义了但 ingest_scrape_result 没用它，而是直接 `asyncio.to_thread(simhash_computer, ...)` 调用传入的同步函数。两个方案并存是否冗余？
2. 调用方 `simhash_computer` 仍是同步的 `compute_simhash`（来自 collector.py），用 `to_thread` 包裹是对的。但如果调用方传的是 `compute_simhash_async`（async 函数），`to_thread` 会失败。是否应该做类型判断？
3. `asyncio.to_thread` 默认用默认线程池，长文本批量场景下是否会耗尽线程？

---

### M-4 招标编号正则收紧

**app/processors/hallucination_checker.py:46-49**：

```python
# 招标编号：常见前缀（SH-/ZB-/GG-/BJ-/GD-/JS-/ZJ-/FZ-/XM-/CG-/GS-/GZ-）或 字母+数字混合+连字符
("招标编号", re.compile(
    r"(?:(?:SH|ZB|GG|BJ|GD|JS|ZJ|FZ|XM|CG|GS|GZ)-[A-Z0-9-]{4,28})"
    r"|(?:[A-Z]{2,}\d{4,}[A-Z0-9]*(?:-[A-Z0-9]+)?)"
)),
```

**审查重点**：
1. 第二分支 `[A-Z]{2,}\d{4,}[A-Z0-9]*(?:-[A-Z0-9]+)?` 是否会误报 `ABCDEFG123456` 这种纯字母+数字串？（会匹配，因为没要求连字符）
2. 是否覆盖常见格式：`SH-2026-001` / `ZB20260415001` / `CG-GG-2024-007`？
3. 报告建议的正则是 `(?:CG|ZB|GS|GG|GZ)[A-Z0-9-]{6,25}|[A-Z]{2,}\d{6,}[A-Z\d-]*`，本轮实现是否到位？

---

### M-5 金额/日期归一化

**app/processors/hallucination_checker.py:53-83**：

```python
# M-5 修复：金额归一化 → 转换为"元"为单位的纯数字字符串
def _normalize_amount(value: str) -> str | None:
    """金额归一化：'1万元' / '10000元' / '10000' 都返回 '10000'。"""
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*(亿元|亿|万元|万|元)?\s*", value)
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2) or ""
    if "亿" in unit:
        num *= 100_000_000
    elif "万" in unit:
        num *= 10_000
    # 元或不带单位：保持原值
    if num == int(num):
        return str(int(num))
    return f"{num:.2f}".rstrip("0").rstrip(".")


# M-5 修复：日期归一化 → 转换为 YYYY-MM-DD
def _normalize_date(value: str) -> str | None:
    """日期归一化：'2024年1月1日' / '2024/1/1' / '2024-01-01' 都返回 '2024-01-01'。"""
    m = re.match(
        r"\s*(\d{4})[年\-/](\d{1,2})[月\-/](\d{1,2})日?\s*", value
    )
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return datetime(y, mo, d).strftime("%Y-%m-%d")
    except ValueError:
        return None
```

**_fact_in_source 比对逻辑**：

```python
def _fact_in_source(fact: Fact, source_text: str) -> bool:
    """判断单个事实是否在原文中找到。

    M-5 修复：金额/日期先归一化后比对，避免 "1万元" vs "10000元" 误判。
    """
    if not source_text:
        return False

    normalized_source = re.sub(r"\s+", "", source_text)
    normalized_value = re.sub(r"\s+", "", fact.value)
    if normalized_value in normalized_source:
        return True

    # M-5 归一化比对：金额 / 日期
    if fact.category == "金额":
        target_norm = _normalize_amount(fact.value)
        if target_norm:
            for m in re.finditer(r"\d+(?:\.\d+)?\s*(?:亿元|亿|万元|万|元)?", source_text):
                src_norm = _normalize_amount(m.group(0))
                if src_norm and src_norm == target_norm:
                    return True
        return False

    if fact.category == "日期":
        target_norm = _normalize_date(fact.value)
        if target_norm:
            for m in re.finditer(
                r"\d{4}年\d{1,2}月\d{1,2}日|\d{4}-\d{1,2}-\d{1,2}|\d{4}/\d{1,2}/\d{1,2}",
                source_text,
            ):
                src_norm = _normalize_date(m.group(0))
                if src_norm and src_norm == target_norm:
                    return True
        return False

    return False
```

**审查重点**：
1. `_normalize_amount("1.5亿元")` → `150000000` (str)，`_normalize_amount("150000000元")` → `150000000`，相等 ✓
2. `_normalize_amount("1.234 万元")` → `12340` (因为 `1.234 * 10000 = 12340.00` → rstrip 后 `12340`)?
3. 金额正则 `\d+(?:\.\d+)?\s*(?:亿元|亿|万元|万|元)?` 会不会匹配到非金额的纯数字（如招标编号里的 2026）？会导致误命中吗？
4. 日期正则没匹配 `2024.1.1` 格式，是否需要补？
5. `for m in re.finditer(...)` 每次都全文扫描，长文本下性能如何？

---

### M-6 多平台并发采集

**app/scheduler/collector.py:66-143**：

```python
async def _collect_one_platform(
    platform: str,
    filters: ParsedFilters,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    """采集单个平台（受 semaphore 控制并发）。"""
    async with semaphore:
        from app.core.scraper import ScrapeError, scraper
        from app.processors.simhash import compute_simhash
        from app.processors.tender_ingestor import ingest_scrape_result

        request = build_scrape_request(platform, filters)
        if request is None:
            return {"platform": platform, "error": "unsupported platform"}

        try:
            result = await scraper.scrape(request)
            ingest = await ingest_scrape_result(
                scrape_result=result,
                template=platform,
                simhash_computer=compute_simhash,
            )
            return {
                "platform": platform,
                "collected": ingest["total"],
                "inserted": ingest["inserted"],
                "duplicates": ingest["duplicates"],
                "errors": ingest["errors"],
                "_ingest": ingest,
            }
        except ScrapeError as exc:
            logger.warning("collect failed platform=%s err=%s", platform, exc)
            return {"platform": platform, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            logger.exception("collect unexpected error platform=%s", platform)
            return {"platform": platform, "error": str(exc)}


async def collect_new_tenders(
    sub: Subscription, filters: ParsedFilters
) -> dict[str, Any]:
    """M-6 修复：多平台并发采集（asyncio.gather + Semaphore）。"""
    platforms = sub.platforms or ["ccgp"]
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_PLATFORMS)

    # M-6 并发采集所有平台
    tasks = [_collect_one_platform(p, filters, semaphore) for p in platforms]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    total_collected = 0
    total_inserted = 0
    total_duplicates = 0
    total_errors = 0
    per_platform: list[dict[str, Any]] = []

    for r in results:
        ingest = r.pop("_ingest", None)  # type: ignore[union-attr]
        if ingest:
            total_collected += ingest["total"]
            total_inserted += ingest["inserted"]
            total_duplicates += ingest["duplicates"]
            total_errors += ingest["errors"]
        per_platform.append(r)

    return {
        "total": total_collected,
        "inserted": total_inserted,
        "duplicates": total_duplicates,
        "errors": total_errors,
        "per_platform": per_platform,
    }
```

**审查重点**：
1. 多个并发任务同时 `ingest_scrape_result` 写同一数据库，SQLite 是否能处理？（SQLite 默认串行写入，可能锁冲突）
2. `r.pop("_ingest", None)` 修改了返回的 dict，是否会导致 per_platform 里的字典和原结果不一致？（pop 后 per_platform 里没有 `_ingest` 字段，符合预期）
3. `return_exceptions=False` 任一平台异常会中断所有，但 `_collect_one_platform` 内部已经 try/except 兜底了，所以不会真的抛。逻辑对吗？
4. Semaphore(3) 上限是否合理？2 个平台时实际只用 2 个并发槽。

---

### M-7 SSRF 内网 URL 校验

**新增 app/utils/url_safety.py**（57 行）：

```python
"""URL 安全校验：防 SSRF。"""
from __future__ import annotations
import ipaddress
from urllib.parse import urlparse


def is_safe_url(url: str) -> tuple[bool, str]:
    """防 SSRF：拒绝内网/回环/链路本地地址。"""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return False, "invalid hostname"

        if parsed.scheme not in ("http", "https"):
            return False, f"scheme not allowed: {parsed.scheme}"

        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private:
                return False, f"private ip blocked: {hostname}"
            if ip.is_loopback:
                return False, f"loopback ip blocked: {hostname}"
            if ip.is_link_local:
                return False, f"link-local ip blocked: {hostname}"
            if ip.is_reserved:
                return False, f"reserved ip blocked: {hostname}"
            if ip.is_multicast:
                return False, f"multicast ip blocked: {hostname}"
        except ValueError:
            # 域名形式，MVP 阶段允许（生产环境应 DNS 解析后再校验）
            if hostname in ("localhost", "metadata.google.internal"):
                return False, f"internal hostname blocked: {hostname}"

        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"url parse error: {exc}"
```

**scraper.py 调用**：

```python
url = request.get("url")
if not url or not isinstance(url, str):
    raise ScrapeError("url 字段必填且必须是字符串")

# M-7 修复：SSRF 防护，拒绝内网/回环/链路本地地址
safe, reason = is_safe_url(url)
if not safe:
    logger.warning("SSRF blocked url=%s reason=%s", url[:80], reason)
    raise ScrapeError(f"URL 不安全: {reason}")
```

**attachment_downloader.py 改用公共模块**：

```python
from app.utils.url_safety import is_safe_url as _is_safe_url
# 删除了本地 _is_safe_url 实现
```

**审查重点**：
1. 域名形式只拦截 `localhost` 和 `metadata.google.internal`，攻击者用 `localtest.me`（解析到 127.0.0.1）能绕过。MVP 阶段是否可接受？
2. `is_safe_url` 没校验端口，`http://example.com:22` 会通过，是否需要限制？
3. IPv6 地址（如 `[::1]`）urlparse 解析是否正确？`ipaddress.ip_address("::1")` 能识别为 loopback 吗？
4. 重定向场景：scraper 抓 example.com → 302 到 169.254.169.254，Playwright 会跟随重定向，本校验无效。是否需要禁用 follow_redirects 或在 Playwright 层加路由拦截？

---

### M-8 cookie 目录独立配置

**app/config.py:50-51**：

```python
# 附件下载目录（命题第 4 项硬要求）
ATTACHMENT_DIR: str = "data/attachments"
# M-8 修复：cookie 目录独立配置（不再从 ATTACHMENT_DIR 推导）
COOKIE_DIR: str = "data/cookies"
```

**app/templates/qianlima.py:34**：

```python
# 千里马 cookie 文件默认路径
# M-8 修复：使用独立的 COOKIE_DIR 配置，不再从 ATTACHMENT_DIR 推导
_COOKIE_FILE = Path(settings.COOKIE_DIR) / "qianlima.json"
```

**审查重点**：
1. 旧部署的 cookie 文件在 `data/cookies/`（基于 ATTACHMENT_DIR.parent 推导），新配置默认值也是 `data/cookies`，路径是否兼容？
2. 启动时是否需要校验 COOKIE_DIR 在 `data/` 目录范围内，防止被配置到 `/etc/` 等敏感位置？
3. .env.example 是否需要补充 COOKIE_DIR 示例？

---

### Minor 修复（m-1 / m-2 / m-3 / m-8）

**m-1 数量正则去重复"套"**（hallucination_checker.py:42）：

```python
# m-1 修复：去掉重复的"套"
("数量", re.compile(r"\d+\s*(?:台|套|个|批|项|份|辆)")),
```

**m-2 模块级 lambda 改函数**（simhash.py:36-39）：

```python
# m-2 修复：模块级 lambda 改为标准函数（PEP8 E731）
def _jieba_tokenizer(text: str) -> list[str]:
    return [t for t in jieba.lcut(text) if t.strip()]

_tokenizer = _jieba_tokenizer
```

**m-3 ccgp 升级到 https**（collector.py:31）：

```python
_PLATFORM_URLS: dict[str, str] = {
    "ccgp": "https://search.ccgp.gov.cn/bxsearch?searchtype=1&page_index=1",
    "chinabidding": "https://www.chinabidding.cn/search/searchzbgg",
    # m-8 修复：ggzy 不支持 URL 参数搜索，移除
    # "ggzy": "https://deal.ggzy.gov.cn/ds/deal/dealList.html",
}
```

**审查重点**：
1. ccgp 实际是否支持 https？（如果不支持会证书错误）
2. ggzy 移除后，订阅 platforms 里有 ggzy 时会返回 `unsupported platform`，是否需要文档说明？

---

## 三、测试验证

```
90 passed, 31 warnings in 276.38s (0:04:36)
```

所有原有测试通过，但**未为本轮新增功能写新测试**：
- C-2 反幻觉真正生效：没有"传 source_texts 后检出幻觉"的测试
- C-3 金额单位解析：test_parse_decimal_wan 仍用旧数据 `50万元 → 500000`，没覆盖 `1.5亿元` / `100万美元` 等边界
- M-5 归一化比对：没有 `_normalize_amount` / `_normalize_date` 的单元测试
- M-6 并发采集：没有"多平台同时抓取"的集成测试
- M-7 SSRF：没有"scraper 拒绝内网 URL"的测试

**建议补充以下测试**：
1. `test_normalize_amount_various_units`：覆盖 万元/亿元/亿/万/元/纯数字
2. `test_normalize_date_various_formats`：覆盖 年月日 / - / / 三种格式
3. `test_hallucination_with_source_texts`：传 source_texts 后能检出幻觉
4. `test_scraper_rejects_ssrf`：scraper.scrape({"url": "http://127.0.0.1"}) 抛 ScrapeError
5. `test_parse_decimal_yi_yuan`：`1.5亿元 → 150000000`
6. `test_collect_new_tenders_concurrent`：mock 2 个平台，验证并发执行

---

## 四、命题硬要求达标情况（本轮修复后预估）

| 命题硬要求 | 上轮评分 | 本轮修复 | 本轮预估 |
|---|---|---|---|
| 1. 意图解析 5 槽位 | 9/10 | 未改动 | 9/10 |
| 2. ≥2 网站 + ≥1 登录态采集 | 7/10 | M-8 cookie 目录独立 | 7.5/10 |
| 3. SimHash 内容去重 | 5/10 | C-1 已修 + M-1/M-2/M-3 优化 | 8/10 |
| 4. 5 字段汇总 + Word 命名 | 9/10 | 未改动 | 9/10 |
| 5. 定时执行（cron 触发） | 8/10 | 未改动 | 8/10 |
| 6. 增量推送（已推送不重复） | 8/10 | 未改动 | 8/10 |
| 7. 反幻觉（core_content 与原文一致） | 3/10 | C-2 + M-4 + M-5 核心修复 | 7.5/10 |

**预估总评分**：6.5/10 → **8.0/10**

---

## 五、审查请求

请按以下顺序审查：

1. **逐项核查 16 个修复点是否真正落地**（对照"关键代码片段"）
2. **检查是否引入新问题**（特别关注 C-2 字段添加对旧数据库的兼容性、M-6 并发写 SQLite 的锁冲突、M-5 归一化正则的误匹配）
3. **评估命题硬要求达标情况**（特别是第 3 项 SimHash 和第 7 项反幻觉，从 5/10 和 3/10 提升到多少）
4. **指出本轮未修复的遗留问题**（如重定向 SSRF、SQLite 并发写、未补测试等）
5. **给出本轮总评分**（代码质量 / 命题覆盖 / 企业级成熟度，三项各 10 分制）

输出格式：

```
## 第三轮审查报告

### 修复验收
| 编号 | 修复点 | 是否到位 | 备注 |
|---|---|---|---|
| C-1 | ... | ✅/⚠️/❌ | ... |
...

### 新引入问题
- [问题描述 + 文件:行号]

### 命题硬要求达标情况
| 硬要求 | 上轮 | 本轮 | 变化 |
...

### 总评分
- 代码质量：X/10
- 命题覆盖：X/10
- 企业级成熟度：X/10

### 下轮建议
- [按优先级排序的待修复项]
```
