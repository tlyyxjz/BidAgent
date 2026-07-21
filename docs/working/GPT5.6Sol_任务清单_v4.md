# GPT-5.6 Sol 专属任务清单（v4 · scrapeflow-complete 移植版）

**更新时间**：2026-07-19
**说明**：GLM-5.2 已对比 `scrapeflow-complete.zip`（早期完整版），发现 4 个值得移植的金子模块正好补齐当前项目的命题硬要求缺口。Sol 任务重新规划为「移植 + 原难点」两条线。

---

## 一、scrapeflow-complete 对比结果（GLM-5.2 已完成）

### complete 版有但当前项目没有的模块（按价值排序）

| 模块 | 文件 | 大小 | 命题对应 | 移植价值 |
|---|---|---|---|---|
| 🔥 SMTP 邮件推送 | `app/core/email_sender.py` | 10.3KB | 命题 6 推送 | ⭐⭐⭐⭐⭐ |
| 🔥 登录态管理 | `app/core/session_manager.py` | 10.8KB | 命题 2 登录态 | ⭐⭐⭐⭐⭐ |
| 反检测引擎 | `app/core/anti_detect.py` | 10.7KB | 命题 2 反爬 | ⭐⭐⭐⭐ |
| 浏览器池 | `app/core/browser_pool.py` | 6.7KB | 性能优化 | ⭐⭐⭐⭐ |
| 128-bit SimHash | `app/processors/dedup.py` | 10.1KB | 命题 3 去重 | ⭐⭐⭐ |
| 套餐配置 | `app/core/plans.py` | 5.3KB | 商业化 | ⭐⭐ |

### 当前项目已超越 complete 版的模块（无需移植）

`hallucination_checker.py` / `simhash.py`(自实现) / `tender_ingestor.py` / `pdf_parser.py` / `coordinator.py` / `url_safety.py` / `scheduler/{utils,push}.py` / `templates/qianlima.py` / `templates/html/` / `api/{admin,agents}.py`

---

## 二、Sol 任务列表（共 5 项，预估 $2.5 美元 ≈ ¥18 RMB）

### 🔥🔥 S-10: 移植 email_sender.py 到 push.py（命题硬要求 6，最高优先级）

**为什么给 Sol**：当前 `app/scheduler/push.py` 仅是日志占位，命题 6「增量推送」会被判不达标。complete 版的 `email_sender.py` 是 10KB 完整实现，需要 Sol 改造接入。

**需求**：
1. 把 complete 版 `app/core/email_sender.py` 复制到当前项目 `app/core/email_sender.py`
2. 修改 `app/scheduler/push.py` 的 `push_to_channels`，调用 `EmailSender.send_with_attachment()`
3. 在 `app/config.py` 加 SMTP 配置字段（参考 complete 版）：
   ```python
   SMTP_HOST: str = ""
   SMTP_PORT: int = 587
   SMTP_USER: str = ""
   SMTP_PASSWORD: str = ""
   SMTP_USE_TLS: bool = True
   SMTP_FROM_ADDR: str = ""
   SMTP_FROM_NAME: str = "ScrapeFlow 招标推送"
   ```
4. `push_to_channels` 增加 `is_configured()` 检查，未配置 SMTP 时降级到日志
5. 写一个简单 mock 测试 `tests/test_push_email.py`

**约束**：
- 单文件 ≤ 300 行
- SMTP 配置缺失时不能崩溃
- 命题硬要求：「成功推送后更新 last_pushed_at」（已在 subscription.py 做了，Sol 不要重复）

**预期 token**：~$0.4

---

### 🔥🔥 S-11: 移植 session_manager.py + anti_detect.py + browser_pool.py（命题硬要求 2）

**为什么给 Sol**：命题 2 要求「≥1 登录态采集」。complete 版的 `session_manager.py` 是完整实现，但需要 Sol 改造适配当前项目的 `app/templates/qianlima.py`。

**需求**：
1. 复制 complete 版 3 个文件到当前项目 `app/core/`：
   - `session_manager.py`（登录态持久化）
   - `anti_detect.py`（patchright + stealth 反检测）
   - `browser_pool.py`（浏览器池复用）
2. 在 `app/config.py` 加配置字段：
   ```python
   ANTI_DETECT_ENABLED: bool = True
   ANTI_DETECT_HEADLESS: bool = True
   ANTI_DETECT_NO_SANDBOX: bool = False
   ANTI_DETECT_SESSION_DIR: str = "data/sessions"
   ```
3. 修改 `app/core/scraper.py` 的 `Scraper.scrape()`，可选使用 `AntiDetectBrowser` 替代原生 Playwright（通过 `settings.ANTI_DETECT_ENABLED` 切换）
4. 修改 `app/templates/qianlima.py`，集成 `SessionManager("qianlima")`，无 session 时调用 S-1 的登录流程

**约束**：
- patchright 可能 Windows 安装失败，需要 fallback 到 playwright
- 不能破坏现有 90 测试（test_scraper.py 的 mock 适配）
- 单文件 ≤ 300 行，三个文件如果超过需要拆分

**预期 token**：~$0.6

---

### 🔥 S-1: 千里马验证码识别（依赖 S-11，原任务保留）

**为什么给 Sol**：ddddocr 模型调参 + Playwright 模拟登录 + cookie 持久化，GLM-5.2 鲁棒性差

**需求**：新建 `app/templates/qianlima_login.py`
```python
async def login_and_save_cookies(
    username: str,
    password: str,
    login_url: str = "https://www.qianlima.com/login",
    cookie_file: Path = Path("data/cookies/qianlima.json"),
) -> dict:
    """
    1. 用 AntiDetectBrowser 打开登录页（S-11 依赖）
    2. 截图验证码 → ddddocr 识别
    3. 填用户名/密码/验证码 → 提交
    4. 检测登录成功（URL 跳转或元素出现）
    5. 用 SessionManager.save() 持久化 storage_state
    6. 返回 {"success": bool, "error": str|None}
    """
```

**约束**：失败重试 ≤ 3 次，401 自动重登，单文件 ≤ 300 行

**预期 token**：~$0.5

---

### 🔧 S-2: SimHash 升级（可选，TF-IDF 加权 OR 移植 128-bit）

**为什么给 Sol**：当前等权 64-bit 准确率约 75%，需要提升

**两条路线，Sol 选一条**：

**路线 A（推荐 · 30 行）**：在 `app/processors/simhash.py` 加 TF-IDF 加权
```python
from collections import Counter
import math

token_freq = Counter(tokens)
for token, freq in token_freq.items():
    tf = 1 + math.log(freq)
    h = _hash64(token)
    for i in range(64):
        if h & (1 << i):
            weights[i] += tf
        else:
            weights[i] -= tf
```

**路线 B（移植 · 50 行）**：用 complete 版 `app/processors/dedup.py` 替换当前 `simhash.py`
- 优点：128-bit + LSH 索引，准确率 95%+
- 缺点：依赖 `1e0ng/simhash` 库（需要 `pip install simhash`），可能 Windows 安装失败

**约束**：不破坏现有 90 测试，汉明距离阈值校准

**预期 token**：~$0.3

---

### 🔧 S-9: queue.py Redis 断线重连（最低优先级，原任务保留）

**为什么给 Sol**：需要实战经验调连接池参数

**需求**：
1. 全局 Redis 客户端单例 + 连接池
2. Redis 断线后最多重试 3 次（指数退避 1s/2s/4s）
3. 重试失败才降级到线程池
4. Redis 恢复后自动恢复使用

**约束**：测试环境无 Redis 时不阻塞

**预期 token**：~$0.2

---

## 三、验证标准

1. `python -m pytest --tb=short` 必须 ≥ 90 测试全过（当前基线）
2. S-10 完成：能发一封测试邮件到指定邮箱
3. S-11 完成：能加载已保存的 storage_state，无 session 时降级到匿名访问
4. S-1 完成：千里马登录流程能跑通（mock 环境也行）
5. S-2 完成：SimHash 准确率提升（不破坏现有测试）
6. S-9 完成：Redis 断线重连不影响测试环境

---

## 四、预期 Sol 总消耗

| 任务 | 成本 | 命题对应 |
|---|---|---|
| 🔥 S-10 邮件推送移植 | $0.4 | 命题 6 |
| 🔥 S-11 登录态管理移植 | $0.6 | 命题 2 |
| 🔥 S-1 千里马验证码 | $0.5 | 命题 2 |
| 🔧 S-2 SimHash 升级 | $0.3 | 命题 3 |
| 🔧 S-9 Redis 重连 | $0.2 | 健壮性 |
| 调试 bug 2 次 | $0.5 | - |
| **总计** | **$2.5 ≈ ¥18 RMB** | |

**建议充值 ¥20 RMB（$2.8 美元）就够 Sol 用了。**

---

## 五、任务执行顺序建议

```
S-10 邮件推送（独立，最先做）
   ↓
S-11 登录态管理（独立）
   ↓
S-1 千里马验证码（依赖 S-11）
   ↓
S-2 SimHash 升级（独立）
   ↓
S-9 Redis 重连（独立，最低优先级）
```

**并行机会**：S-10 和 S-11 可以并行；S-2 和 S-9 可以并行。

---

## 六、GLM-5.2 已完成清单（Sol 不要再做）

| 任务 | 完成方式 |
|---|---|
| S-3 scraper.py cookies 传递 | 修改 `_merge_template` 读取 `template.cookies` |
| S-4 拆分 ui.py（490→41 行） | HTML 拆到 `app/templates/html/` |
| S-5 反幻觉正则误报 | 招标编号正则收紧 |
| S-6 cron 默认 base 时间 | `last_run=None` 时返回 False |
| S-7 拆分 auth.py（298→115 行） | admin 路由拆到 `app/api/admin.py` |
| S-8 tender.py 重复 session | 3 个公共查询改为 `Depends(get_db)` |
| 第三轮 C-1 cron 不触发 | `is_cron_due` 重写 |
| 第三轮 C-2 source_raw_text | Tender 模型加字段 + 入库 + 反幻觉组装 |
| 第三轮 C-3 金额解析 | `_parse_decimal` 支持 万元/亿元 |
| 第三轮 C-4 subscription.py 拆分 | 工具移到 utils.py，推送移到 push.py |
| 新-1 SQLite 迁移 | `_run_sqlite_migrations` + PRAGMA table_info |
| 新-2 SQLite WAL | `journal_mode=WAL` + `busy_timeout=30000` |
| 新-3 金额正则要求单位 | `_AMOUNT_RE` 必须带 万元/亿元 |
| 新-4 SSRF 重定向防护 | `page.route("**/*", _ssrf_guard)` |
| 新-5 N+1 根治 | 三阶段批量去重 |
| 新-6 日期支持点号 | `_DATE_RE` 支持 `2024.05.01` |
| 新-7 simhash 清理 | 删除 `compute_simhash_async` |
| 新-8 _validate_data_dir | 启动时校验路径在 data/ 范围内 |

**当前测试基线**：90 passed, 30 warnings in 335.42s

---

**文档结束**
