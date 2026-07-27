# BidAgent — 智能标讯助手

> AI+金融方向的招投标数据服务系统。六 Agent 协同架构，将自然语言需求转化为 Word 报告与邮件推送，为供应链金融机构提供供应商信用评估与风险预警。

**当前状态**：MVP 已完成核心功能（GOAI 世界人工智能开源大赛 · 无界应用赛道 · AI+金融方向）

## 核心能力

### 六 Agent 协同架构
意图解析 → 采集执行 → 数据加工 → 质量保障 → 金融分析 → 报告交付

| Agent | 职责 | 关键技术 |
|-------|------|----------|
| ① 意图解析 | 自然语言 → 5 槽位 + 多轮追问 | DeepSeek V3 + Pydantic Schema |
| ② 采集执行 | 4+ 平台并行抓取 + 登录态 | Playwright + patchright stealth |
| ③ 数据加工 | 字段对齐 + 分类 + 相关性 | jieba + TF-IDF |
| ④ 质量保障 | SimHash 去重 + 反幻觉校验 | 64 位指纹 + 事实比对 |
| ⑤ 金融分析 ⭐ | BOQ + 废标 + 信用评分 | 三大子模块（见下） |
| ⑥ 报告交付 | Word + SMTP + Webhook | python-docx + at-least-once |

### 多平台采集
- ccgp.gov.cn / chinabidding.com.cn / ggzy.gov.cn / vip.qianlima.com
- 千里马登录态采集（16 cookies 持久化实测通过）
- 浏览器反检测 + 浏览器池复用

### 金融分析（核心差异化）
- **BOQ 报价异常检测**：20 类基准价格库，识别围标/劣质供货风险
- **废标风险预警**：18 条规则覆盖排他性资质/付款风险/交货期/资质门槛，含否定语境检测
- **供应商信用评分**：活跃度 30% + 中标率 40% + 偏离度 30% 三维度加权

### 质量保障（W2 核心成果）
- SimHash 64 位去重（汉明距离 ≤ 3）
- 反幻觉校验：金额/日期归一化 + 原文事实比对，无依据字段不展示
- **证据验证闭环**：5 级降级匹配 + 双坐标映射 + IoU 边界评测 + A/B/C 消融实验
  - recall 87.10% · precision 74.36% · IoU 0.6929 / 0.9242（matched）

### 推送与安全
- SMTP 邮件 + Webhook HMAC 签名，at-least-once 语义 + content_hash 30 分钟幂等去重
- SSRF 三层防护 / LIKE 注入防护 / 邮件头注入防护 / 路径穿越防护

## 测试与质量

```
571 passed, 1 skipped, 0 errors / 0 failures
```

- 六 Agent 协作框架
- BOQ 异常检测引擎 / 废标风险预警引擎 / 供应商信用评分
- SimHash / 反幻觉 / 邮件推送 / 订阅 / 采集器 / 登录态
- W2 证据验证闭环（5 级匹配 + IoU 评测 + 消融实验）
- 企业级特征（SSRF / 路径穿越 / LIKE 注入 / 幂等去重）
- W2-06 前端字段高亮（12 项 Playwright + 9 项冒烟测试）

## 前端 Demo

- 详情页：字段高亮原文对应位置（基于金标数据）
- 聊天页：6 Agent 协作进度面板
- 路径：`/ui/tenders/1` / `/ui/chat`

## 安装与启动

### 环境准备
- Python 3.11+
- Playwright Chromium 二进制

### 安装依赖

```bash
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
- 聊天 Demo: http://localhost:8000/ui/chat
- 详情页 Demo: http://localhost:8000/ui/tenders/1?doc=tender_06_4e47868721c5

### Docker 启动

```bash
docker-compose up -d
```

详见 [DEPLOY.md](DEPLOY.md)。

## 测试

```bash
pytest -v
```

## 文档目录索引

| 文档 | 位置 | 说明 |
|---|---|---|
| 设计文档 | `docs/superpowers/specs/2026-07-20-bidagent-goai-design.md` | 六 Agent 架构 + 金融方向定位 + 合规边界 |
| 方案 PPT | `docs/proposal.pptx` | GOAI 初赛 28 页方案 PPT |
| 合规声明 | `docs/compliance.md` | 数据来源 / 隐私保护 / 风险提示 / 行业边界 |
| 部署文档 | `DEPLOY.md` | Docker 部署说明 |
| 详设文档 | `docs/详设文档.md` | 详细设计文档 |

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
- 遵守《数据安全法》《个人信息保护法》相关要求

### 风险提示
- BOQ 异常检测/废标风险/供应商信用评分均为**决策辅助**，不构成金融建议
- 报告中明确标注「AI 生成，仅供参考，决策请人工复核」
- 定位为数据服务商，不提供金融建议，不承担金融决策责任

详见 [docs/compliance.md](docs/compliance.md)。

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

- 所有中间件用 async/await，不用 callback 风格
- API key 用 SHA256 hash 存储，不用明文
- 环境变量密钥用 `secrets.token_hex(32)` 生成 64 字符 hex
- 单文件 ≤ 300 行
- SSRF 三层防护 / LIKE 注入防护 / 邮件头注入防护 / 路径穿越防护
- 异步函数用 `run_in_executor` 卸载同步 CPU/IO 任务
- 结构化日志带 request_id 上下文
- 统一错误响应 `{code, data, msg}`
- Docker 多阶段构建 + non-root 用户 + healthcheck

## 团队

**标小智团队**

| 成员 | 学校 · 专业 | 职责 |
|------|------------|------|
| 徐浚钊 | 上海建桥学院 · 计算机科学与技术 | 项目负责人 / 全栈开发 |
| 王祯明 | 上海建桥学院 · 计算机科学与技术 | 数据标注 / 质量测试 |

## 开源协议

本项目采用 **Open Core 模式**：

- **基础框架**（Apache License 2.0）：Agent 协作框架、SimHash 去重、反幻觉校验、采集器、浏览器池等通用工程组件
- **金融分析模块**（保留版权）：`app/processors/boq_engine.py`、`app/processors/risk_engine.py`、`app/processors/supplier_risk.py` 及相关基准价格库、规则库为专有代码，仅供评估与授权使用

六大可独立复用组件已开源：
1. Agent 协作框架（纯 Python async 图，不依赖 langgraph）
2. SimHash 去重（64 位指纹 + 汉明距离 ≤ 3）
3. 反幻觉校验（金额/日期归一化 + 原文事实比对）
4. 证据定位引擎（5 级降级匹配 + 双坐标映射）
5. 浏览器池 + 登录态（反检测 + storage_state 持久化）
6. 推送幂等（at-least-once + content_hash 去重）

详见 [LICENSE](LICENSE)。
