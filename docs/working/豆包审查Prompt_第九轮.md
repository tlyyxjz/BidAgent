# 豆包代码审查 · 第九轮

## 背景

这是 ScrapeFlow 项目（超聚变命题 · 招投标信息聚合工具）的**第九轮审查**。

- **第八轮**：评分 9.3/8.5/8.8（代码质量/命题覆盖/企业级成熟度），0 Critical + 0 Major + 3 Minor
- **第九轮（本轮）**：接入 Sol 完整交付的 13 个新文件 + 13 个修改文件补丁，完成命题 2（登录态采集）+ 命题 6（增量推送）的核心代码实现

本轮重点变化：
1. **S-10 真实 SMTP 邮件推送**（命题 6 核心）— 新增 `app/core/email_sender.py`，支持 STARTTLS/SMTP_SSL、重试、邮件头注入防护
2. **S-11 登录态管理**（命题 2 基础设施）— 新增 `app/core/session_manager.py` + `browser_pool.py`，Playwright storage_state 持久化
3. **S-15 Webhook 推送**（命题 6 加分项）— 新增 `app/core/webhook_sender.py`，HMAC-SHA256 签名 + SSRF 防护
4. **S-1 千里马登录**（命题 2 硬要求）— 新增 `app/templates/qianlima_login.py` + `scripts/login_qianlima.py`
5. **S-13 DOM 探测**（已完成实测）— 新增 `scripts/probe_qianlima_dom.py`，实测确认真实登录 URL `https://vip.qianlima.com/login.html`
6. **关键事务一致性修复** — `subscription.py` 中 trigger_subscription 重排顺序：先推送 → delivered=True → 写 PushLog + commit
7. **测试从 170 → 211**（+41，新增 4 个测试文件覆盖 email/session/browser_pool/qianlima_login）

命题硬要求（6+1 项）：
1. LLM 意图解析 5 槽位 / 2. ≥2 网站 + ≥1 登录态 / 3. SimHash 去重 / 4. 5 字段汇总+Word 命名 / 5. cron 定时 / 6. 增量推送 / 7. 反幻觉

---

## 本轮修改清单（共 5 大块）

### 修改 1：S-10 真实 SMTP 邮件推送（命题 6 核心）

**新增文件**：`app/core/email_sender.py`（252 行）

**关键设计**：
```python
class EmailSender:
    @staticmethod
    def _config() -> SMTPConfig:
        """从 settings 读取 SMTP 配置。"""

    def is_configured(self) -> bool:
        """SMTP 配置完整性检查。"""

    async def send_with_attachment(
        self,
        to_addrs: list[str],
        subject: str,
        body: str,
        attachment_path: Path,
    ) -> dict[str, Any]:
        """发送带附件邮件。
        返回 {"ok": bool, "message_id": str|None, "error": str|None}
        最多 4 次尝试（首次 + 3 次重试，退避 1/2/4 秒）
        """
```

**安全特性**：
1. `_sanitize_header()` 拒绝 CR/LF 邮件头注入
2. `_normalize_recipients()` 校验 + 去重收件人
3. `parseaddr()` + 显式结构校验防绕过
4. `email.utils.make_msgid()` 生成 Message-ID
5. 同步 `smtplib` 通过 `asyncio.to_thread()` 包装，不阻塞事件循环
6. STARTTLS（587）和 SMTP_SSL（465）双路径
7. 配置不完整时不抛异常，返回 `{ok: False, error: "SMTP 配置不完整"}`

**审查重点**：
- `MAX_RETRIES = 3` + `RETRY_DELAYS = (1, 2, 4)`：总尝试次数 4（首次 + 3 次重试），是否符合"最多 3 次重试"的语义？
- `server.sendmail()` 返回的 `refused` 字典是否被正确处理？
- `_send_via_starttls` 中 `server.ehlo()` 调用两次（STARTTLS 前后），是否符合 RFC 2487？
- 附件不存在时返回 `{ok: False}` 而不是抛 `FileNotFoundError`，是否合理？
- 邮件头注入防护：`_sanitize_header` 只检查 CR/LF，是否够用？还应防什么？

**测试**：`tests/test_push_email.py`（13 个测试，覆盖 STARTTLS 路径、SSL 路径、收件人校验、重试、附件缺失、配置不完整等）

---

### 修改 2：S-11 登录态管理（命题 2 基础设施）

**新增文件**：`app/core/session_manager.py`（约 220 行）+ `app/core/browser_pool.py`（约 180 行）

#### SessionManager

```python
class SessionManager:
    def __init__(self, platform: str, session_path: Path | None = None)
    def has_session(self) -> bool
    async def load_state(self) -> dict[str, Any] | None
    async def save(self, context: Any) -> Path
    async def is_valid(self, required_cookie_names: set[str] | None = None,
                       domain_suffix: str | None = None) -> bool
    async def create_context(self, browser: Any, **context_options: Any) -> Any
    async def cookie_summary(self) -> list[dict[str, Any]]  # 脱敏，不含 value
    async def delete(self) -> bool
```

**安全特性**：
1. 平台名安全校验 `^[A-Za-z0-9_-]{1,64}$`，防路径穿越
2. 原子写入 `tempfile.NamedTemporaryFile` + `os.replace`
3. Cookie 过期检查兼容 `expires` / `maxAge` / `max-age`
4. 域名边界匹配 `domain.endswith("." + suffix)`
5. `cookie_summary()` 脱敏，日志不输出 Cookie value
6. Session 文件必须保存在 `data/sessions/` 下（`main.py` 中 `_validate_data_dir` 校验）

#### BrowserPool

```python
class BrowserPool:
    def __init__(self, size: int | None = None,
                 acquire_timeout: float | None = None,
                 headless: bool | None = None)
    async def __aenter__(self) -> "BrowserPool"
    async def __aexit__(self, *args) -> None
    async def acquire(self) -> BrowserSlot  # asyncio.wait_for + Semaphore
    async def release(self, slot: BrowserSlot) -> None  # 幂等
    @asynccontextmanager
    async def context(self, **context_options) -> AsyncIterator[Any]  # 自动归还
```

**关键设计**：
1. **不在模块导入时创建 Lock/Semaphore/Queue**（解决跨事件循环问题）
2. Queue + Semaphore 组合实现有界池
3. Context 创建失败也归还槽位（async with 最终块）
4. release 幂等（重复归还不会增加池容量）

**审查重点**：
- `BrowserPool.__aenter__` 中启动多个 Browser，部分启动失败时是否正确清理已启动的？
- `release()` 幂等性如何实现？是否需要在 slot 上加 `_returned` 标志位？
- `acquire()` 的超时（`asyncio.wait_for`）触发后，已开始启动的 Browser 怎么办？
- `storage_state` 加载失败的降级策略是否合理？（返回 None → 匿名访问）

**测试**：`tests/test_session_manager.py`（13 个测试）+ `tests/test_browser_pool.py`（10 个测试）

---

### 修改 3：S-15 Webhook 推送 + 事务一致性修复

**新增文件**：`app/core/webhook_sender.py`（约 100 行）

```python
class WebhookSender:
    async def send(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """HMAC-SHA256 签名 + SSRF 防护 + 重试 3 次"""
```

**安全特性**：
1. `X-ScrapeFlow-Signature` HMAC-SHA256 签名（`WEBHOOK_SECRET`）
2. SSRF 防护用 `is_safe_url_async`
3. `follow_redirects=False` 防重定向攻击
4. 重试 3 次，间隔 2^attempt 秒

**修改文件**：`app/scheduler/push.py`（重写）+ `app/scheduler/subscription.py`（关键修复）

#### push.py 重写

```python
async def push_to_channels(sub, report_path, count) -> dict[str, Any]:
    """返回 {delivered: bool, channels: [...]}"""
    channels = sub.push_channels or []
    results = []
    any_delivered = False
    for channel in channels:
        if channel == "email":
            result = await _push_email(sub, attachment, count)
        elif channel == "webhook":
            result = await _push_webhook(sub, attachment, count)
        else:
            result = {"channel": channel, "ok": False, ...}
        if result.get("delivered"):
            any_delivered = True
        results.append(result)
    return {"delivered": any_delivered, "channels": results}
```

**通道降级语义**：
- `notify_email` 未配置 → 降级 log（`delivered=False`）
- SMTP 未配置 → 降级 log（`delivered=False`）
- 附件不存在 → `ok=False, delivered=False`
- 真实发送失败 → `ok=False, delivered=False`
- 只有真实发送成功才 `delivered=True`

#### subscription.py 事务顺序修复（关键）

**问题**：旧顺序 [写 PushLog → 更新 last_pushed_at → commit → 推送] 导致 SMTP 失败时数据库已认为推送成功，下次 NOT EXISTS 过滤 → 永久漏发

**新顺序**：
```python
# 1. 查询未推送 tender
unpushed = await get_unpushed_tenders(db, subscription_id, filters)

# 2. 生成 Word 报告
report_path = await generate_report(filters, items, ...)

# 3. 真实外部推送（email/webhook）
push_results = await push_to_channels(sub, report_path, len(unpushed))

# 4. 检查 delivered
if not push_results.get("delivered", False):
    # 推送失败：不写 PushLog，下次触发会重新推送
    return {"status": "push_failed", "count": len(unpushed), ...}

# 5. 推送成功：写 PushLog + 更新 last_pushed_at + commit
await _record_push(db, subscription_id, tender_ids)  # 不再自行 commit
sub.last_pushed_at = utc_now()
await db.commit()
```

**`_record_push()` 不再自行 commit**：PushLog 和 last_pushed_at 在同一事务中提交

**审查重点**：
- 推送失败时不写 PushLog，下次触发会重新推送这批数据 — 是否会导致重复推送？（用户收到 2 次相同邮件）
- 如果推送成功但 `db.commit()` 失败（数据库锁），会导致什么？邮件已发但数据库没记录
- `push_results.get("delivered", False)` 的"或"语义是否正确？多个通道时只要一个 delivered=True 就算成功？
- Webhook 推送失败时是否应该有降级？还是直接 `ok=False`？
- `push_to_channels` 中遍历 channels 是串行的，是否应该用 `asyncio.gather` 并行？

---

### 修改 4：S-1 千里马登录 + DOM 探测（已完成实测）

**新增文件**：
- `app/templates/qianlima_login.py`（约 200 行）— 人工完成验证码 + 自动保存 storage_state
- `scripts/login_qianlima.py`（约 90 行）— 命令行交互式登录
- `scripts/probe_qianlima_dom.py`（约 120 行）— DOM 探测器
- `qianlima-dom.json`（DOM 配置，`verified: true`）

**关键发现**：原配置 `https://www.qianlima.com/login` 返回 301 Moved Permanently 且无 DOM 元素；真实登录 URL 是 `https://vip.qianlima.com/login.html`（最终跳转到 `https://vip.qianlima.com/login/`）

**实测 DOM 选择器**：
```json
{
  "login": {
    "url": "https://vip.qianlima.com/login.html",
    "username_selector": ["input[name='username']"],
    "password_selector": ["input[name='password']"],
    "captcha_input_selector": ["input[placeholder='请输入验证码']"],
    "submit_selector": ["button.handle-btn:has-text('登录')"],
    "success_url_pattern": "vip.qianlima.com"
  }
}
```

**qianlima.py 模板改造**：
- 删除模块导入时固化 Cookie（`template.__dict__["cookies"] = cookies`）
- 改用 `SessionManager("qianlima")` 单例
- `get_qianlima_storage_state()` 动态加载，Session 失效时返回 None（降级匿名访问）

**scraper.py 接入**：
- `_merge_template()` 保留 `template` 名 + 新增 `storage_state` 可覆盖字段
- `scrape()` 中千里马模板动态加载 storage_state
- `_scrape_with_playwright()` 接收 `storage_state` 参数，创建 context 时注入

**审查重点**：
- 千里马登录脚本依赖 DOM 选择器，选择器变化时如何检测？（是否需要定期 probe？）
- `login_and_save_cookies` 中 `wait_timeout_seconds=300` 等待人工验证码，是否合理？
- Session 失效时降级匿名访问，但千里马匿名访问可能拿不到完整数据 — 是否应该 fail-fast？
- `qianlima_login.py` 中异常路径是否正确清理 Context/Browser/Driver？
- DOM 探测脚本 `probe_qianlima_dom.py` 输出 `verified: false`，但 `qianlima-dom.json` 已手动标记 `verified: true` — 这个手动标记是否合规？

**测试**：`tests/test_qianlima_login.py`（8 个测试）

---

### 修改 5：测试 + 配置 + 部署

**测试新增**：
- `tests/test_session_manager.py`（13 个测试）
- `tests/test_browser_pool.py`（10 个测试）
- `tests/test_qianlima_login.py`（8 个测试）
- `tests/test_push_email.py`（13 个测试）
- 总计 +44 测试，**测试总数从 170 → 211**

**测试修复**（兼容新事务语义）：
- `test_e2e_flow.py` 3 个测试加 `mock_push_delivered` fixture（测试环境无 SMTP 配置，需 mock delivered=True 才能写 PushLog）
- `test_push_email.py` 2 个测试修复 mock（`sendmail.return_value = {}` 避免 `_raise_if_refused` 误判）
- `test_qianlima_login.py` 1 个测试修复 mock（`page.locator()` 是同步方法，改用 `MagicMock`）

**配置修改**：
- `app/config.py`：加 SMTP/Session/BrowserPool/Qianlima 配置 + `validate_positive_config` 校验器
- `app/models/subscription.py`：加 `notify_email` / `webhook_url` 字段
- `app/models/database.py`：加 SQLite 迁移条目
- `app/api/subscribe.py`：加 EmailStr/AnyHttpUrl 字段 + `validate_push_channels` 白名单校验
- `app/main.py`：加 `ANTI_DETECT_SESSION_DIR` 目录校验
- `.env.example` / `docker-compose.yml`：加全部新环境变量

**Dockerfile 升级**：Python 3.11 → 3.13，多阶段构建 + 非 root 用户 + Playwright 系统依赖 + healthcheck

**审查重点**：
- `test_e2e_flow.py` 的 `mock_push_delivered` fixture 是否掩盖了真实问题？（测试环境无法验证真实推送链路）
- `validate_push_channels` 白名单 `{"email", "webhook"}` 是否够用？未来扩展性如何？
- `EmailStr` 和 `AnyHttpUrl` 的 Pydantic 校验是否足够严格？
- SQLite 迁移只加列不删列，是否符合迁移最佳实践？
- Dockerfile 升级到 Python 3.13 是否有兼容性风险？（某些依赖可能未支持 3.13）

---

## 请你做的事

请基于本轮**新增/修改的代码**做第九轮审查，重点关注：

1. **Sol 代码的工程质量**：13 个新文件是否符合企业级标准？
2. **事务一致性修复**：新顺序是否真正解决了"永久漏发"问题？是否引入新问题（如重复推送）？
3. **登录态安全性**：storage_state 持久化 + 域名边界匹配 + Cookie 过期检查是否够用？
4. **SMTP 实现的健壮性**：重试语义、邮件头注入防护、附件处理是否完备？
5. **Webhook 推送的 SSRF 防护**：`is_safe_url_async` + `follow_redirects=False` 是否够用？
6. **测试覆盖度**：211 个测试是否真正覆盖关键场景？mock 是否合理？
7. **命题 2 和命题 6 的完成度**：从"部分实现"到"代码完成"是否名副其实？

请按以下格式输出：

```
## 评分
- 代码质量：X.X/10
- 命题覆盖：X.X/10
- 企业级成熟度：X.X/10

## Critical（必须修复）
- C-X: <文件>:<行号> — <问题描述> + 修复建议

## Major（强烈建议修复）
- M-X: <文件>:<行号> — <问题描述> + 修复建议

## Minor（可选修复）
- m-X: <文件>:<行号> — <问题描述> + 修复建议

## 亮点（加分项）
- ✨ <文件>:<行号> — <亮点描述>

## 命题完成度评估
- 命题 1（意图解析 5 槽位）：X/10
- 命题 2（≥2 网站 + ≥1 登录态）：X/10
- 命题 3（SimHash 去重）：X/10
- 命题 4（5 字段汇总+Word 命名）：X/10
- 命题 5（cron 定时）：X/10
- 命题 6（增量推送）：X/10
- 命题 7（反幻觉）：X/10

## 总评
<50-100 字总结>
```

---

## 参考文件路径

- 项目根：`ppp_dev/scrapeflow/`
- 新增文件：`app/core/email_sender.py` / `session_manager.py` / `browser_pool.py` / `webhook_sender.py`
- 新增文件：`app/templates/qianlima_login.py` / `scripts/login_qianlima.py` / `scripts/probe_qianlima_dom.py`
- 修改文件：`app/config.py` / `app/models/subscription.py` / `app/models/database.py` / `app/api/subscribe.py`
- 修改文件：`app/scheduler/push.py`（重写）/ `app/scheduler/subscription.py`（事务顺序修复）
- 修改文件：`app/core/scraper.py` / `app/templates/qianlima.py` / `app/main.py`
- 测试：`tests/test_push_email.py` / `test_session_manager.py` / `test_browser_pool.py` / `test_qianlima_login.py`
- 配置：`.env.example` / `docker-compose.yml` / `Dockerfile` / `qianlima-dom.json`
