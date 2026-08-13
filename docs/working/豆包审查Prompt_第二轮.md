# 豆包 Turbo 第二轮审查 Prompt（GLM-5.2 新增代码）

> 复制以下内容到豆包 Turbo 任务模式，让它审查 GLM-5.2 本轮新增/修改的代码

---

## 审查背景

我正在为 **2026 AI 先锋未来人才大赛 · 超聚变命题** 开发招投标信息聚合工具 ScrapeFlow。上一轮豆包审查发现的 5 Critical + 8 Major + 5 Minor 已全部修复。本轮 GLM-5.2 又新增了 4 个模块 + 修改了 5 个模块，需要再审查一遍。

**技术栈**：FastAPI + SQLAlchemy 2.x async + Playwright + DeepSeek V3 + croniter + python-docx + Docker

**工程硬性约束**：
- 单文件 ≤ 300 行
- 单函数 ≤ 50 行
- 所有 I/O 必须 async/await
- 禁止裸 `except:`
- 所有公开函数必须有类型注解 + docstring
- 日志用 loguru，禁用 print
- 配置用 pydantic-settings
- 联系人手机号/邮箱 SHA256 哈希存储

**命题硬要求**：
1. 意图解析 5 槽位（topic/region/time_range/frequency/trigger_type）
2. ≥2 网站 + ≥1 登录态采集
3. SimHash 内容去重
4. 5 字段汇总（标题/发布时间/来源链接/核心内容/附件链接）+ Word 命名
5. 定时执行（cron 频率触发）
6. 增量推送（已推送不重复）
7. 反幻觉（core_content 与原文事实一致）

---

## 本轮新增/修改的文件清单

| # | 文件 | 行数 | 类型 | 关注点 |
|---|---|---|---|---|
| 1 | app/processors/tender_ingestor.py | 247 | 新增 | 字段映射 + SHA256 + SimHash 去重 + 金额解析 |
| 2 | app/processors/simhash.py | 108 | 新增 | 64 位算法 + jieba 分词 + 汉明距离 |
| 3 | app/processors/hallucination_checker.py | 169 | 新增 | 关键事实提取 + 原文比对 |
| 4 | app/templates/qianlima.py | 94 | 新增 | cookie 文件加载 + 登录态模板 |
| 5 | app/scheduler/collector.py | 93 | 新增（拆分） | 平台 URL 拼接 + 主动采集 |
| 6 | app/api/scrape.py | 175 | 修改 | auto_save 参数 + 入库逻辑 |
| 7 | app/scheduler/subscription.py | 304 | 修改 | auto_collect 参数 + 采集→推送链路 |
| 8 | app/core/scraper.py | 276 | 修改 | cookies + extra_headers 注入 |
| 9 | app/report/docx_components.py | 220 | 修改 | 反幻觉校验章节 |
| 10 | app/report/docx_generator.py | 177 | 修改 | 集成反幻觉章节 |
| 11 | app/processors/__init__.py | 5 | 修改 | 导出 simhash 函数 |
| 12 | app/templates/__init__.py | 40 | 修改 | 注册 qianlima 模板 |

**总计**：4 个新模块 + 8 个修改模块，约 1900 行代码

---

## 审查维度（请按维度打分 0-10 + 列出问题）

### 1. 安全性
- 路径遍历 / SSRF / SQL 注入 / XSS
- 联系人信息是否 SHA256
- cookie 文件加载是否有路径校验
- LLM 输出是否会被覆盖原始数据

### 2. 命题覆盖度
- 6 项硬要求是否真的实现
- SimHash 去重是否生效（汉明距离 3 是否合理）
- 反幻觉校验是否能检测到幻觉（关键事实模式是否覆盖全）
- cron 匹配逻辑是否正确

### 3. 性能
- N+1 查询
- 同步阻塞 IO 在 async 函数中
- 内存泄漏（缓存无 TTL）
- SimHash 候选集合扫描是否合理（1000 条）

### 4. 工程规范
- 单文件 ≤ 300 行（subscription.py 是 304 行，是否需要再拆）
- 类型注解完整性
- 错误处理是否捕获具体异常
- 日志是否带上下文

### 5. 代码质量
- 重复代码
- 圈复杂度过高
- 命名规范
- 边界条件处理

### 6. 测试覆盖
- 53 个测试是否够
- 哪些关键路径没测到
- 测试用例是否真实模拟命题示例

---

## 输出格式要求

```
# 审查报告

## 总体评分
代码质量：X/10
命题覆盖：X/10
企业级成熟度：X/10

## Critical 问题（必须修复）
C-X: [文件:行号] 问题描述 + 修复建议

## Major 问题（建议修复）
M-X: [文件:行号] 问题描述 + 修复建议

## Minor 问题（可选修复）
m-X: [文件:行号] 问题描述 + 修复建议

## 亮点
- ...

## 命题覆盖度评估
表格

## 改进建议
- ...
```

---

## 关键代码片段（让豆包重点看）

### 1. tender_ingestor.py 的去重逻辑
```python
async def _find_duplicate(db, simhash):
    stmt = select(Tender).where(Tender.simhash.is_not(None)).order_by(Tender.id.desc()).limit(1000)
    result = await db.execute(stmt)
    candidates = result.scalars().all()
    for c in candidates:
        if _hamming_distance(simhash, c.simhash or 0) <= SIMHASH_HAMMING_THRESHOLD:
            return c
    return None
```
**关注点**：1000 条候选是否会漏掉重复？是否应该按 source_platform 过滤？

### 2. hallucination_checker.py 的关键事实模式
```python
_PATTERNS = [
    ("金额", re.compile(r"\d+(?:\.\d+)?\s*(?:万元|亿元|元|万|亿)")),
    ("日期", re.compile(r"\d{4}年\d{1,2}月\d{1,2}日|\d{4}-\d{1,2}-\d{1,2}|\d{4}/\d{1,2}/\d{1,2}")),
    ("百分比", re.compile(r"\d+(?:\.\d+)?\s*%")),
    ("数量", re.compile(r"\d+\s*(?:台|套|个|批|项|份|辆|套)")),
    ("联系电话", re.compile(r"\d{3,4}-?\d{7,8}")),
    ("邮箱", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("招标编号", re.compile(r"[A-Z0-9-]{8,30}")),
]
```
**关注点**：招标编号模式 `[A-Z0-9-]{8,30}` 是否误报太多？普通英文单词也会命中？

### 3. simhash.py 的分词
```python
try:
    import jieba
    _tokenizer = lambda text: [t for t in jieba.lcut(text) if t.strip()]
except ImportError:
    logger.warning("jieba 未安装，退化到字符 2-gram")
```
**关注点**：lambda 赋值给模块级变量是否会有闭包问题？jieba 没装时退化方案效果如何？

### 4. subscription.py 的 cron 匹配
```python
def _is_cron_due(cron_expr, last_run, now):
    if cron_expr.startswith("once:"):
        return True
    base = last_run or (now - timedelta(hours=1))
    itr = croniter(cron_expr, base)
    next_run = itr.get_next(datetime)
    return next_run <= now
```
**关注点**：`base = last_run or (now - 1h)` 默认 1 小时前是否合理？如果订阅刚创建还没推过，会不会立即触发？

### 5. qianlima.py 的 cookie 加载
```python
_COOKIE_FILE = Path(settings.ATTACHMENT_DIR).parent / "cookies" / "qianlima.json"

def _load_cookies():
    if not _COOKIE_FILE.exists():
        return []
    data = json.loads(_COOKIE_FILE.read_text(encoding="utf-8"))
    # ...校验...
```
**关注点**：cookie 文件路径是否可控？用户能否通过环境变量配置？是否有路径遍历风险？

### 6. scrape.py 的 auto_save 入库
```python
if payload.auto_save and result.get("data"):
    from app.processors.tender_ingestor import ingest_scrape_result
    ingest_summary = await ingest_scrape_result(
        scrape_result=result,
        template=payload.template,
        simhash_computer=None,  # 阶段 2 集成，此处先传 None
    )
```
**关注点**：simhash_computer=None 是否会导致重复入库？应该改成默认调用 compute_simhash？

---

## 文件位置

完整源代码在：
```
<旧工作区>/
```

需要审查的文件：
- app/processors/tender_ingestor.py
- app/processors/simhash.py
- app/processors/hallucination_checker.py
- app/templates/qianlima.py
- app/scheduler/collector.py
- app/api/scrape.py
- app/scheduler/subscription.py
- app/core/scraper.py
- app/report/docx_components.py
- app/report/docx_generator.py

---

**审查完成后请贴回报告，我会按 Critical → Major → Minor 顺序修复。**
