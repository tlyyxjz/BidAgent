# ScrapeFlow · Sol 续作任务清单（P0 续轮）

## 一、当前交付评估

**已交付**：30 KB 设计文档（30 节，含接口设计、代码片段、测试清单、验收标准）
**未交付**：完整可粘贴代码、测试代码、千里马 DOM 调研、Webhook 实现

**评估结论**：设计完整、思路清晰、风险分析到位，但**距离直接落地还差一轮"代码化"工作**。我（Trae/GLM）可以按文档实施 80% 的工作，剩余 20% 需要你（Sol）补完整代码 + 真实环境调研。

---

## 二、必须补充的（让代码能真正落地）

### 🔥 S-12：补完整代码文件（5 个新增源码）

当前文档只给了接口签名和片段，需要补成完整可粘贴的 .py 文件。

**优先级**：🔥🔥🔥（最高，不补就无法落地）
**预估成本**：$0.6

#### 1. app/core/email_sender.py（完整实现）
文档第 4 节给了接口，但缺：
- `_send_via_starttls()` / `_send_via_ssl()` 完整实现
- `Message-ID` 生成逻辑（用 `email.utils.make_msgid()` + 域名）
- 邮件头换行注入防护（`_sanitize_header()` 函数）
- 多收件人去重 + 格式校验
- 附件 MIME 编码（`MIMEApplication` + `MIMEMultipart`）
- 重试循环（1/2/4 秒，用 `asyncio.sleep`）
- 同步 smtplib 通过 `asyncio.to_thread` 包装

**输出要求**：完整 .py 文件，可直接 `cp` 到项目

#### 2. app/core/session_manager.py（完整实现）
文档第 9 节给了 7 个方法签名，但缺：
- `is_valid()` 的完整 Cookie 过期检查逻辑（`expires` / `max-age` 字段）
- 域名匹配实现（`domain_suffix` 参数的 `cookie.domain.endswith(suffix)` 逻辑）
- `create_context()` 的 storage_state 注入
- `cookie_summary()` 脱敏（不返回 value 字段）
- 原子写入（`tempfile.NamedTemporaryFile` + `os.replace`）
- 平台名安全校验（正则 `^[a-zA-Z0-9_-]+$`）

#### 3. app/core/browser_pool.py（完整实现）
文档第 12 节给了调用方式，但缺：
- `BrowserPool` 完整类实现（`__aenter__` / `__aexit__` / `acquire` / `release` / `context`）
- `asyncio.Semaphore` + `asyncio.Queue` 组合实现有界池
- 超时机制（`asyncio.wait_for`）
- Context 自动归还（`async with` 上下文管理）
- 部分启动失败时清理已启动浏览器
- `close()` 关闭所有 Browser + Playwright Driver
- **关键**：`_pool_lock` 不能在模块导入时创建（文档 12.1 提到的问题）

#### 4. app/templates/qianlima_login.py（完整实现）
文档第 16 节给了接口，但缺：
- `login_and_save_cookies()` 完整实现
- 用户名/密码输入框选择器（需要现场调研，见 S-13）
- 登录成功判断（URL 跳转 + 元素检测组合）
- `getpass.getpass()` 集成
- 异常路径清理（Context / Browser / Driver）
- 日志不输出密码（用 `logger.bind(password="***")` 或过滤）

#### 5. scripts/login_qianlima.py（完整脚本）
文档 16.4 提到，但没给代码。需要：
- 命令行参数解析（`argparse`）
- `getpass.getpass()` 交互输入
- 调用 `qianlima_login.login_and_save_cookies()`
- 成功/失败退出码

### 🔥 S-13：千里马真实 DOM 调研

文档 14/17 节反复强调"真实 URL 和 DOM 必须现场确认"。

**任务**：用 Playwright 实际访问 qianlima.com，记录：
- 登录页 URL（确认是 `https://www.qianlima.com/login` 还是其他）
- 用户名输入框 CSS 选择器（`#username` / `input[name="username"]` / 其他）
- 密码输入框 CSS 选择器
- 登录按钮选择器
- 验证码图片选择器 + 是否需要点击
- 登录成功后的 URL 跳转目标
- 用户中心元素选择器（文档 16.3 列了候选，需要确认哪个真实存在）
- 搜索结果页 URL 格式（确认 `https://www.qianlima.com/zb/kw-{topic}/` 是否正确）
- 招标条目列表的 CSS 选择器
- 单条招标详情页选择器（5 字段：标题/金额/日期/地区/联系方式）

**输出要求**：一份 `qianlima-dom.json` 文件，含所有选择器，供代码直接引用

**预估成本**：$0.3

### 🔥 S-14：补完整测试代码

文档第 21 节列了 4 个测试文件的测试清单（共 40+ 个测试点），但没有实际代码。

**任务**：补 4 个测试文件的完整代码：
- `tests/test_push_email.py`（13 个测试点）
- `tests/test_session_manager.py`（13 个测试点）
- `tests/test_browser_pool.py`（10 个测试点）
- `tests/test_qianlima_login.py`（8 个测试点）

**要求**：
- 用 `pytest` + `pytest-asyncio`
- SMTP 测试用 `aiosmtplib` mock 或 `smtplib` mock
- Playwright 测试用 `unittest.mock.AsyncMock` mock
- 不依赖真实网络
- 每个测试有清晰的 docstring 说明测试意图

**预估成本**：$0.5

---

## 三、建议补充的（提升答辩分数）

### ⭐ S-15：Webhook 推送实现

文档第 7 节说"Webhook 尚未实现时不能返回 delivered=True"，但命题 6 要求"增量推送"，Webhook 是企业级推送的标准方式。

**任务**：在 push.py 中实现 Webhook 推送：
- 用 `httpx.AsyncClient` 发 POST 请求
- 支持 HMAC 签名（`X-ScrapeFlow-Signature` header）
- 超时 10 秒
- 失败重试 2 次（间隔 1/2 秒）
- 响应非 2xx 视为失败
- Webhook URL 用 `is_safe_url()` 防 SSRF（已有工具函数）

**加分点**：答辩时可以说"支持邮件 + Webhook 双通道推送，Webhook 支持 HMAC 签名验证"

**预估成本**：$0.3

### ⭐ S-16：Outbox Pattern 实现（企业级加分）

文档 25.2 提到"邮件已发送但数据库提交失败"的问题，说"最终企业级方案应采用 Outbox Pattern"，但没实现。

**任务**：实现简化版 Outbox Pattern：
- 新增 `push_outbox` 表：`id` / `subscription_id` / `tender_ids` (JSON) / `status` / `message_id` / `created_at` / `sent_at`
- 推送前先写 outbox（status=pending）
- 推送成功后更新 outbox（status=sent）+ 写 PushLog
- 推送失败后 outbox 保持 pending，下次定时任务重试
- 答辩时说"采用 Outbox Pattern 解决外部副作用与数据库事务的原子性问题"

**加分点**：这是企业级分布式系统的标准模式，评委加分高

**预估成本**：$0.4

### ⭐ S-17：Dockerfile Python 3.13 修复

文档第 20 节指出 Dockerfile 用 Python 3.11 但项目用 3.13，但没给最终版本。

**任务**：重写 Dockerfile：
- `FROM python:3.13-slim AS builder` + `FROM python:3.13-slim`
- 多阶段构建
- 非 root 用户
- healthcheck
- site-packages 路径修正
- Playwright 系统依赖安装

**预估成本**：$0.1

### ⭐ S-18：答辩技术亮点提炼

文档主要是实施细节，缺答辩用的"亮点提炼"。

**任务**：写一份 `答辩技术亮点.md`，每项亮点配 1 段话术 + 1 个数据：
- 三阶段批量 SimHash 去重（N+1 根治，性能提升 Nx）
- SAVEPOINT 部分事务（单平台失败不影响其他平台）
- SSRF 三层防护（初始 URL + page.route + DNS LRU 缓存）
- LIKE 注入全局防护（safe_contains + ESCAPE 子句）
- SQLite WAL + busy_timeout（并发写缓解）
- 反幻觉校验链路（金额带单位 + 日期归一化 + 原文比对）
- Outbox Pattern（外部副作用事务一致性）
- 多通道推送（SMTP + Webhook + HMAC 签名）
- 完整 storage_state 登录态（不是简单 Cookie）
- BrowserPool 有界池（自动归还 + 超时 + 部分失败清理）
- 170+ 单元测试覆盖（核心算法 + 企业级特征）
- 轻量迁移方案（无 Alembic，PRAGMA + ALTER TABLE）

**预估成本**：$0.2

---

## 四、不需要 Sol 做的（我能做或本地必须做）

### 我（Trae/GLM）能直接做的
- ✅ config.py 加配置字段
- ✅ subscription.py 加 notify_email / webhook_url 字段
- ✅ database.py 加迁移条目
- ✅ subscribe.py API 加推送字段
- ✅ push.py 改造推送顺序（先推送再记 PushLog）
- ✅ scraper.py 注入 storage_state
- ✅ collector.py 加千里马平台
- ✅ main.py 加 session 目录验证
- ✅ .env.example / docker-compose.yml 配置更新
- ✅ qianlima.py 模板改造（用 SessionManager 替代固化 Cookie）

### 必须本地做的（Sol 远程做不了）
- 🔧 运行 pytest 全量回归
- 🔧 SMTP 真实联调（需要授权码）
- 🔧 千里马人工登录（需要账号 + 验证码）
- 🔧 千里马真实 DOM 现场确认（如果 Sol 不做 S-13）

---

## 五、Sol 续作任务汇总

| 任务 | 优先级 | 预估成本 | 必要性 |
|---|---|---|---|
| **S-12** 补 5 个完整源码文件 | 🔥🔥🔥 | $0.6 | 必须（不补无法落地） |
| **S-13** 千里马真实 DOM 调研 | 🔥🔥🔥 | $0.3 | 必须（不补采集失败） |
| **S-14** 补 4 个完整测试文件 | 🔥🔥 | $0.5 | 强烈建议 |
| **S-15** Webhook 推送实现 | ⭐ | $0.3 | 加分 |
| **S-16** Outbox Pattern | ⭐ | $0.4 | 加分 |
| **S-17** Dockerfile 3.13 修复 | ⭐ | $0.1 | 加分 |
| **S-18** 答辩技术亮点 | ⭐ | $0.2 | 加分 |

**总成本估算**：
- 必做（S-12 + S-13 + S-14）：$1.4 ≈ ¥10
- 全做（含加分项）：$2.4 ≈ ¥17

---

## 六、给 Sol 的执行指令

```text
请基于上一轮《ScrapeFlow_Sol_P0_交付文档.md》继续完成以下任务：

【必做】
1. S-12：补 5 个完整源码文件（email_sender.py / session_manager.py / browser_pool.py / qianlima_login.py / login_qianlima.py 脚本）
   - 不要只给接口签名和片段，要给完整可粘贴的 .py 文件
   - 严格按上一轮文档的设计实现
   - 注意 _pool_lock 不能在模块导入时创建
   - 注意 Message-ID 生成、邮件头注入防护、附件 MIME 编码

2. S-13：千里马真实 DOM 调研
   - 用 Playwright 实际访问 qianlima.com
   - 记录登录页 + 搜索页所有 CSS 选择器
   - 输出 qianlima-dom.json

3. S-14：补 4 个完整测试文件
   - 按上一轮文档第 21 节的测试清单
   - 用 pytest + pytest-asyncio
   - 全部 mock，不依赖真实网络

【加分】
4. S-15：Webhook 推送实现（httpx + HMAC 签名 + SSRF 防护）
5. S-16：Outbox Pattern（push_outbox 表 + 状态机 + 重试）
6. S-17：Dockerfile Python 3.13 修复
7. S-18：答辩技术亮点提炼（12 项，每项 1 段话术 + 1 个数据）

【输出要求】
- 每个文件用分隔符标识路径，合并成一个 .txt 文件
- 不要再给设计文档，直接给代码
- 测试代码必须能直接运行（不缺 import，不缺 fixture）

【预算】
- 必做：$1.4
- 全做：$2.4
```

---

## 七、当前项目状态回顾（给 Sol 参考）

- 测试基线：170 passed
- 代码质量：9.3 / 10
- 命题覆盖：8.5 / 10（5 项达成 + 2 项部分）
- 企业级成熟度：8.8 / 10
- 已完成轮次：8 轮豆包审查 + 修复
- 已有：SSRF 三层防护 / SimHash 三阶段批量 / 反幻觉链路 / SAVEPOINT / LRU 缓存 / safe_contains / SQLite WAL + 迁移 / 170 单元测试

**Sol 这一轮交付后预期**：
- 命题 2（登录态采集）→ 代码完成（待真实登录）
- 命题 6（增量推送）→ 代码完成（待 SMTP 联调）
- 6+1 项命题硬要求全部代码闭环
- 测试数 170 → 220+（新增 50 个测试）
- 企业级成熟度 8.8 → 9.2+
