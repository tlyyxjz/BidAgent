# 标小智 - 开源边界与原创贡献披露

> 对齐 GOAI 2026 无界应用赛道评审维度：**安全、合规与开放复用价值**。
>
> "完全开源"不是评分门槛，关键是清晰披露依赖和原创贡献。本文档明确标小智的开源边界、第三方依赖、原创贡献与开放复用价值。

---

## 1. 开源协议与边界

### 1.1 协议

**Apache License 2.0** — 全量开源，允许商业使用、修改、分发，仅需保留版权声明。

### 1.2 开源边界

| 类别 | 是否开源 | 说明 |
|---|---|---|
| 核心 Agent 架构 | ✅ 完全开源 | 六 Agent 协同架构（意图解析/采集/加工/质量/金融/交付）|
| 提示工程 | ✅ 完全开源 | `app/llm/prompts.py`，含抽取/意图解析全部 prompt |
| 验证引擎 | ✅ 完全开源 | 34 条确定性验证规则（`app/processors/evidence_locator/`）|
| 工具链 | ✅ 完全开源 | Playwright 采集器、SimHash 去重、双坐标映射 |
| 评测脚本 | ✅ 完全开源 | `scripts/eval_gold598_retest.py`，一键复现 |
| 金标数据集 | ✅ 完全开源 | `tests/fixtures/gold/gold_dataset_v4.json`（598 篇）|
| 文档 | ✅ 完全开源 | ARCHITECTURE.md / DATA_SOURCES.md / COST_ANALYSIS.md |
| Web Demo | ✅ 完全开源 | 8 个功能页 HTML/JS/CSS |
| 标注工具 | ✅ 完全开源 | `annotation_tool/`（含 app.js / schema.js）|

### 1.3 不开源的部分（无）

**本项目无不可开源的关键商业逻辑或模型权重**。标小智的核心能力来自架构设计和验证规则，不依赖自训练模型权重。LLM 能力通过第三方 API（DeepSeek）调用，无自有模型权重。

---

## 2. 第三方依赖披露

### 2.1 运行时依赖

| 依赖 | 协议 | 用途 | 是否修改 |
|---|---|---|---|
| FastAPI | MIT | Web 框架 | 否 |
| SQLAlchemy 2.0 | MIT | ORM | 否 |
| Playwright | Apache 2.0 | 浏览器自动化采集 | 否 |
| httpx | BSD-3 | HTTP 客户端 | 否 |
| APScheduler | MIT | 任务调度 | 否 |
| jieba | MIT | 中文分词（SimHash 用）| 否 |
| argon2-cffi | MIT | 密码哈希 | 否 |
| cryptography | Apache 2.0 | AES-GCM Cookie 加密 | 否 |
| slowapi | MIT | API 速率限制 | 否 |
| croniter | MIT | Cron 表达式解析 | 否 |
| ulid-py | Apache 2.0 | ULID 主键生成 | 否 |

### 2.2 LLM 服务依赖

| 服务 | 协议 | 用途 | 替代方案 |
|---|---|---|---|
| DeepSeek API | 商用 API | 字段抽取 + 证据候选生成 | 可切换至 DashScope/ZHIPU/OpenAI（`.env` 配置）|

**说明**：LLM 是外部服务调用，不是代码依赖。标小智不包含 DeepSeek 的模型权重或内部实现。切换 LLM provider 只需改 `.env`，不影响架构。

### 2.3 数据源依赖

| 数据源 | 类型 | 合规措施 |
|---|---|---|
| ccgp.gov.cn | 官方公开 | 8 秒限流 + robots.txt + 403 即停 |
| ggzy.gov.cn | 官方公开 | 8 秒限流 + robots.txt + 403 即停 |

不抓取付费内容、不绕过登录墙、不采集个人隐私数据。

---

## 3. 原创贡献（评审关注点）

### 3.1 核心原创技术（8 条差异化）

| # | 原创贡献 | 代码位置 | 复用价值 |
|---|---|---|---|
| 1 | **LLM 只生成候选，确定性程序验证** | `app/processors/evidence_locator/` | 可复用于任何"LLM 抽取 + 可验证"场景 |
| 2 | **34 条确定性验证规则**（G/A/T/D/I/E/M 七族）| `app/processors/evidence_locator/field_validator.py` | 规则库可独立复用 |
| 3 | **双坐标映射**（normalized_index ↔ raw_index）| `app/processors/evidence_locator/offset_mapping.py` | 解决 LLM 输出与原文对齐的通用问题 |
| 4 | **5 级降级匹配**（精确→空白→全半角→格式变体→模糊）| `app/processors/evidence_locator/matcher.py` | 通用文本匹配容错策略 |
| 5 | **四层实体数据模型**（Project→Notice→Source→Version）| `app/models/tender.py` | 可复用于任何多源数据治理场景 |
| 6 | **同源转载识别**（SimHash 汉明距离 ≤ 3）| `app/processors/simhash.py` | 防止转载数据误判为独立交叉验证 |
| 7 | **事实断言键**（FactAssertionKey）| `app/processors/fact_assertion_key.py` | 跨源比对前确保双方表达同一业务事实 |
| 8 | **选择性输出**（display_grade 三档，宁缺毋滥）| `app/processors/display_grade.py` | 杜绝"看起来对"的脏数据 |

### 3.2 评测体系原创

| 原创贡献 | 说明 |
|---|---|
| 四组消融实验（A/B/C/D）| 每个模块的增益可量化、可复现 |
| span 级 IoU 指标 | 证据文本边界与金标的重合度，非字段级口径 |
| null_false_positive_rate | 金标 absent 字段零误报率（v4.1 §10 新指标）|
| Bootstrap 95% 置信区间 | 项目级统计显著性 |
| 598 篇全量金标集 | 四批来源（frozen93/W3/W4/W5）血缘清晰，可复现 |

### 3.3 工程原创

| 原创贡献 | 说明 |
|---|---|
| 六 Agent 协同架构 | 纯 Python 轻量实现，不依赖 langgraph |
| 合规采集架构 | 域名级限流 + robots.txt + 来源白名单 + 403 即停 |
| 凭证安全架构 | HMAC-SHA256（API Key）+ Argon2id（密码）+ AES-GCM（Cookie）|
| 多值字段独立验证 | 每个值独立 span 证据，非聚合判断 |

---

## 4. 开放复用价值

### 4.1 可直接复用的模块

| 模块 | 复用场景 |
|---|---|
| 验证引擎（34 条规则）| 合同审查、研报核验、公告比对等文档理解场景 |
| 双坐标映射 | 任何需要 LLM 输出与原文对齐的场景 |
| SimHash 同源识别 | 任何需要跨源去重的场景 |
| 四层实体模型 | 任何多源数据治理场景（新闻/论文/法规）|
| 评测脚本 | 任何需要 span 级证据评测的场景 |

### 4.2 范式复用

标小智的核心范式——**"LLM 只生成候选，确定性程序负责验证，找不到依据不输出"**——是通用范式，可复制到：

- 合同审查（条款抽取 + 原文核验）
- 研报核验（数据抽取 + 源文件核验）
- 公告比对（多源比对 + 矛盾检测）
- 法规解读（条款抽取 + 法条定位）

### 4.3 评委验证方式

```bash
# 1. 验证引擎独立性（预期：零匹配，验证引擎不调用 LLM）
grep -r "openai\|anthropic\|chat.completion" app/processors/

# 2. 验证规则数量（预期：34 条）
grep -c "def _validate_" app/processors/evidence_locator/field_validator.py

# 3. 双坐标映射实现
grep -rn "to_normalized\|to_raw" app/processors/evidence_locator/

# 4. 一键复现评测
python scripts/eval_gold598_retest.py

# 5. 查看金标数据集
python -c "import json; d=json.load(open('tests/fixtures/gold/gold_dataset_v4.json')); print(len(d), '篇')"
```

---

## 5. 安全与合规

### 5.1 安全措施

| 维度 | 措施 |
|---|---|
| SSRF 防护 | 仅允许 HTTP/HTTPS，拦截内网/回环/链路本地/云元数据 |
| 路径穿越防护 | 白名单存储目录，文件名与存储键分离 |
| 凭证安全 | API Key HMAC-SHA256 / 密码 Argon2id / Cookie AES-GCM |
| 速率限制 | slowapi，防暴力枚举 |
| CORS | 生产环境显式域名，不默认 `*` |

### 5.2 合规措施

| 维度 | 措施 |
|---|---|
| 采集合规 | 8 秒限流 + robots.txt + 来源白名单 + 403 即停 |
| 隐私保护 | 不采集个人隐私，联系人电话/邮箱 SHA256 存储 |
| 数据删除 | 支持 5 种范围删除，记录审计日志 |
| AI 反幻觉 | 确定性验证，找不到依据不输出 |
| 风险提示 | 报告标注"AI 生成，仅供参考，决策请人工复核"|

---

## 6. 总结

| 维度 | 状态 |
|---|---|
| 开源协议 | Apache 2.0，全量开源 |
| 不可开源部分 | 无（无自有模型权重，无关键商业逻辑）|
| 第三方依赖 | 全部 MIT/Apache/BSD 兼容协议，已披露 |
| 原创贡献 | 8 条核心差异化 + 评测体系 + 工程架构 |
| 开放复用价值 | 验证引擎/双坐标映射/四层实体模型可独立复用 |
| 安全 | SSRF/路径穿越/凭证安全/速率限制 |
| 合规 | 采集合规/隐私保护/数据删除/AI 反幻觉 |

**标小智全量开源，无不可开源部分，原创贡献清晰可验证，开放复用价值明确。**
