# BidAgent — 智能标讯助手

> AI+金融方向的招投标数据服务系统。六 Agent 协同架构，将自然语言需求转化为 Word 报告与邮件推送。

**当前状态**：MVP 开发阶段（GOAI 世界人工智能开源大赛 · 无界应用赛道 · AI+金融方向）

## 核心能力

- **六 Agent 协同架构**：意图解析 → 采集执行 → 数据加工 → 质量保障 → 金融分析 → 报告交付
- **多平台采集**：ccgp / chinabidding / ggzy / 千里马（登录态采集，16 cookies 持久化）
- **金融分析**（核心卖点）：
  - BOQ 报价异常检测（20 类基准价格库）
  - 废标风险预警（18 条规则，含否定语境检测）
  - 供应商信用评分（已实现）
- **质量保障**：SimHash 64 位去重 + 反幻觉校验（金额/日期归一化 + 事实比对）
- **证据验证与来源谱系**(W3):
  - 证据定位器(EvidenceLocator):为每个字段在原文中定位证据 span,支持精确匹配 + 规范化匹配 + IoU 边界评估
  - 事实断言键(fact_assertion_key):为跨公告的同一事实生成唯一键,支持多源合并
  - 来源谱系(source_lineage):识别公告来源角色(official_original/official_repost/commercial_repost/unknown),基于 SimHash 跨平台去重
  - 字段等价性校验(FieldValidator):验证 LLM 抽取值与原文证据一致性
- **展示等级与选择性输出**(W3):
  - display_grade 三级分类(high/review/low):基于支持度(support_level)+来源角色+交叉验证状态
  - 四种输出策略:strict(仅 high)/default(high + STRONG review)/loose(high + 全部 review)/audit(全部含 low)
- **数据质量评测**(W3/W4):
  - 证据检出率(recall)/证据精确率(precision)/边界 IoU 三大指标
  - Bootstrap 95% 置信区间(按 project_id 分组,1000 次重采样)
  - 四组消融实验(A: Direct LLM / B: +候选证据 / C: +程序验证 / D: 完整 BidAgent + 选择性输出)
- **Web Demo**(W3):
  - 6 个 UI 页面:查询 / 公告列表 / 质量评测 Dashboard / 来源版本链 / 组织画像 / 公告详情
  - 三分钟 Demo 流程承载(查询→去重→证据验证→拒绝冲突→组织画像→评测结果)
- **推送**：SMTP 邮件 + Webhook HMAC 签名，at-least-once 语义 + content_hash 幂等去重
- **安全**：SSRF 三层防护 / LIKE 注入防护 / 邮件头注入防护 / 路径穿越防护

## MVP 边界

**已实现**:
- 六 Agent 骨架 + 编排器
- BOQ 异常检测引擎(25 测试)
- 废标风险预警引擎(41 测试,含否定语境检测)
- 千里马登录态采集(实测通过)
- SMTP 邮件实发(163 邮箱联调通过)
- W3 证据验证与来源谱系(EvidenceLocator + FieldValidator + source_lineage)
- W3 展示等级与选择性输出(display_grade + 4 种输出策略)
- W3/W4 数据质量评测(召回/精确/IoU + Bootstrap CI + 四组消融)
- W3 Web Demo(6 个 UI 页面)
- 824 测试全过,3 模块覆盖率 100%(bootstrap_ci/display_grade/output_strategies)

**规划中**:
- 30+ 数据源注册(S-4)
- 全量 90 篇评测(等测试集冻结后运行)
- Demo 视频录制

## 安装与启动

### 环境准备

- Python 3.11+
- Playwright Chromium 二进制

### 安装依赖

```bash
cd BidAgent
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
python -m playwright install chromium
```

### 配置环境变量

```bash
cp .env.example .env
```

生成必需密钥：

```bash
# SECRET_KEY: 64 字符 hex（必须用 token_hex(32) 生成）
python -c "import secrets; print(secrets.token_hex(32))"
```

把生成结果填入 `.env` 的 `SECRET_KEY=`。同时设置至少 8 字符的 `ADMIN_SECRET`。

### 启动服务

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- API 文档 (Swagger UI): http://localhost:8000/docs
- 健康检查: http://localhost:8000/health

### Docker 启动

```bash
docker-compose up -d
```

详见 [DEPLOY.md](DEPLOY.md)。

## 测试

```bash
pytest -v
```

当前测试覆盖：
- 六 Agent 协作框架（21 测试）
- BOQ 异常检测引擎（25 测试）
- 废标风险预警引擎（41 测试，含否定语境反面用例）
- 核心模块（SimHash / 反幻觉 / 邮件推送 / 订阅 / 采集器 / 登录态）
- 企业级特征（SSRF / 路径穿越 / LIKE 注入 / 幂等去重）
- W3 证据验证(EvidenceLocator / FieldValidator / source_lineage / fact_assertion_key)
- W3 展示等级(display_grade + output_strategies 四策略)
- W3 数据质量评测(bootstrap_ci 置信区间 / 消融实验 A/B/C/D 四组)
- W3 覆盖率补强(3 模块 100%:bootstrap_ci / display_grade / output_strategies)

## 文档目录索引

| 文档 | 位置 | 说明 |
|---|---|---|
| 设计文档 | `docs/superpowers/specs/2026-07-20-bidagent-goai-design.md` | 六 Agent 架构 + 金融方向定位 + 合规边界 |
| 作品简介 | `docs/introduction_500.md` | GOAI 初赛作品简介 500 字 |
| 合规文档 | `docs/compliance.md` | 数据来源 / 隐私保护 / 风险提示 / 行业边界 |
| 部署文档 | `DEPLOY.md` | Docker 部署说明 |
| 完整版对比报告 | `docs/完整版对比报告.md` | 完整版资产吸收清单 |
| W3 评测报告 | `_w3_outputs/w3_03_evidence_report.json` | 90 篇证据评测结果(召回/精确/IoU) |
| W3 display_grade 规则 | `_w3_outputs/display_grade_rule_frozen.md` | 展示等级规则冻结文档 |
| 金标冻结 | `tests/fixtures/gold/gold_frozen_v1.json` | 90 篇金标标注冻结 |

## 数据与合规边界

### 数据来源

| 数据源 | 合规性 | 说明 |
|---|---|---|
| ccgp.gov.cn | ✅ 公开数据 | 中国政府采购网 |
| chinabidding.com.cn | ✅ 公开数据 | 中国招标投标网 |
| ggzy.gov.cn | ✅ 公开数据 | 全国公共资源交易平台 |
| vip.qianlima.com | ⚠️ 用户授权 | 用户提供个人账号登录，仅采集用户有权访问的数据，不绕过付费墙 |

### 隐私保护

- 不采集个人隐私数据（身份证/手机号/家庭住址等）
- 供应商信用评分基于公开招投标历史数据，不涉及个人数据
- 用户账号凭证经加密后通过 Playwright storage_state 存储于用户自有部署环境

### 风险提示

- BOQ 异常检测/废标风险/供应商信用评分均为**决策辅助**，不构成金融建议
- 报告中明确标注「AI 生成，仅供参考，决策请人工复核」
- 定位为数据服务商，不提供金融建议，不承担金融决策责任

详见 [docs/compliance.md](docs/compliance.md)。

## 当前已知限制

1. **数据源覆盖有限**：当前 4 平台，S-4 后扩展至 30+
2. **测试集 93 篇**：K3 正在补抓至 100 篇(tender 30 + award 32 + correction 30 → 100)
3. **cross_verified 默认 False**：交叉验证赋值需等 W4 多源合并阶段接入
4. **Web Demo 为前端 Mock 数据**：后端 demo_api.py 已预留接入点,后续改为真实 API
5. **risk_engine 基于关键词匹配**：存在一定误报率,已加否定语境检测,但仍需人工复核
6. **法条引用准确性未核实**：risk_engine 中部分 law_ref 字段为空或未核实,答辩前需补充

## 技术栈

| 层 | 技术 |
|---|---|
| 后端框架 | Python 3.13 + FastAPI |
| Agent 框架 | 纯 Python 轻量级实现（不依赖 langgraph） |
| 抓取引擎 | Playwright (async API) + httpx AsyncClient |
| 反检测 | patchright + stealth init_script |
| LLM | DeepSeek V3 |
| 任务调度 | APScheduler + croniter |
| 去重算法 | jieba 分词 + 自实现 64 位 SimHash |
| 反幻觉校验 | 金额/日期归一化 + 事实比对 + 溯源引用 |
| 数据库 | SQLite (MVP) → PostgreSQL (生产) |
| ORM | SQLAlchemy 2.0 async + aiosqlite |
| 部署 | Docker 多阶段构建 + non-root 用户 + healthcheck |

## 工程规范

继承自 BidAgent 企业级硬性规则：

- 所有中间件用 async/await，不用 callback 风格
- API key 用 SHA256 hash 存储，不用明文
- 环境变量密钥用 `secrets.token_hex(32)` 生成 64 字符 hex
- 单文件 ≤ 300 行
- SSRF 三层防护 / LIKE 注入防护 / 邮件头注入防护 / 路径穿越防护
- 异步函数用 `run_in_executor` 卸载同步 CPU/IO 任务
- 结构化日志带 request_id 上下文
- 统一错误响应 `{code, data, msg}`
- Docker 多阶段构建 + non-root 用户 + healthcheck

## 许可证

**尚未确定**。代码、依赖和数据的授权边界待确认后再决定开源许可证。
