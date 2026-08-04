# GOAI 初赛提交清单（2026-08-16 截止）

> 生成时间：2026-07-26
> 最近更新：2026-08-04（v4.1 全面对齐：107 篇真实公告 / 4 组消融 A/B/C/D 全部实现 / null_false_positive_rate=0.0 / 去除 BOQ / 凭证安全升级到 HMAC-SHA256+Argon2id+AES-GCM / 视频降级处理）
> 团队：标小智（徐浚钊、王祯明）
> 赛事：GOAI 世界人工智能开源大赛 · 无界应用赛道 · AI+金融方向

## 一、提交材料状态总览

| # | 提交项 | 必需性 | 当前状态 | 文件路径 | 备注 |
|---|--------|--------|----------|----------|------|
| 1 | 作品简介（500 字） | 必需 | 已完成 v4.1 对齐 | GOAI_初赛提交材料_正式版.md 第一节 | 107 篇 / 4 组消融 / null_false_positive_rate=0.0 / HMAC-SHA256+Argon2id+AES-GCM |
| 2 | 方案 PPT/PDF | 必需 | 已定稿 v4.1 对齐 | proposal.pptx | 28 页，去除 BOQ/信用评分违规页，补 v4.1 新功能 |
| 3 | 合规边界声明 | 建议附 | 已更新 v4.1 对齐（2026-08-04，14916 字节） | compliance.md | 4 大块：数据来源/隐私保护/AI 反幻觉/行业边界 |
| 4 | Demo 视频（90 秒） | 可选加分 | 降级处理 | 标小智_Demo_脚本.md | 视频暂不录制，以代码仓库 + Web Demo 8 页作为等价可验证材料 |
| 5 | 代码仓库链接 | 建议附 | 已公开 | GitHub: tlyyxjz/BidAgent | feature/glm-w4-k3-data 分支，commit caeacde，1316 passed |
| 6 | Web Demo 8 页 | 内部交付 | 已入库 | static/*.html | 工作台/招标检索/公告列表/证据验证/组织画像/质量评测/版本历史/智能问答 |

## 二、v4.1 对齐改造成果

### 已修复的违规与虚标（按 Sol 规矩复查发现）

| 编号 | 项目 | 违规/虚标现象 | 修复后状态 |
|------|------|-----------|-----------|
| F1 | supplier_risk.py 信用评分模块 | v4.1 §9.1 明确不输出信用评分，但代码有 0-100 变相信用评分 | 已删除模块 + 45 个相关测试 |
| F2 | pipeline.py Agent 描述 | 含信用评分描述 | 已删除信用评分描述 |
| F3 | demo_api.py _build_5d_credit | 0-100 变相信用评分 | 已去掉变相信用评分 |
| F4 | scheduler/__init__.py docstring | APScheduler 虚标声明 | 改为如实描述 |
| F5 | README.md | 信用评分功能宣传 | 对齐 v4.1，删除信用评分宣传 |
| F6 | compliance.md | 信用评分基于公开中标数据计算（违规） | 重写，去除信用评分，数据更新到 v4.1 口径 |
| F7 | 作品简介数据 | 22 篇 W2 口径 + BOQ 实验性能力 | 更新到 107 篇 W3 口径，去除 BOQ |
| F8 | D 组消融实验 | 标 D=C（来源谱系未实现） | 已实现，D 组 unjustified_rate=0% |
| F9 | 凭证安全 | 标 SHA256 hash + 后续升级 HMAC | 已实现 HMAC-SHA256+Argon2id+AES-GCM |
| F10 | null_false_positive_rate | v4.1 §10 新指标未实现 | 已实现，5 篇重跑 A/B/C/D 四组均为 0.0 |
| F11 | 域名级频率限制 | 全局频率限制 | 已实现 DomainRateLimiter 域名级独立计数 |
| F12 | robots.txt 检查 | 未实现 | 已实现 RobotsChecker，30 分钟缓存 |
| F13 | 数据删除 | 未实现 | 已实现 DataDeletionService 5 种范围 + 审计日志 |
| F14 | 来源白名单 | 未实现 | 已实现 SourceWhitelist，集成到 scraper 前置检查 |
| F15 | source_lineage 阈值 | <50 字符误判短公告 | 改为 <10 字符，区分无正文与短公告 |

### v4.1 W3 评测报告

文件：_w3_outputs/w3_ablation_smoke_v41_rerun_report.md（7935 字节），包含：

1. 实验元信息（model_id=deepseek-v4-flash，code_commit=caeacde，request_time=2026-08-03T08:18:17Z）
2. 请求参数与 LLM 调用统计（A/B/C/D 四组，D 复用 C）
3. null_false_positive_rate 指标验证（6 个 should_not_have_value 字段，0 误报）
4. 完整指标汇总（A/B/C/D 四组对比）
5. 与上次冒烟结果对比（稳定性验证）
6. 实验复现信息（prompt_hash / total_tokens / latency_ms_avg / code_commit）

## 三、全量回归测试结果

1316 passed, 30 warnings in 643.07s

- 0 errors / 0 failures
- 30 个 warnings = test_new_modules.py 的 @pytest.mark.asyncio 标在同步函数上（预存在，与本次修复无关）
- 测试范围：包含 v4.1 新增的 test_rate_limiter / test_robots_checker / test_data_deletion / test_source_whitelist / test_repost_features / test_credentials / test_v41_api / test_v41_fields / test_demo_pages_smoke / test_real_demo_api 等

## 四、PPT 28 页结构（v4.1 对齐版）

| 页码 | 内容 | v4.1 对齐说明 |
|------|------|------|
| 1 | 封面：标小智 | - |
| 2 | 目录 | - |
| 3-7 | 01 项目背景与定位（数据孤岛/痛点/用户/市场/方案） | 对齐 v4.1 §1 供应链金融贷前尽调定位 |
| 8-12 | 02 六 Agent 协同架构（架构图/各 Agent 详解/金融分析核心） | 对齐 v4.1 §9 六维观察信号，不输出信用评分 |
| 13 | 03 产品体验与 Demo | 更新为 Web Demo 8 页 |
| 14-19 | 04 六大技术亮点 | 去除 BOQ/评分，改为：5 级降级匹配 / 双坐标证据映射 / 来源谱系 / 事实断言键 / 选择性输出 / 凭证安全 |
| 20-23 | 05 测试与质量保障 | 1316 测试 / 4 组消融 / null_false_positive_rate=0.0 |
| 24-25 | 06 安全合规与开放复用 | HMAC-SHA256+Argon2id+AES-GCM / 域名级频率限制 / robots.txt / 数据删除 / 来源白名单 |
| 26-28 | 07 路线图/团队/致谢 | - |

## 五、待办事项（按优先级）

### P0 必须做（8/16 截止前）

1. 已完成：作品简介更新到 v4.1 口径
2. 已完成：compliance.md 更新到 v4.1 口径
3. 已完成：PPT 去除 BOQ/评分违规页
4. 已完成：GitHub 仓库推送最新代码
5. 已完成：4 组消融实验全部实现
6. 已完成：null_false_positive_rate 指标验证
7. 已完成：凭证安全升级（HMAC-SHA256+Argon2id+AES-GCM）
8. 降级处理：Demo 视频录制（以代码仓库 + Web Demo 8 页作为等价可验证材料）

### P1 应该做（复赛阶段）

1. 金标扩展到 v4.1 推荐 300 to 350 篇
2. 划分开发集/校准集/测试集
3. 录制 Demo 视频
4. temperature 记录口径修复
5. React 前端升级

## 六、提交步骤

1. 访问 https://goaihz.com/#register
2. 注册账号（邮箱：135****8907@163.com）
3. 登录后选择赛道：无界应用 to AI+金融
4. 填写项目信息：
   - 项目名称：标小智
   - 团队名称：标小智
5. 上传材料：
   - 作品简介：复制 GOAI_初赛提交材料_正式版.md 第一节
   - 方案 PPT：上传 proposal.pptx
   - 补充材料（可选）：compliance.md
   - Demo 视频（可选）：降级处理，不提交
6. 提交，截止日期：2026-08-16 23:59

## 七、关键文件路径汇总

| 文件 | 路径 | 状态 |
|------|------|------|
| GOAI 提交材料正文 | GOAI_初赛提交材料_正式版.md | v4.1 对齐 |
| 提案 PPT | proposal.pptx | v4.1 对齐 |
| 合规声明 | compliance.md | v4.1 对齐 |
| W3 评测报告 | _w3_outputs/w3_ablation_smoke_v41_rerun_report.md | v4.1 指标验证 |
| 99 篇全量消融 | _w3_outputs/w3_ablation_full_99.json | 4 组 A/B/C/D |
| Bootstrap CI | _w3_outputs/w3_bootstrap_ci_full_99.json | 99 篇 |
| Demo 脚本 | 标小智_Demo_脚本.md | 待录制（降级） |
| 项目代码 | GitHub 仓库 tlyyxjz/BidAgent | 1316 测试通过 |

## 八、Git 状态

### 当前分支结构
- feature/glm-w4-k3-data（W4 主线，已 push 到 GitHub）
  - commit caeacde: v4.1 P2-7/P2-8 + 累积改进: source_lineage 阈值修正 + 空值误报率指标 + 采集组件/白名单/数据删除/观察信号/转载特征
  - commit 69faaaa: v4.1 sec 5.3 scraper integration + unit tests
  - commit 0976bee: v4.1 sec 5.3+13 compliance layer + credential security
  - commit 7f354cf: P0-1b: demo_api.py _build_5d_credit 去掉0-100变相信用评分
  - commit 12f9c03: P0-7: docker-compose.yml 服务名/DB名/品牌名对齐
  - commit 97f7866: P0-6: demo_api.py 删除围标/陪标/地方保护定性描述
  - commit 0f4cf1a: P0-5: pipeline.py Agent 描述删除信用评分
  - commit 1590896: P0-4: 修正 scheduler/__init__.py APScheduler 虚标 docstring
  - commit d1db04b: P0-3: compliance.md 删除虚标声明，改为如实描述
  - commit be6ee79: P0-2: README.md 对齐 v4.1，删除信用评分功能宣传
  - commit 7fdb74c: P0-1: 删除违规的 supplier_risk.py 信用评分模块
- 均未推 main/develop：符合硬约束
- GitHub 仓库：https://github.com/tlyyxjz/BidAgent（公开）

## 九、v4.1 关键要求对照

### 9.1 项目定位（v4.1 第 1.1 to 1.4 节）

- 一句话定位：面向供应链金融贷前尽调与企业采购核验的可验证招投标数据引擎——将不可核验的 LLM 输出转化为可复核、可追踪的数据资产
- 核心用户：供应链金融贷前尽调人员
- 系统定位：位于金融风控的数据准备与事实核验环节，为后续人工尽调或其他风控系统提供带证据的招投标数据，而非直接替代风控决策
- 8 条核心差异化（第 1.4 节）：见作品简介第一节

### 9.2 MVP 边界（v4.1 第 2.1 to 2.3 节）

MVP 必做（第 2.1 节）：
- 两个已冻结页面体系的官方来源适配器（ccgp + ggzy_national）
- 招标公告、中标公告和更正公告
- 六类核心结构化字段（项目编号、采购人名称、中标人名称、金额及金额类型、发布日期、投标截止日期）
- 字段级多证据验证
- 页面快照与版本管理
- 同源转载识别
- 三维质量评估（抽取支持度 / 来源质量 / 交叉验证状态）
- 独立金标评测（含消融实验）
- Web Demo
- REST API
- 基础组织实体公开活动画像

MVP 暂不实施（第 2.3 节）：
- 企业信用评分
- 授信建议
- 中标概率预测
- 围标自动判定
- 全品类 BOQ 异常检测
- 分布式采集
- 图数据库
- 多租户系统
- 商业平台账号池及验证码自动处理
- PDF/OCR 深度解析

### 9.3 四层实体数据模型（v4.1 第 4 章）

TenderProject（采购项目）to TenderNotice（业务公告）to NoticeSource（来源页面）to NoticeVersion（抓取版本）to ExtractedField（抽取字段）to FieldEvidenceLink to Evidence（字段证据）

辅助实体：Organization（组织机构）、NoticeParticipant（公告参与关系）、ProjectIdentifier（项目标识）、FactAssertionKey（事实断言键）。所有核心实体使用无业务含义的内部稳定主键（ULID）。

### 9.4 6 个 MVP 观察信号（v4.1 第 9.2 节）

| 信号 | 说明 |
|---|---|
| 中标活跃度 | 近 90 天公开中标次数和金额趋势，不作正负定性 |
| 公开中标集中度 | 当前覆盖数据中 Top 3 采购人及地区占比 |
| 废标公告关联 | 企业在废标或流标公告中被观察到的次数，不直接归因 |
| 明确投标否决 | 公告明确写明企业投标被否决，并记录原因 |
| 信息冲突观察 | 相同事实断言在不同有效来源中出现矛盾 |
| 高频共现提示（选做） | 企业与其他企业在同一标段被反复观察到，不用于判断围标 |

严谨表述（第 9.3 节）：前端使用「公开公告中观察到的投标出现次数」，不得使用「企业实际投标次数」；集中度使用「当前覆盖公开中标记录中的采购人集中度」，不得使用「企业客户集中度或营业收入集中度」。

### 9.5 12 个标准 API 端点（v4.1 第 12 章）

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

### 9.6 凭证安全（v4.1 第 13.1 节）

- API Key 使用高熵随机值，服务端只保存基于服务端密钥的 HMAC-SHA256 摘要
- 密码使用 Argon2id
- Cookie 使用 AES-GCM
- 加密密钥通过环境密钥或密钥管理服务提供，nonce 必须唯一
- 日志不得记录凭证
- SSRF 防护：仅允许 HTTP/HTTPS，拦截内网/回环/链路本地/云元数据地址，重定向后重新检查
- 路径安全：白名单存储目录，禁止路径穿越，文件名与真实存储键分离

### 9.7 合规采集（v4.1 第 5 章）

- 域名级频率限制：默认 8 秒间隔，按域名独立计数（DomainRateLimiter）
- robots.txt 合规检查：30 分钟域名级缓存，不可达时默认允许（RobotsChecker）
- 来源白名单：维护允许采集的来源平台/域名清单，支持运行时下架/重新启用（SourceWhitelist）
- 数据删除：支持按来源 URL、来源平台、公告来源实例、页面快照、用户授权数据 5 种范围删除，记录审计日志（DataDeletionService）
- 失败回退：触发 403/封禁时停止访问，不进行规避；失败时回滚频率限制 reservation

### 9.8 高频问题对照（v4.1 第 18.3 节）

和招投标搜索平台有什么不同？
招投标搜索平台主要解决信息覆盖和检索问题；标小智主要解决结构化字段是否有原文依据、来源是否明确以及数据是否可追踪的问题。

为什么不作信用评分？
公开招投标数据不足以代表企业整体信用。标小智只输出有明确数据口径的公开活动观察信号，不构成授信或投资依据。

证据会不会也是模型编的？
不会直接采信。LLM 只提供候选证据文本，最终证据必须由程序在指定公告版本快照中搜索并验证，找不到就标记为无证据。

为什么很多公告没有独立交叉验证？
同一业务公告通常只有一个原始发布主体，其他页面可能只是转载。单一官方原始来源加直接原文证据可以构成高可信，独立来源验证属于额外增强项。

不输出供应商信用评分（第 9.1 节）：标小智不输出供应商信用评分。所有信号只反映系统覆盖来源和时间范围内观察到的公开招投标活动，供人工尽调参考。

### 6 信号一览（v4.1 第 9.2 节）

6 个 MVP 观察信号一览：中标活跃度、公开中标集中度、废标公告关联、明确投标否决、信息冲突观察、高频共现提示（选做）。所有信号均不直接归因，仅供人工尽调参考。
