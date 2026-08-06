# 标小智 — 可验证招投标数据引擎

> 面向供应链金融贷前尽调的可验证招投标数据引擎 · GOAI 2026。将不可核验的 LLM 输出转化为可复核、可追踪的数据资产——LLM 只生成候选，确定性程序负责验证。

**当前状态**：v4.1 对齐版（GOAI 世界人工智能开源大赛 · 无界应用赛道 · AI+金融方向）
**测试**：1942 passed · 0 warnings · **评测数据**：162 篇真实公告 · 金标 598 篇 · **分支**：feature/w5-ui-redesign

---

## 核心定位（v4.1 §1）

- **一句话定位**：面向供应链金融贷前尽调与企业采购核验的可验证招投标数据引擎——将不可核验的 LLM 输出转化为可复核、可追踪的数据资产
- **核心用户**：供应链金融贷前尽调人员
- **系统定位**：位于金融风控的数据准备与事实核验环节，为后续人工尽调或其他风控系统提供带证据的招投标数据，而非直接替代风控决策
- **核心差异化**：LLM 只生成字段和证据候选，确定性程序负责在原文快照中搜索验证，找不到依据的字段一律标记为无依据不输出

### 8 条核心差异化（v4.1 §1.4）

1. 每个字段可绑定多段原文证据，区分主证据、限定条件和推导输入
2. LLM 只生成字段和证据候选，确定性程序负责验证
3. 区分项目、公告、来源页面和页面版本四层实体
4. 区分官方原始发布、官方转载、商业转载和索引页面
5. 识别同源转载，避免将转载数量误判为独立交叉验证
6. 将抽取支持度、来源质量和交叉验证状态独立保存
7. 使用独立金标测试集验证准确率、覆盖率和无依据输出率
8. 只输出可解释的公开招投标活动观察信号，不输出信用评分

---

## 核心能力

### 四层实体数据模型（v4.1 §4）

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

辅助实体：Organization（组织机构）、NoticeParticipant（参与关系）、ProjectIdentifier（项目标识）、FactAssertionKey（事实断言键）。所有核心实体使用无业务含义的内部稳定主键（ULID）。

### 六 Agent 协同架构

意图解析 → 采集执行 → 数据加工 → 质量保障 → 金融分析 → 报告交付

### 可验证抽取引擎（核心差异化）

- LLM 只生成字段和证据候选，确定性程序负责验证
- 5 级降级匹配：L1 精确 → L2 空白归一化 → L3 全半角统一 → L4 金额日期格式变体 → L5 模糊匹配/失败标记
- 双坐标映射（normalized_index ↔ raw_index），证据偏移量在快照中可稳定复现，前端不依赖实时网页 DOM
- 找不到依据的字段一律标记为无依据不输出

### 来源谱系与版本追踪

- 来源角色判定：official_original / official_repost / commercial_repost / unknown
- 同源转载识别（SimHash 汉明距离 ≤ 3），避免将转载数量误判为独立交叉验证
- 事实断言键（FactAssertionKey）：跨源比较前确保双方表达同一业务事实
- 页面版本追踪，历史版本不被新版本覆盖

### 展示等级与选择性输出

- 三级分类（high / review / low）：基于抽取支持度 + 来源质量 + 交叉验证状态
- 四种输出策略：strict / default / loose / audit

### 6 个 MVP 观察信号（v4.1 §9.2，严格不输出信用评分）

| 信号 | 说明 |
|---|---|
| 中标活跃度 | 近 90 天公开中标次数和金额趋势，不作正负定性 |
| 公开中标集中度 | 当前覆盖数据中 Top 3 采购人及地区占比 |
| 废标公告关联 | 企业在废标或流标公告中被观察到的次数，不直接归因 |
| 明确投标否决 | 公告明确写明企业投标被否决，并记录原因 |
| 信息冲突观察 | 相同事实断言在不同有效来源中出现矛盾 |
| 高频共现提示（选做） | 企业与其他企业在同一标段被反复观察到，不用于判断围标 |

> 严谨表述（v4.1 §9.3）：使用「公开公告中观察到的投标出现次数」，不得使用「企业实际投标次数」；高频共现必须附带说明「仅凭共现不能判断企业关联关系或围标行为」。

### 合规采集（v4.1 §5）

- **域名级频率限制**（DomainRateLimiter）：默认 8 秒间隔，按域名独立计数，失败时回滚 reservation
- **robots.txt 合规检查**（RobotsChecker）：30 分钟域名级缓存，不可达时默认允许
- **来源白名单**（SourceWhitelist）：维护允许采集的来源平台/域名清单，支持运行时下架/重新启用，集成到 scraper 前置检查
- **数据删除**（DataDeletionService）：支持按来源 URL、来源平台、公告来源实例、页面快照、用户授权数据 5 种范围删除，记录审计日志
- 失败回退：触发 403/封禁时停止访问，不进行规避

### 凭证安全（v4.1 §13.1）

- API Key 使用高熵随机值，服务端只保存基于服务端密钥的 **HMAC-SHA256 摘要**（secrets.compare_digest 防时序攻击）
- 密码使用 **Argon2id** 哈希（防彩虹表/暴力破解）
- Cookie 使用 **AES-GCM** 加密（nonce 唯一）
- SSRF 防护：仅允许 HTTP/HTTPS，拦截内网/回环/链路本地/云元数据地址，重定向后重新检查
- 路径安全：白名单存储目录，禁止路径穿越，文件名与真实存储键分离
- 日志不得记录凭证

### 质量评测（v4.1 §10）

- 证据 recall / precision / IoU 三大指标（span 级口径：衡量证据文本边界与金标的重合度）
- 项目级 Bootstrap 95% 置信区间
- 四组消融实验（A/B/C/D），其中 evidence_precision 为字段级口径（输出字段的证据可在原文定位），与 span 级指标不同维度，不可直接比较
- v4.1 §10 新指标：null_false_positive_rate（金标 absent/not_applicable 字段零误报）

### 12 个标准 API 端点（v4.1 §12）

| 接口 | 方法 | 说明 |
|---|---|---|
| /api/projects/search | GET | 搜索采购项目 |
| /api/projects/{project_id} | GET | 获取项目及公告生命周期 |
| /api/notices/{notice_id} | GET | 获取公告详情 |
| /api/notices/{notice_id}/sources | GET | 获取来源页面和谱系 |
| /api/notices/{notice_id}/participants | GET | 获取公告参与方列表 |
| /api/sources/{source_id}/versions | GET | 获取页面版本历史 |
| /api/fields/{field_id} | GET | 获取字段和全部证据 |
| /api/organizations/search | GET | 搜索组织实体 |
| /api/organizations/{org_id} | GET | 获取组织实体公开活动画像 |
| /api/extract/tasks | POST | 提交异步抽取任务 |
| /api/extract/tasks/{task_id} | GET | 查询任务状态 |
| /api/stats/quality | GET | 获取数据质量和评测统计 |

抽取任务异步状态：queued / running / partially_succeeded / succeeded / failed

### Web Demo（8 页）

工作台 / 招标检索 / 公告列表 / 证据验证详情 / 组织画像 / 质量评测 / 版本历史 / 智能问答

### 推送与去重

- SMTP 邮件 + Webhook HMAC 签名，at-least-once 语义 + content_hash 幂等去重
- Word 报告自动生成 + cron 定时推送

---

## 评测数据（v4.1 W3 真实数据）

### 数据集

| 项目 | 数值 |
|---|---|
| 数据库公告总数 | 162 篇（2026-08 恢复灌库，SimHash 去重）|
| 金标集 | 598 篇（`tests/fixtures/gold/gold_dataset_v4.json`，document_id 双口径唯一）|
| W3 评测集 | 100 篇（ccgp_w3）|
| 实时采集 | 7 篇（ccgp）|
| 公告类型覆盖 | tender 34 / award 35 / correction 33 / 其他 5 |
| 金标字段总数（99 篇全量）| 594 |

### 4 组消融实验（99 篇全量，final5 实测，详见 `_w3_outputs/端到端评测报告.md`）

| 指标 | A 组（Direct LLM）| B 组（LLM+候选证据）| C 组（LLM+程序验证）| D 组（完整 BidAgent）|
|---|---|---|---|---|
| unjustified_rate | **100.00%** | 0.00%（失真）| **3.01%** | **0.00%** |
| field_precision | 96.17% | 87.76% | 87.59% | **98.08%** |
| evidence_precision | N/A | N/A | 100.00% | 100.00% |

### v4.1 §10 新指标 null_false_positive_rate（99 篇全量，final5 实测）

| 指标 | A 组 | B/C/D 组 | 目标 |
|---|---|---|---|
| null_false_positive_rate | 4.37% | **0.63%** | <5%（达标）|

仅剩 1 个空值误报（w3_correction_043），经核对为金标标注矛盾。

### 金标 v4 合集 598 篇全量复测（2026-08-06 实测，`scripts/eval_gold598_retest.py`）

| 指标 | A 组（Direct LLM）| B 组（LLM+候选证据）| C 组（LLM+程序验证）| D 组（完整 BidAgent）|
|---|---|---|---|---|
| field_precision | 87.21% | 95.46% | 95.32% | **96.44%** |
| unjustified_rate | **100.00%** | 0.00% | 3.89% | **0.00%** |
| evidence_precision | N/A | N/A | 100.00% | **100.00%** |
| null_false_positive_rate | 24.48% | 2.23% | 2.15% | **2.15%** |
| multi_value_f1_avg | 0.7729 | 0.8466 | 0.8505 | **0.8505** |

口径说明：598 篇全覆盖（fields_total=3588），与 99 篇消融同一套 run_group/summarize 口径；D 组选择性输出拒绝低置信字段后 fields_evaluable=2133（不确定的不输出）。按来源分组 D 组精确率：w3 98.13% / w4 97.50% / w5 96.03% / frozen93 80.22%（早期冻结标注 22 篇，值匹配口径更严）。产物：`_w3_outputs/gold598_retest.json`，断点 checkpoint 去重后 598 唯一（双进程并发写入已用 `scripts/finalize_gold598_retest.py` 归一）。

### 测试

- 1942 passed · 0 errors / 0 failures（含 5 个 Playwright 页面级 E2E：`tests/test_e2e_pages.py`，真实 uvicorn + chromium，覆盖工作台/列表/详情/看板/搜索渲染主路径与零 JS 异常）
- 0 warnings（已清理 asyncio mark 误标与 datetime.utcnow() 弃用告警）
- 测试覆盖率 90.63%（达到 pyproject.toml 阈值 90%，1942 用例全量实测）

---

## MVP 边界（v4.1 §2）

### MVP 必做（已实现）

- 两个已冻结页面体系的官方来源适配器（ccgp + ggzy_national）
- 招标公告、中标公告和更正公告
- 六类核心结构化字段（项目编号、采购人名称、中标人名称、金额及金额类型、发布日期、投标截止日期）
- 字段级多证据验证
- 页面快照与版本管理
- 同源转载识别
- 三维质量评估（抽取支持度 / 来源质量 / 交叉验证状态）
- 独立金标评测（含消融实验）
- Web Demo + REST API
- 基础组织实体公开活动画像

### MVP 暂不实施

- 企业信用评分 / 授信建议 / 中标概率预测 / 围标自动判定
- 全品类 BOQ 异常检测
- 分布式采集 / 图数据库 / 多租户系统
- 商业平台账号池及验证码自动处理
- PDF/OCR 深度解析

> 历史代码中存在的 BOQ 异常检测与废标风险预警模块为早期实验性实现，v4.1 MVP 不包含这些能力，不作为对外功能宣传。

---

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

### 多模型支持（LLM provider 可切换）

抽取与意图解析均走 OpenAI 兼容协议，通过 `.env` 切换供应商：

```bash
# 供应商：deepseek（默认）/ dashscope / zhipu / openai
LLM_PROVIDER=deepseek

# 各供应商各自的 key（只填所选 provider 的即可）
DEEPSEEK_API_KEY=sk-xxx
# DASHSCOPE_API_KEY=sk-xxx      # 通义千问（阿里云百炼）
# ZHIPU_API_KEY=xxx             # 智谱 GLM
# OPENAI_API_KEY=sk-xxx

# 可选覆盖
# LLM_EXTRACTION_MODEL=deepseek-reasoner  # 抽取任务单独指定模型
# LLM_BASE_URL / LLM_API_KEY              # 指向任意 OpenAI 兼容端点（如自建代理）
# LLM_JSON_MODE=true/false                # 强制开关 json_object response_format
```

说明：
- 未支持 `response_format=json_object` 的模型（如部分 GLM 版本）会自动关闭该参数，
  解析层用宽松 JSON 解析器兜底（去围栏 / 截取花括号 / 尾逗号修复），解析失败还会
  追加纠正指令自动重试一次，抗偶发 JSON 破损。
- 切换模型后 `prompt_hash` 不变（prompt 内容未变），`model_id` 记录实际使用的模型，
  评测报告可区分口径。

### 启动服务

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- API 文档 (Swagger UI): http://localhost:8000/docs
- 健康检查: http://localhost:8000/health

- Web Demo: http://localhost:8000/ui

### 架构验证指南

供评委 / 审查者核查核心声明的 grep 命令：

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

# 7. ULID 主键 + 外键索引
grep -n "ulid\|ULID\|index=True\|ForeignKey" app/models/organization.py

# 8. 运行凭证安全测试（45 用例，分支 100%）
pytest tests/test_credentials.py -v
```

### 依赖安全

```bash
# 依赖漏洞扫描（已验证：No known vulnerabilities found）
pip-audit --requirement requirements.txt
```


### Docker 启动

```bash
docker-compose up -d
```

详见 [DEPLOY.md](DEPLOY.md)。

## 测试

```bash
pytest -v
```

```bash
# 覆盖率
pytest --cov=app --cov-report=term-missing
```

测试范围包含 v4.1 新增：test_rate_limiter / test_robots_checker / test_data_deletion / test_source_whitelist / test_repost_features / test_credentials / test_v41_api / test_v41_fields / test_demo_pages_smoke / test_real_demo_api 等。

---

## 数据与合规边界

### 数据来源

| 数据源 | 类型 | 说明 |
|---|---|---|
| ccgp.gov.cn | 官方公开 | 中国政府采购网（MVP 适配器）|
| ggzy.gov.cn | 官方公开 | 全国公共资源交易平台（MVP 适配器）|

采集行为：域名级 8 秒频率限制 + robots.txt 合规检查 + 来源白名单 + 403 不重试。不绕过登录墙、不抓取付费内容。

### 隐私保护

- 不采集个人隐私数据（身份证/手机号/家庭住址等）
- 联系人电话/邮箱使用 SHA256 hex 存储
- API Key 用 HMAC-SHA256 摘要，密码用 Argon2id，Cookie 用 AES-GCM
- 不输出供应商信用评分（v4.1 §9.1），所有信号仅供人工尽调参考

### 风险提示

- 报告输出明确标注「AI 生成，仅供参考，决策请人工复核」
- 定位为数据服务商，不提供金融建议，不承担金融决策责任
- 不输出信用评分，不判断围标，不提供授信建议

详见 [compliance.md](_w2_report/compliance.md)。

---

## 文档目录索引

| 文档 | 位置 | 说明 |
|---|---|---|
| v4.1 总规划 | `docs/BidAgent_项目总体规划_v4.1_执行定稿版.md` | v4.1 执行定稿 |
| GOAI 提交材料 | `GOAI_初赛提交材料_正式版.md` | 初赛作品简介 + 技术指标 |
| 合规声明 | `_w2_report/compliance.md` | 数据来源 / 隐私保护 / AI 反幻觉 / 行业边界 |
| 部署文档 | `DEPLOY.md` | Docker 部署说明 |
| W3 评测报告 | `_w3_outputs/w3_ablation_smoke_v41_rerun_report.md` | v4.1 指标验证报告 |
| 99 篇全量消融 | `_w3_outputs/w3_ablation_full_99.json` | 4 组 A/B/C/D |
| Bootstrap CI | `_w3_outputs/w3_bootstrap_ci_full_99.json` | 99 篇置信区间 |
| 金标冻结 | `tests/fixtures/gold/gold_frozen_v1.json` | 金标标注冻结 |
| 金标合集（598 篇）| `tests/fixtures/gold/gold_dataset_v4.json` | w4/w5 补标合并，document_id 唯一 |
| 验证规则清单 | `docs/验证规则清单_v1.0.md` | 验证引擎 34 条规则显性化（G/A/T/D/I/E/M 七族，含变更流程与测试映射）|

---

## 当前已知限制

1. **金标数量 598 篇**（2026-08 补标收官，合集 `tests/fixtures/gold/gold_dataset_v4.json`），已超 v4.1 推荐 300～350 篇；全量 598 篇复测已完成（2026-08-06）：D 组 field_precision 96.44%、unjustified_rate 0.00%、evidence_precision 100%、null_false_positive_rate 2.15%（详见上文复测小节）
2. **未划分开发集/校准集/测试集**：当前为统一金标集
3. **temperature 记录口径**：记录 0.0，实际 0.1，不影响指标结论，后续修复
4. **Demo 视频降级处理**：初赛阶段以代码仓库 + Web Demo 8 页作为等价可验证材料，视频待复赛补录
5. **2 个官方来源适配器**：MVP 冻结范围 ccgp + ggzy_national，商业平台暂不接入

---

## 技术栈

| 层 | 技术 |
|---|---|
| 后端框架 | Python 3.11+ / FastAPI |
| Agent 框架 | 纯 Python 轻量级实现（不依赖 langgraph）|
| 抓取引擎 | Playwright (async API) + httpx AsyncClient |
| LLM | DeepSeek |
| 任务调度 | APScheduler + croniter |
| 去重算法 | jieba 分词 + 64 位 SimHash |
| 数据库 | SQLite (MVP) → PostgreSQL (生产) |
| ORM | SQLAlchemy 2.0 async + aiosqlite |
| 部署 | Docker 多阶段构建 + non-root 用户 + healthcheck |

## 工程规范

- 所有中间件用 async/await，不用 callback 风格
- API Key 用 HMAC-SHA256 摘要 + Argon2id 密码哈希 + AES-GCM Cookie 加密
- 环境变量密钥用 `secrets.token_hex(32)` 生成 64 字符 hex
- SSRF 三层防护 / LIKE 注入防护 / 邮件头注入防护 / 路径穿越防护
- 异步函数用 `run_in_executor` 卸载同步 CPU/IO 任务
- 结构化日志带 request_id 上下文，不记录凭证
- 统一错误响应 `{code, data, msg}`
- Docker 多阶段构建 + non-root 用户 + healthcheck
- GitHub Actions CI：pytest 全量 + 覆盖率 90% 阈值 + pip-audit 依赖漏洞扫描（.github/workflows/ci.yml）

## 许可证

Apache License 2.0

## 团队

- 团队：标小智（徐浚钊、王祯明）
- 所属：上海建桥大学 计算机科学与技术专业
- 赛事：GOAI 世界人工智能开源大赛 · 无界应用赛道 · AI+金融方向
- 仓库：https://github.com/tlyyxjz/BidAgent