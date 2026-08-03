# 标小智 — 可验证招投标数据引擎

> 面向供应链金融贷前尽调的可验证招投标数据引擎 · GOAI 2026。将不可核验的 LLM 输出转化为可复核、可追踪的数据资产——LLM 只生成候选，确定性程序负责验证。

**当前状态**：MVP 开发阶段（GOAI 世界人工智能开源大赛 · 无界应用赛道 · AI+金融方向）

## 核心定位

- **一句话定位**：面向供应链金融贷前尽调与企业采购核验的可验证招投标数据引擎
- **核心用户**：供应链金融贷前尽调人员
- **核心任务**：在审核供应商公开经营活动时，快速核验企业近期中标项目、金额、采购人及原始公告证据
- **核心差异化**：LLM 只生成字段和证据候选，确定性程序负责验证；区分项目/公告/来源/版本四层实体；区分官方原始发布、官方转载、商业转载和索引页面

## 核心能力

- **四层实体数据模型**：TenderProject（采购项目）→ TenderNotice（业务公告）→ NoticeSource（来源页面）→ NoticeVersion（抓取版本），辅以 Organization（组织实体）/ NoticeParticipant（参与关系）/ ProjectIdentifier（项目标识）
- **六 Agent 协同架构**：意图解析 → 采集执行 → 数据加工 → 质量保障 → 金融分析 → 报告交付
- **多平台采集**：ccgp / chinabidding / ggzy / 千里马（登录态采集，16 cookies 持久化）
- **可验证抽取引擎**（核心差异化）：
  - LLM 只生成字段和证据候选，确定性程序负责验证
  - 5 级降级匹配（L1 精确→L2 去空白→L3 去标点→L4 核心子串→L5 失败标记）
  - 双坐标映射（normalized_index ↔ raw_index），证据偏移量在快照中可稳定复现
  - 找不到依据的字段一律标记为无依据不输出
- **来源谱系与版本追踪**：
  - 来源角色判定（official_original / official_repost / commercial_repost / unknown）
  - 同源转载识别（SimHash 汉明距离 ≤ 3），避免将转载数量误判为独立交叉验证
  - 事实断言键（FactAssertionKey）：跨源比较前确保双方表达同一业务事实
  - 页面版本追踪，历史版本不被新版本覆盖
- **展示等级与选择性输出**：
  - 三级分类（high / review / low）：基于抽取支持度 + 来源质量 + 交叉验证状态
  - 四种输出策略：strict / default / loose / audit
- **供应商公开活动观察度**（非授信评分）：
  - 五维度：集中度 + 金额异常 + 频率异常 + 地域集中 + 采购人集中
  - 仅反映公开招投标活动观察信号，不构成授信或投资依据
- **废标风险预警**：18 条规则覆盖排他性资质、付款风险、交货期、资质门槛
- **BOQ 报价异常检测**（实验性能力）：32 类基准价格库，结果仅供研究参考
- **质量保障**：SimHash 64 位去重 + 反幻觉校验（金额/日期归一化 + 事实比对）
- **数据质量评测**：证据 recall / precision / IoU 三大指标 + Bootstrap 95% 置信区间 + 四组消融实验（A/B/C/D）
- **Web Demo**：8 个 UI 页面（工作台 / 招标检索 / 跨平台去重 / 证据验证 / 组织画像 / 质量评测 / 版本历史 / 智能问答）
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
- 826 测试全过,3 模块覆盖率 100%(bootstrap_ci/display_grade/output_strategies)

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
| 合规文档 | `_w2_report/compliance.md` | 数据来源 / 隐私保护 / 风险提示 / 行业边界 |
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
- 供应商公开活动观察度基于公开招投标数据，不涉及个人数据，不输出信用评分（v4.1 §9.1）
- 用户账号凭证经加密后通过 Playwright storage_state 存储于用户自有部署环境

### 风险提示

- BOQ 异常检测（实验性）/废标风险预警/供应商公开活动观察度均为**决策辅助**，不构成金融建议
- 报告中明确标注「AI 生成，仅供参考，决策请人工复核」
- 定位为数据服务商，不提供金融建议，不承担金融决策责任

详见 [docs/compliance.md](docs/compliance.md)。

## 当前已知限制

1. **数据源覆盖有限**：当前 4 平台，S-4 后扩展至 30+
2. **测试集 100 篇**：K3 已补抓至 100 篇(tender 33 + award 34 + correction 33)
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
