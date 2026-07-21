# BidAgent — AI+金融智能标讯助手 设计文档

> **版本**：v1.0
> **日期**：2026-07-20
> **赛事**：GOAI 世界人工智能开源大赛 · 无界应用赛道 · AI+金融方向
> **团队**：智汇标讯
> **代码基础**：基于 ScrapeFlow 现有代码库新增 `app/agents/` 层，复用全部 221 测试 + 核心模块

---

## 1. 项目背景与定位

### 1.1 赛事硬要求（来自 goaihz.com 官网）

**无界应用赛道 · AI+金融方向重点验证**：
- 资料理解（招投标文件解析）
- 规则匹配（废标规则 + BOQ 价格基准）
- 风险提示（废标风险 + 报价异常）
- 投研整理（多源数据聚合 + 报告生成）
- 流程辅助（投标决策辅助）

**核心作品要求**：
1. 至少一个可演示、可验证的任务闭环
2. 清楚说明：目标用户、场景痛点、交互流程、技术路线、数据来源、合规边界、后续迭代计划
3. 具备可运行 Demo、视频演示或等价可验证材料
4. 说明：模型、Agent 架构、工具接口、知识库、数据处理、部署方式
5. 数据授权、隐私保护、风险提示、行业边界明确说明
6. 鼓励开放复用的应用模板、工具组件、示例数据

**评审维度**：行业场景价值 / Agent 能力与任务闭环 / 产品体验与 Demo 完成度 / 技术实现深度与工程可复现性 / 安全、合规与开放复用价值

**提交节点**：
- 初赛（7.16-8.16）：作品简介 + 方案 PPT/PDF + 可选原型或视频
- 复赛（8.25-9.23）：更新方案 + Demo + 运行说明 + 代码或等价工程材料
- 决赛（9.22-9.23）：路演 PPT + 现场 Demo + 最终工程材料

### 1.2 项目定位

**产品名**：BidAgent — 智能标讯助手
**一句话定位**：为供应链金融机构提供招投标数据聚合与供应商信用评分 API 的 AI Agent 应用

**混合合规定位**（法律+商业+技术三层）：
- **法律层面**：决策辅助工具，免责声明清晰，不提供金融建议，不承担决策责任
- **商业叙事**：招投标数据服务商，为银行/保理公司提供 BOQ 异常检测和供应商信用评分 API
- **技术展示**：招投标信息聚合平台，集成 BOQ 异常检测和供应商风控 AI 能力

**关键话术**：「BidAgent 定位为招投标数据服务商，为金融机构提供 BOQ 异常检测和供应商信用评分 API。本工具仅提供信息聚合和 AI 分析辅助，最终决策由金融机构自行承担。」

### 1.3 与 ScrapeFlow 的关系

BidAgent 是 ScrapeFlow 的产品化包装，不是独立项目：
- **代码组织**：在现有 `scrapeflow/` 上新增 `app/agents/` 层，复用全部 221 测试和核心模块
- **工程规范继承**：智汇标讯企业级工程硬性规则（async/await、SHA256、Dockerfile 多阶段、SSRF 三层防护、LIKE 注入防护、单文件 ≤300 行等）全部继承
- **产品叙事转换**：从"招投标信息聚合工具"转换为"AI Agent 智能标讯助手"，技术栈不变，故事重塑

---

## 2. 六 Agent 协同架构

### 2.1 架构图

```
用户输入自然语言
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ ① 意图解析 Agent   (app/agents/intent_agent.py)          │
│ 自然语言 → 5 槽位（关键词/地区/预算/时间/品类）+ 多轮追问  │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ ② 采集执行 Agent   (app/agents/collector_agent.py)       │
│ 调度 4+ 平台采集器并行抓取（ccgp/chinabidding/ggzy/千里马）│
│ 复用：anti_detect.py / browser_pool.py / session_manager │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ ③ 数据加工 Agent   (app/agents/processor_agent.py)       │
│ 字段对齐 + 分类标注 + 相关性评分                           │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ ④ 质量保障 Agent   (app/agents/quality_agent.py)         │
│ SimHash 64位去重 + 反幻觉校验（金额/日期归一化 + 事实比对）│
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ ⑤ 金融分析 Agent   (app/agents/finance_agent.py) ⭐核心   │
│ BOQ 报价异常检测 + 废标风险预警 + 供应商信用评分          │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ ⑥ 报告交付 Agent   (app/agents/delivery_agent.py)        │
│ Word 报告生成 + SMTP 邮件推送 + Webhook HMAC 签名推送    │
└─────────────────────────────────────────────────────────┘
    │
    ▼
用户收到 Word 报告 + 邮件通知
```

### 2.2 各 Agent 职责详解

#### ① 意图解析 Agent

- **职责**：用户说"找上海最近7天的IT采购项目" → 拆解为关键词、地区、预算、时间窗口、品类
- **输入**：自然语言查询字符串 + 可选对话历史
- **输出**：`ParsedFilters`（5 槽位：query / region / budget / time_window / category）
- **核心能力**：
  - 调用 DeepSeek V3 LLM 做意图理解
  - 关键词降级兜底（LLM 不可用时走规则匹配）
  - 多轮追问（slot 缺失时反问用户）
- **复用**：`app/llm/parser.py` + `app/llm/prompts.py` + `app/llm/schemas.py`
- **新增**：多轮追问逻辑（slot 缺失检测 + 反问生成）

#### ② 采集执行 Agent

- **职责**：调度多平台采集器并行抓取，管理登录态
- **输入**：`ParsedFilters`
- **输出**：`list[RawTenderItem]`（未加工的招标信息）
- **核心能力**：
  - 4+ 平台并行采集（ccgp/chinabidding/ggzy/千里马）
  - 千里马登录态采集（已实测通过，16 cookies 持久化）
  - 浏览器反检测 + 浏览器池
  - 任务状态上报（progress / started_at / completed_at）
- **复用**：`app/templates/*` + `app/core/scraper.py` + `app/core/browser_pool.py` + `app/core/session_manager.py` + `app/core/anti_detect.py`（Sol 已交付）
- **新增**：包装为 Agent 接口（实现 `AgentFunc` 签名）

#### ③ 数据加工 Agent

- **职责**：字段对齐、分类标注、相关性评分
- **输入**：`list[RawTenderItem]`
- **输出**：`list[ProcessedTenderItem]`（字段标准化后的招标信息）
- **核心能力**：
  - 字段对齐（不同平台字段名映射到统一 schema）
  - 分类标注（IT/工程/医疗等品类标注）
  - 相关性评分（基于用户查询的 TF-IDF 相似度）
- **复用**：`app/processors/tender_ingestor.py` + `app/processors/tender_utils.py`
- **新增**：包装为 Agent 接口

#### ④ 质量保障 Agent

- **职责**：去重 + 反幻觉校验
- **输入**：`list[ProcessedTenderItem]`
- **输出**：`list[QualityCheckedTenderItem]`（去重 + 校验通过的招标信息）
- **核心能力**：
  - SimHash 64 位去重（三阶段批量 + SAVEPOINT）
  - 反幻觉校验（金额归一化 + 日期归一化 + 事实比对）
  - 溯源引用（每条数据标注来源 URL + 提取片段）
- **复用**：`app/processors/simhash.py` + `app/processors/hallucination_checker.py`
- **新增**：包装为 Agent 接口

#### ⑤ 金融分析 Agent（核心卖点）

- **职责**：BOQ 报价异常检测 + 废标风险预警 + 供应商信用评分
- **输入**：`list[QualityCheckedTenderItem]`
- **输出**：`list[FinanceAnalyzedTender]`（含风控评分的招标信息）
- **核心能力**（三子模块）：

  **5.1 BOQ 报价异常检测**（吸收完整版 `boq_engine.py` 并修复 bug）
  - 20 类常见采购品类基准价格库（充电桩/服务器/电脑/交换机等）
  - 正则提取"数量+单位+品名"和"品名+数量+单位"两种模式
  - 按市场均价 ±std 判定 underpriced/overpriced/normal
  - 输出 BOQReport 含 score + risk_level
  - **Bug 修复**：双向包含匹配过宽松 → 改为精准品类匹配 + 评分归一化

  **5.2 废标风险预警**（吸收完整版 `risk_engine.py` 并修复 bug）
  - 18 条规则覆盖排他性资质、付款风险、交货期、资质门槛
  - 输出 RiskReport 含 score + risk_items + qualification_gaps
  - **Bug 修复**：`any` 应为 `all`（多词组合规则逻辑错误）+ `seen` 去重逻辑修正

  **5.3 供应商信用评分**（吸收完整版 `supplier_risk.py` 并重构）
  - **去掉伪造的联邦学习实现**（PPT 不讲联邦学习，避免答辩翻车）
  - 重新设计为「基于采购历史数据的供应商信用评分模型」
  - 三个维度：投标活跃度 + 中标率 + 平均报价偏离度
  - 输出 SupplierRiskReport 含 dimensions + flags + score
- **复用**：完整版 `app/processors/boq_engine.py` + `risk_engine.py` + `supplier_risk.py`（修复后）
- **新增**：金融分析 Agent 接口 + 三子模块协调器

#### ⑥ 报告交付 Agent

- **职责**：生成 Word 报告 + 邮件/Webhook 推送
- **输入**：`list[FinanceAnalyzedTender]` + 用户订阅信息
- **输出**：推送结果（delivered 状态 + message_id）
- **核心能力**：
  - Word 报告生成（含金融分析章节：BOQ 异常 + 废标风险 + 供应商信用）
  - SMTP 邮件推送（已实测通过，163 邮箱实发成功）
  - Webhook HMAC 签名推送
  - at-least-once 推送 + content_hash 幂等去重（M-2 修复）
- **复用**：`app/report/docx_generator.py` + `app/core/email_sender.py` + `app/core/webhook_sender.py` + `app/scheduler/push.py`
- **新增**：Word 报告增加金融分析章节

### 2.3 Agent 编排器

复用现有 `app/agents/coordinator.py` 轻量级框架：
- 不依赖 langgraph
- 纯 Python 实现 Agent 图，状态通过 dict 传递
- 每个 Agent 是 async 函数，签名：`async def agent(state: dict) -> dict`
- 支持顺序执行 + 简单条件分支
- 完整的执行日志，便于答辩时展示协作流程

---

## 3. 技术亮点（PPT 重点展示）

### 3.1 T1 — 六 Agent 协同架构

- 意图 → 采集 → 加工 → 质检 → 金融分析 → 交付
- 每个 Agent 职责单一、技术栈清晰
- 金融分析 Agent 独立成卖点，与 AI+金融赛道主题完美契合

### 3.2 T4 — BOQ 报价异常检测（金融核心）

- 20 类基准价格库
- 正则提取 + 市场均价 ±std 判定
- 故事：检测低价中标违约风险，为供应链金融提供数据

### 3.3 T5 — 供应商信用评分（金融核心）

- 三维度评分：投标活跃度 + 中标率 + 平均报价偏离度
- 故事：为银行授信决策提供数据支撑

### 3.4 辅助亮点

- T2 浏览器反检测 + 登录态持久化（千里马 16 cookies 实测通过）
- T6 反幻觉校验（金额/日期归一化 + 事实比对）
- T7 at-least-once 推送 + content_hash 幂等去重
- T8 SSRF 三层防护 + 邮件头注入防护 + HMAC 签名
- 221 测试覆盖（核心算法 + 企业级特征）

---

## 4. 完整版资产吸收清单

### 4.1 高价值吸收（初赛前完成）

| # | 模块 | 来源 | 吸收到 | 工作量 |
|---|---|---|---|---|
| 1 | `boq_engine.py` | 完整版 `app/processors/` | 金融分析 Agent | 0.5 天（修复 bug） |
| 2 | `risk_engine.py` | 完整版 `app/processors/` | 金融分析 Agent | 0.5 天（修复 bug） |
| 3 | `supplier_risk.py` | 完整版 `app/processors/` | 金融分析 Agent | 1 天（重构去联邦学习） |
| 4 | `sources.py` + 8 个新模板 | 完整版 `app/templates/` | 采集 Agent | 1 天（30+ 数据源） |

### 4.2 中价值吸收（复赛阶段）

| # | 模块 | 来源 | 吸收到 | 工作量 |
|---|---|---|---|---|
| 5 | `proxy_manager.py` | 完整版 `app/core/` | 采集 Agent | 0.5 天 |
| 6 | `tests_deep/security*.py` | 完整版 `tests_deep/` | 项目质量 | 1 天 |
| 7 | `ai_config.py` | 完整版 `app/core/` | 意图 Agent | 0.5 天 |

### 4.3 不吸收（有严重问题）

| # | 模块 | 原因 |
|---|---|---|
| - | `user_auth.py` | SHA256 无 salt + X-User-Email 头认账，严重安全漏洞 |
| - | `dedup.py` | 与现有 simhash.py 冲突（64位 vs 128位） |
| - | `report_engine.py` | 加权公式有 bug，且与现有 docx_generator 重复 |
| - | `safe_json.py` | 无调用方，dead code |
| - | `rss_collector.py` | 无调用方，dead code |
| - | Cloudflare 部署 | 前端依赖未挂载的后端端点 |

---

## 5. Demo 视频方案

### 5.1 视频规格

- **时长**：5 分钟
- **格式**：MP4 / 1080p
- **语言**：中文配音或字幕
- **提交方式**：初赛提交时上传（可选原型或视频）

### 5.2 视频内容大纲

1. **开场（30 秒）**：产品定位 + 团队介绍
2. **任务输入（30 秒）**：在简易聊天 UI 输入"找上海最近7天的IT采购项目"
3. **意图解析（30 秒）**：展示 5 槽位解析结果
4. **多源采集（60 秒）**：展示 4 平台并行采集进度
5. **数据加工 + 质检（30 秒）**：展示去重 + 反幻觉校验
6. **金融分析（90 秒）**：⭐重点展示 BOQ 异常检测 + 废标风险 + 供应商信用评分
7. **报告交付（30 秒）**：展示 Word 报告 + 邮件推送
8. **结尾（30 秒）**：技术亮点 + 合规边界 + 后续计划

### 5.3 录制方式

- **屏幕录制**：用户用 Windows 自带录屏（Win+G）或 OBS 录制全屏，AI 负责驱动浏览器走完全流程
- **浏览器驱动**：AI 通过 browser_use 子代理操作浏览器，按视频大纲顺序演示功能
- **简易聊天 UI**：新增 `app/api/chat.py` + `app/templates/html/chat.html`（1 天工作量），替代 Swagger UI 提升产品感
- **真实数据**：使用千里马登录态（`data/sessions/qianlima_session.json`，16 cookies 已持久化）+ 163 SMTP 实发邮件（已联调通过）

---

## 6. 工程规范继承（智汇标讯企业级硬性规则）

以下规则全部从 ScrapeFlow 继承到 BidAgent，内容不变：

### 6.1 硬约束

- 所有中间件用 async/await，不用 callback 风格
- Docker 部署需 `AI_API_KEYS` 和 `LLM_TIMEOUT_SECONDS` 环境变量
- Admin 路由不被认证中间件拦截
- API key 限速用 SHA256 hash 而非明文
- 数据库联合表无 id 字段时不按 id 排序
- 环境变量密钥用 `secrets.token_hex(32)` 生成 64 字符 hex
- pytest 包含 data 目录创建
- Docker worker 服务命令用 `python -m app.worker_loop`
- Scheduler 部署需 `APP_ROLE` 环境变量区分 web/worker 角色
- SMTP 配置支持 465 (SSL) / 587 (STARTTLS) / 25 (plain) 端口
- 配置含 `APP_BASE_URL` / `WEBHOOK_SECRET` / `APP_ROLE` 字段
- CORS origins 不默认 `*`，生产环境必须显式配置域名
- 附件下载 sanitize 文件名防路径穿越，最终路径用 `ATTACHMENT_DIR` 校验
- 外部 URL 请求必须校验私网/环回/链路本地 IP 防 SSRF
- 定时订阅必须用 croniter 校验 `frequency_cron` 表达式
- 异步函数必须用 `run_in_executor` 卸载同步 CPU/IO 任务
- 增量数据查询必须用 SQL `NOT EXISTS` 而非 Python 层过滤
- LLM few-shot 示例必须用 `json.dumps()` 格式化为标准 JSON
- LIKE 查询参数必须转义特殊字符（`%`/`_`/`\`）防通配符注入
- LLM 语义缓存必须用 TTLCache 防 memory leak
- `ParsedFilters` 必须显式从 LLM 输出数据中移除 `raw_query`
- 订阅触发必须在推送成功后更新 `last_pushed_at` 字段
- PushLog 必须用 `add_all()` 批量插入
- 数据库连接池必须显式配置 `pool_size` / `max_overflow` / `pool_recycle`
- 单文件 ≤ 300 行

### 6.2 工程约定

- 结构化日志带 request_id 上下文，INFO/WARN/ERROR 级别
- 统一 error response 格式：`{code, data, msg}`
- Docker 镜像多阶段构建 + non-root 用户 + healthcheck
- `requirements.txt` 包含 pytest + pytest-asyncio
- 共享工具函数（如字体设置）提取到 common 模块
- API list 端点支持 offset 分页，含 `total` / `limit` / `offset` 参数
- 数据库 session 创建合并，避免冗余连接

---

## 7. 时间规划（方案 A：代码优先）

### 7.1 详细时间线

| 阶段 | 时间 | 任务 | 交付物 |
|---|---|---|---|
| **W1 代码骨架** | 7/21-7/22 | 六 Agent 接口骨架 + 编排器集成 | `app/agents/*.py` 6 个文件 |
| **W1 金融核心** | 7/23-7/26 | 吸收 boq_engine + risk_engine + supplier_risk 并修复 bug | 金融分析 Agent 可运行 |
| **W1 数据源扩展** | 7/27-7/28 | 吸收 sources.py + 8 个新模板 | 30+ 数据源注册 |
| **W2 资产吸收** | 7/29-7/31 | 吸收 proxy_manager + tests_deep + ai_config | 安全测试 + 多 LLM 适配 |
| **W2 聊天 UI** | 8/1 | 简易聊天 UI（`app/api/chat.py` + `chat.html`） | Demo 用 UI |
| **W2 Demo 视频** | 8/2-8/3 | 录制 5 分钟 Demo 视频 | MP4 视频 |
| **W3 PPT 制作** | 8/4-8/10 | 方案 PPT 制作（用 pptx 技能或 Kimi K3） | 方案 PPT |
| **W3 文档** | 8/11-8/12 | 作品简介 500 字 + 合规边界文档 | 初赛必需文档 |
| **W3 打磨** | 8/13-8/14 | 全量测试 + 打磨材料 | 测试报告 |
| **W4 提交** | 8/15-8/16 | 最终检查 + 提交初赛材料 | 初赛提交包 |

### 7.2 复赛阶段（9.3 截止前）

- WebUI 完善（聊天式界面 + 多轮交互）
- 知识库/RAG 接入
- Demo 视频升级
- 代码开源准备

---

## 8. 合规边界说明

### 8.1 数据来源合规

| 数据源 | 合规性 | 说明 |
|---|---|---|
| ccgp.gov.cn | ✅ 公开数据 | 中国政府采购网，政府公开数据 |
| chinabidding.com.cn | ✅ 公开数据 | 中国招标投标网，公开数据 |
| ggzy.gov.cn | ✅ 公开数据 | 全国公共资源交易平台，政府公开数据 |
| vip.qianlima.com | ⚠️ 用户授权 | 用户提供个人账号登录，仅采集用户有权访问的数据，不绕过付费墙 |

### 8.2 隐私保护

- 不采集个人隐私数据（身份证/手机号/家庭住址等）
- 供应商信用评分基于公开招投标历史数据，不涉及个人数据
- 用户账号密码用 Playwright storage_state 加密存储，不外泄

### 8.3 风险提示

- BOQ 异常检测/废标风险/供应商信用评分均为**决策辅助**，不构成金融建议
- 报告中明确标注「本报告由 AI 生成，仅供参考，决策请人工复核」
- 邮件推送内容含免责声明

### 8.4 行业边界

- 不提供金融建议
- 不承担金融决策责任
- 不持有用户资金
- 不做金融交易撮合
- 定位为数据服务商，金融机构自行决策

### 8.5 AI 反幻觉保障

- 金额归一化（万元/亿元/元统一）
- 日期归一化（多种格式统一）
- 事实比对（LLM 提取的关键事实必须在原文中找到）
- 溯源引用（每条数据标注来源 URL + 提取片段）

---

## 9. 后续迭代计划

### 9.1 初赛后（8/17-8/24）

- 根据初赛反馈调整方向
- 准备复赛 Demo 环境

### 9.2 复赛阶段（8/25-9/23）

- WebUI 完善（聊天式界面 + 多轮交互）
- 知识库/RAG 接入（招投标法规库 + 历史中标数据）
- Demo 视频升级
- 代码开源准备（GitHub repo + LICENSE + README）

### 9.3 决赛阶段（9/22-9/23）

- 路演 PPT 优化
- 现场 Demo 演练
- 答辩准备

### 9.4 长期规划

- 接入更多招投标平台（30+ → 50+）
- 接入更多 LLM 平台（10+）
- 商业化：B2B 数据 API 服务
- 开源：模板注册机制作为通用数据采集组件

---

## 10. 开放复用价值

### 10.1 可复用组件

- **ScrapeTemplate 模板化架构**：支持快速接入新平台（新增一个模板文件即可）
- **Agent 协作框架**：轻量级纯 Python 实现，可复用于其他 Agent 应用
- **SimHash 64 位去重**：通用文本去重组件
- **反幻觉校验**：通用 LLM 输出校验组件
- **BOQ 基准价格库**：20 类采购品类基准价格，可扩展
- **安全防护体系**：SSRF 三层防护 + 邮件头注入防护 + HMAC 签名 + 路径穿越防护

### 10.2 开源计划

- 初赛阶段：代码不开源（保护差异化）
- 复赛阶段：核心组件开源（Agent 框架 + 模板架构）
- 决赛阶段：完整代码开源（MIT License）

---

## 附录 A：作品简介 500 字草稿

**BidAgent — 智能标讯助手**

**问题与场景**：招投标信息分散在 ccgp、chinabidding、ggzy、千里马等多个平台，企业投标人员每天需手动浏览 4-5 个网站，耗时且易遗漏。供应链金融机构在评估供应商信用时，缺乏统一的招投标历史数据来源。

**核心方案**：六 Agent 协同架构（意图解析 → 采集执行 → 数据加工 → 质量保障 → 金融分析 → 报告交付），用户输入自然语言即可触发跨平台采集，自动生成 Word 报告并邮件推送。金融分析 Agent 独立提供 BOQ 报价异常检测、废标风险预警、供应商信用评分三大能力，为供应链金融授信决策提供数据支撑。

**创新点**：① 浏览器反检测 + 登录态持久化，突破反爬限制；② at-least-once 推送 + content_hash 幂等去重，保障金融数据不漏发不重发；③ SimHash 64 位去重 + 反幻觉校验，金融级准确率保障；④ ScrapeTemplate 模板化架构支持快速接入新平台，30+ 数据源覆盖。

**合规边界**：定位为决策辅助工具，不提供金融建议。数据来源为政府公开数据 + 用户授权登录的商业平台。供应商信用评分基于公开招投标历史数据，不涉及个人隐私。

**开源价值**：Agent 协作框架、ScrapeTemplate 模板架构、SimHash 去重组件、反幻觉校验组件均可作为通用组件复用。

---

## 附录 B：技术栈

| 层 | 技术 |
|---|---|
| 后端框架 | Python 3.13 + FastAPI |
| Agent 框架 | 纯 Python 轻量级实现（不依赖 langgraph） |
| 抓取引擎 | Playwright (async API) + httpx AsyncClient |
| 反检测 | patchright + stealth init_script |
| LLM | DeepSeek V3（主） + 多平台适配（ai_config.py） |
| 任务调度 | APScheduler + croniter |
| 去重算法 | jieba 分词 + 自实现 64 位 SimHash |
| 反幻觉 | 金额/日期归一化 + 事实比对 |
| 金融分析 | BOQ 基准价格库 + 18 条废标规则 + 供应商信用评分 |
| 报告生成 | python-docx |
| 邮件推送 | smtplib + asyncio.to_thread（SSL/STARTTLS 双路径） |
| Webhook | httpx + HMAC-SHA256 签名 |
| 数据库 | SQLite WAL（MVP） → PostgreSQL（生产） |
| ORM | SQLAlchemy 2.x async + aiosqlite |
| 安全 | SSRF 三层防护 + 邮件头注入防护 + 路径穿越防护 + LIKE 注入防护 |
| 测试 | pytest + pytest-asyncio（221 测试） |
| 部署 | Docker 多阶段构建 + non-root 用户 + healthcheck |
