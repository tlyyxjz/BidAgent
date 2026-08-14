# 标小智 - 系统架构说明

> 面向供应链金融贷前尽调的可验证招投标数据引擎 · GOAI 2026 无界应用赛道 · AI+金融方向
>
> 本文档说明系统整体架构、六 Agent 协同、核心差异化技术、数据模型与合规设计，供评委与审查者核查。

---

## 1. 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                        用户层 (Web Demo 8 页)                │
│  工作台 / 招标检索 / 跨平台去重 / 证据验证 / 组织画像 /     │
│  质量评测 / 版本历史 / 智能问答                              │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP REST API (12 标准端点 + Demo 端点)
┌────────────────────────▼────────────────────────────────────┐
│                  FastAPI 应用层 (app/main.py)                │
│  ├ 中间件: request_id / CORS / SlowAPI 限流 / 异常处理      │
│  ├ 路由: admin / scrape / agents / subscribe / tender / ui  │
│  └ lifespan: 数据库初始化 / 目录校验 / Sentry               │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│                  六 Agent 协同层 (app/agents/)               │
│  意图解析 → 采集执行 → 数据加工 → 质量保障 → 金融分析 →    │
│  报告交付                                                    │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────┬───────────┴───────────┬────────────┬──────────┐
│  采集层    │   LLM 抽取层          │  验证层    │  存储层  │
│ (app/core) │  (app/llm)            │(processors)│ (models) │
│ Playwright │  DeepSeek/OpenAI兼容  │ 34条规则   │ SQLite   │
│ httpx      │  JSON Schema          │ 5级降级    │ → PG     │
│ 限流/robots │  宽松解析兜底         │ SimHash    │          │
└────────────┴───────────────────────┴────────────┴──────────┘
```

### 目录结构（真实代码）

```
BidAgent/
├── app/
│   ├── main.py              # FastAPI 入口（lifespan + 中间件 + 路由挂载）
│   ├── config.py            # 配置（pydantic-settings 读 .env）
│   ├── worker_loop.py       # Worker 角色（APP_ROLE=worker 时运行）
│   ├── scheduler_loop.py    # Scheduler 角色
│   ├── agents/              # 六 Agent 协同
│   │   ├── coordinator.py   #   协调器（编排六 Agent 顺序）
│   │   ├── intent_agent.py  #   意图解析（自然语言 → 结构化查询）
│   │   ├── collector_agent.py  # 采集执行（调 core/scraper）
│   │   ├── processor_agent.py  # 数据加工（调 llm/extractor + processors）
│   │   ├── quality_agent.py    # 质量保障（证据验证 + 展示分级）
│   │   ├── finance_agent.py    # 金融分析（6 个观察信号）
│   │   ├── delivery_agent.py   # 报告交付（Word 生成 + 推送）
│   │   └── pipeline.py         # Pipeline 会话状态机
│   ├── api/                 # API 路由
│   │   ├── v41_api.py       #   v4.1 12 个标准端点
│   │   ├── tender.py / scrape.py / subscribe.py / agents.py
│   │   ├── ui.py            #   Web Demo 页面
│   │   ├── real_demo.py     #   Demo 数据接口
│   │   └── admin.py / auth.py
│   ├── core/                # 采集核心
│   │   ├── scraper.py       #   抓取调度（SSRF + robots + 限流）
│   │   ├── scraper_playwright.py  # Playwright 抓取
│   │   ├── http_fetcher.py  #   httpx 异步抓取
│   │   ├── rate_limiter.py  #   域名级 8 秒频率限制
│   │   ├── robots_checker.py #  robots.txt 合规检查
│   │   ├── source_whitelist.py # 来源白名单
│   │   ├── browser_pool.py  #   浏览器池
│   │   ├── snapshot_manager.py # 页面快照管理
│   │   └── webhook_sender.py / email_sender.py
│   ├── llm/                 # LLM 抽取
│   │   ├── extractor.py     #   抽取主流程
│   │   ├── provider.py      #   多模型 provider（deepseek/dashscope/zhipu/openai）
│   │   ├── parser.py        #   宽松 JSON 解析（去围栏/截取/尾逗号修复）
│   │   ├── extraction_schemas.py # JSON Schema 定义
│   │   └── prompts.py       #   Prompt 模板
│   ├── processors/          # 数据加工与验证
│   │   ├── evidence_locator/ #  证据定位引擎（5 级降级匹配）
│   │   │   ├── _locator.py      #   定位主流程
│   │   │   ├── _matchers.py     #   5 级匹配器
│   │   │   ├── _coordinate_mapping.py # 双坐标映射
│   │   │   └── _verify.py       #   验证
│   │   ├── field_validator.py     # 字段验证（34 条规则）
│   │   ├── field_validator_amount.py / _date.py / _identifier.py
│   │   ├── simhash.py         #   64 位 SimHash + jieba 分词
│   │   ├── source_lineage.py  #   来源谱系（官方/转载判定）
│   │   ├── fact_assertion_key.py # 事实断言键
│   │   ├── display_grade.py   #   展示等级（high/review/low）
│   │   ├── hallucination_checker.py # 幻觉检测
│   │   ├── observation_signals.py #  6 个观察信号
│   │   └── tender_ingestor.py #   数据入库
│   ├── models/              # 数据模型（SQLAlchemy 2.0 async）
│   │   ├── database.py      #   引擎 + init_database
│   │   ├── tender.py        #   Tender 表
│   │   ├── _extracted_field.py # ExtractedField
│   │   ├── evidence.py      #   Evidence 表
│   │   ├── organization.py  #   Organization
│   │   └── _tender_*_entities.py # 四层实体
│   ├── eval/                # 质量评测
│   │   ├── bootstrap_ci.py  #   Bootstrap 95% 置信区间
│   │   └── set_metrics.py   #   recall/precision/IoU
│   ├── report/              # 报告生成
│   │   ├── docx_generator.py #  Word 报告
│   │   └── docx_sections.py / docx_components.py
│   ├── scheduler/           # 定时调度（APScheduler + croniter）
│   ├── services/data_deletion/ # 数据删除服务（5 种范围）
│   ├── templates/           # 站点适配模板（ccgp / ggzy / qianlima ...）
│   └── utils/               # 工具
│       ├── credentials.py   #   凭证安全（Argon2id / AES-GCM / HMAC）
│       ├── api_key.py       #   API Key 摘要
│       ├── aes_crypto.py    #   AES-GCM 加密
│       ├── url_safety.py    #   SSRF 防护
│       └── logger.py        #   结构化日志
├── tests/                   # 测试（2031 passed）
├── examples/                # 示例输入输出（3 条真实公告）
├── static/                  # Web Demo 静态资源
├── data/bidagent.db         # SQLite 数据库
├── run_demo.py              # 一键启动脚本
└── requirements.txt
```

---

## 2. 六 Agent 协同架构

| 顺序 | Agent | 文件 | 职责 | 输入 | 输出 |
|---|---|---|---|---|---|
| 1 | 意图解析 | `intent_agent.py` | 自然语言查询 → 结构化筛选条件 | 用户文本 | ParsedFilters（region/time_range/industry/notice_type/keywords）|
| 2 | 采集执行 | `collector_agent.py` | 调用 scraper 抓取官方公告 | ParsedFilters | 原始公告文本 + 元数据 |
| 3 | 数据加工 | `processor_agent.py` | LLM 抽取 + 确定性验证 | 原始公告文本 | ExtractedField + Evidence |
| 4 | 质量保障 | `quality_agent.py` | 证据验证 + 展示分级 + 交叉验证 | 字段 + 证据 | display_grade / support_level / cross_verify_status |
| 5 | 金融分析 | `finance_agent.py` | 6 个观察信号计算 | 已验证字段 | observation_signals（不输出信用评分）|
| 6 | 报告交付 | `delivery_agent.py` | Word 报告生成 + 邮件/Webhook 推送 | 全部产物 | .docx 文件 + PushLog |

### 协调器（`coordinator.py`）

按顺序编排六 Agent，维护 `PipelineSession` 状态机，前端通过 `/api/agents/pipeline/{session_id}` 轮询实时进度。

---

## 3. 核心差异化：LLM 只生成候选，确定性程序验证

### 3.1 抽取流程

```
公告原文 → LLM 生成字段候选 + 证据候选
          ↓
          确定性程序在原文快照中搜索验证（5 级降级匹配）
          ↓
          找到依据 → verified=true，输出字段 + 证据
          找不到依据 → 标记无依据，不输出
```

### 3.2 五级降级匹配（`processors/evidence_locator/_matchers.py`）

| 级别 | 方法 | 说明 |
|---|---|---|
| L1 | `_match_exact` | 精确匹配（原文 = 候选值）|
| L2 | `_match_stripped` | 空白归一化后匹配 |
| L3 | `_match_no_punct` | 全半角统一 + 去标点后匹配 |
| L4 | `_match_substring` | 金额/日期格式变体匹配 |
| L5 | `_match_fuzzy` | 模糊匹配 / 失败标记 |

### 3.3 双坐标映射（`_coordinate_mapping.py`）

- `normalized_index`（归一化文本偏移）↔ `raw_index`（原文快照偏移）
- 证据偏移量在快照中可稳定复现，前端不依赖实时网页 DOM
- 验证方法：`grep -rn "OffsetMapping\|to_normalized\|to_raw" app/processors/evidence_locator/`

### 3.4 验证规则（`field_validator*.py`，34 条规则，G/A/T/D/I/E/M 七族）

- **G 族**：通用规则（空值/类型/长度）
- **A 族**：金额规则（单位转换/货币/税状态）
- **T 族**：日期规则（格式/范围/相对时间）
- **D 族**：去重规则（SimHash/事实断言键）
- **I 族**：标识符规则（项目编号格式）
- **E 族**：证据规则（偏移量/快照哈希）
- **M 族**：多值规则（多中标人/多分包）

完整规则清单见 `docs/验证规则清单_v1.0.md`。

### 3.5 核心差异化验证（grep 命令）

```bash
# 1. 验证引擎是否真独立于 LLM（预期：零匹配）
grep -r "openai\|anthropic\|chat.completion\|AsyncOpenAI" app/processors/

# 2. 双坐标映射实现
grep -rn "OffsetMapping\|to_normalized\|to_raw" app/processors/evidence_locator/

# 3. 5 级降级匹配
grep -rn "_match_exact\|_match_stripped\|_match_no_punct\|_match_substring" app/processors/evidence_locator/

# 4. 403 即停不重试
grep -n "HttpForbiddenError\|403\|raise" app/core/scraper.py

# 5. 凭证安全（nonce 随机 + 防时序攻击 + Argon2id 参数）
grep -n "os.urandom\|compare_digest\|memory_cost\|time_cost" app/utils/credentials.py

# 6. SimHash 用 jieba 分词
grep -n "jieba\|_tokenize\|threshold" app/processors/simhash.py
```

---

## 4. 四层实体数据模型（`app/models/`）

```
TenderProject（采购项目）
  └── TenderNotice（业务公告）
        ├── NoticeParticipant（公告参与关系）
        └── NoticeSource（来源页面）
              └── NoticeVersion（抓取版本）
                    └── ExtractedField（抽取字段）
                          └── FieldEvidenceLink
                                └── Evidence（字段证据）
```

### 辅助实体

- `Organization`（组织机构）：113 条
- `NoticeParticipant`（参与关系）
- `ProjectIdentifier`（项目标识）
- `FactAssertionKey`（事实断言键）：跨源比较前确保双方表达同一业务事实

### 主键策略

所有核心实体使用无业务含义的内部稳定主键（ULID，`ulid-py` 库），不使用业务编号作主键。

### 数据库真实数字（2026-08-11 实测）

| 表 | 行数 | 说明 |
|---|---|---|
| tenders | 701 | 公告总数（全部 ccgp）|
| extracted_fields | 582 | 抽取字段总数 |
| evidence | 586 | 证据总数 |
| notice_sources | 721 | 来源页面 |
| notice_versions | 721 | 页面版本 |
| organizations | 113 | 组织机构 |

按 `notice_type` 分组：tender 516 / award 104 / correction 79 / 未分类 2。
有抽取字段的公告数：154。

---

## 5. 合规采集架构（`app/core/`）

```
URL 输入
  ↓
SSRF 防护（url_safety.py：拦截内网/回环/链路本地/云元数据）
  ↓
robots.txt 合规检查（robots_checker.py：30 分钟域名级缓存，不可达默认允许）
  ↓
来源白名单检查（source_whitelist.py：运行时可下架/启用）
  ↓
域名级频率限制（rate_limiter.py：8 秒间隔，按域名独立计数，失败回滚 reservation）
  ↓
模板合并（templates/ccgp.py 等）
  ↓
Playwright / httpx 抓取
  ↓
403/封禁 → 立即停止，不重试，不规避
```

### 数据删除服务（`app/services/data_deletion/`）

支持 5 种范围删除，记录审计日志：
1. 按来源 URL
2. 按来源平台
3. 按公告来源实例
4. 按页面快照
5. 按用户授权数据

---

## 6. 凭证安全架构（`app/utils/`）

| 凭证类型 | 存储方式 | 文件 |
|---|---|---|
| API Key | HMAC-SHA256 摘要（服务端密钥）+ `secrets.compare_digest` 防时序攻击 | `api_key.py` |
| 用户密码 | Argon2id 哈希（防彩虹表/暴力破解）| `credentials.py` |
| Cookie | AES-GCM 加密（nonce 唯一，`os.urandom`）| `aes_crypto.py` |
| 联系人电话/邮箱 | SHA256 hex 存储 | `tender.py` |

### SSRF 三层防护（`url_safety.py`）

1. 仅允许 HTTP/HTTPS 协议
2. 拦截内网/回环/链路本地/云元数据地址（169.254.169.254 等）
3. 重定向后重新检查目标 IP

### 路径安全

- 白名单存储目录（`data/` 范围内）
- 禁止路径穿越（`..` 检测）
- 文件名与真实存储键分离
- `main.py` 的 `_validate_data_dir` 校验 COOKIE_DIR / ATTACHMENT_DIR / REPORT_OUTPUT_DIR / ANTI_DETECT_SESSION_DIR

---

## 7. 质量评测体系（`app/eval/`）

### 指标

- **evidence recall / precision / IoU**（span 级口径：衡量证据文本边界与金标的重合度）
- **field_precision**（字段精确率）
- **unjustified_rate**（无依据输出率，目标 0%）
- **null_false_positive_rate**（金标 absent/not_applicable 字段零误报，目标 <5%）
- **multi_value_f1_avg**（多值字段 F1）

### 四组消融实验

| 组 | 配置 | 验证内容 |
|---|---|---|
| A | Direct LLM（无验证）| LLM 直接输出的幻觉率 |
| B | LLM + 候选证据 | 证据候选对精确率的影响 |
| C | LLM + 程序验证 | 确定性验证的作用 |
| D | 完整 BidAgent | 选择性输出 + 展示分级 |

### Bootstrap 置信区间

`bootstrap_ci.py`：项目级 Bootstrap 95% 置信区间，1000 次重采样。

### 金标集

- `tests/fixtures/gold/gold_dataset_v4.json`：598 篇全量金标
- document_id 双口径唯一
- 2026-08-06 全量复测：D 组 field_precision 96.44%、unjustified_rate 0.00%

---

## 8. 技术栈

| 层 | 技术 | 版本/说明 |
|---|---|---|
| 后端框架 | FastAPI | Python 3.11+ async |
| ORM | SQLAlchemy 2.0 async + aiosqlite | SQLite (MVP) → PostgreSQL (生产) |
| Agent 框架 | 纯 Python 轻量级实现 | 不依赖 langgraph |
| 抓取引擎 | Playwright (async) + httpx AsyncClient | 浏览器池 + 异步 HTTP |
| LLM | DeepSeek（默认）/ DashScope / ZHIPU / OpenAI | OpenAI 兼容协议可切换 |
| 任务调度 | APScheduler + croniter | cron 表达式验证 |
| 去重算法 | jieba 分词 + 64 位 SimHash | 汉明距离 ≤ 3 判同源 |
| 报告生成 | python-docx | Word 报告 |
| 速率限制 | slowapi | API 级 + 域名级 |
| 凭证安全 | argon2-cffi + cryptography | Argon2id + AES-GCM |
| 部署 | Docker 多阶段构建 + non-root + healthcheck | docker-compose |
| CI | GitHub Actions | pytest 全量 + 覆盖率阈值 40% + pip-audit |

---

## 9. 部署架构

### 单机 MVP

```
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

或一键启动：`python run_demo.py`

### Docker 多角色

```yaml
# docker-compose.yml
services:
  web:
    environment:
      APP_ROLE: web
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000
  worker:
    environment:
      APP_ROLE: worker
    command: python -m app.worker_loop
```

通过 `APP_ROLE` 环境变量区分 web/worker 角色。Worker 使用 `python -m app.worker_loop`（非 RQ 队列）。

---

## 10. 已知限制

1. **2 个官方来源适配器**：MVP 冻结范围 ccgp + ggzy_national，商业平台暂不接入
2. **金标 598 篇**：已超 v4.1 推荐 300～350 篇，未划分开发集/校准集/测试集
3. **temperature 记录口径**：记录 0.0，实际 0.1（不影响指标结论）
4. **不输出信用评分**：6 个观察信号仅供人工尽调参考，不判断围标/授信
5. **历史实验性代码**：BOQ 异常检测与废标风险预警为早期实验，v4.1 MVP 不包含
