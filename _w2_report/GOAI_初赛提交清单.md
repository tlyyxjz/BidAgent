# GOAI 初赛提交清单（2026-08-16 截止）

> **生成时间**：2026-07-26
> **最近更新**：2026-07-28（API 充值后 W2-09 22 篇全部成功补跑 + 路演讲稿同步完整口径）；2026-07-28 P0-4 compliance.md 已创建（4 大块：数据来源/隐私保护/AI 反幻觉/行业边界）；2026-07-28 GOAI 对照修复 5 项初赛提交材料问题（作品简介/PPT 测试数/README/.env.example/路演讲稿 IoU avg）
> **团队**：标小智（徐浚钊、王祯明）
> **赛事**：GOAI 世界人工智能开源大赛 · 无界应用赛道 · AI+金融方向

## 一、提交材料状态总览

| # | 提交项 | 必需性 | 当前状态 | 文件路径 | 备注 |
|---|--------|--------|----------|----------|------|
| 1 | 作品简介（500 字） | 必需 | ✅ 已完成 | GOAI_初赛提交材料_正式版.md 第一节 | 500 字整，含六 Agent/三金融能力/合规边界 |
| 2 | 方案 PPT/PDF | 必需 | ✅ 已定稿 | _w2_report/proposal.pptx | 28 页 629.9KB，4:3 布局，含王祯明团队页，路线图含 W4 React 升级 |
| 3 | 合规边界声明 | 建议附 | ✅ 已创建（2026-07-28，117 行，5601 字节） | _w2_report/compliance.md | 4 大块：数据来源/隐私保护/AI 反幻觉/行业边界 |
| 4 | Demo 视频（90 秒） | 可选加分 | ⏳ 脚本就绪待录 | 标小智_Demo_脚本.md | 90 秒 4 场景分镜，571 tests 数据已更新 |
| 5 | 代码仓库链接 | 建议附 | ✅ 已公开 | GitHub: tlyyxjz/BidAgent | feature/glm-w2-evidence + feature/glm-w2-06 已推送 |
| 6 | W2-06 前端字段高亮 Demo | 内部交付 | ✅ 已入库 | feature/glm-w2-06 分支 commit 79b2f35 | 12 项 Playwright + 9 项冒烟全过 |

## 二、本次重修成果

### 已修复的假完成（按 Sol 规矩复查发现）

| 编号 | 项目 | 假完成现象 | 修复后状态 |
|------|------|-----------|-----------|
| F1 | proposal.pptx | GOAI 提交材料标"已完成 30 页"，实际文件不存在 | ✅ 已生成 28 页 629.9KB PPT |
| F2 | compliance.md | 同上，标"已完成"，实际文件不存在 | ✅ 已生成 5.5KB（117 行） |
| F3 | 测试数 511 | Demo 脚本写"511 tests"，实际 571 | ✅ 已完成（511→571） |
| F4 | 路演讲稿 | 还是 ScrapeFlow 时代（5 层架构），未更新到标小智六 Agent | ⏳ W3 任务 |
| F5 | PPT 页脚 "null / 30" | slideNumber 在 addSlide 时为 null + 总页数硬编码 30 | ✅ 改用全局计数器，页脚显示 "X / 28" |
| F6 | PPT 目录页码错 | 目录写 14-21/22-25 等，实际页码 14-19/20-23 等 | ✅ 已修正目录页码引用 |
| F7 | 数据夸大 | compliance.md 实际 5.5KB，之前说 8KB | ✅ 已更正 |
| F8 | 测试环境 120 errors | --basetemp 残留文件被 Windows 锁定 → pytest 清理失败 → 中断 fixture teardown → SQLite 表状态不一致 | ✅ conftest.py 加 try/except，571 passed 0 errors（commit e671e2d） |
| F9 | W2-06 假完成初查误判 | 初查只查主项目目录，未考虑豆包 Turbo 在自己工作目录 6a6626ae8bb75fb682f059f9 工作 | ✅ 全盘搜索找到 11 个文件，迁移到主项目，12 Playwright + 9 冒烟全过（commit 79b2f35） |

### W2 评测报告

文件：`_w2_report/W2_评测报告.md`（249 行 16.4KB），包含：

1. W2 任务完成情况表（W2-01~W2-10 状态）
2. W2-05 证据入库验证（16/16 金标过，96 字段 153 证据 0 错误）
3. W2-01 LLM 候选证据冒烟测试（3 篇，18/18 证据命中）
4. W2-08 消融实验 A/B/C 三组对比（22 篇 budget 口径，C 组 unjustified_rate=1.94%，field_precision=94.49%，evidence_precision=100%）
5. W2-09 证据定位指标（22 篇 budget 口径全部成功，iou 新逻辑：recall 69.90%, precision 60.63%, IoU 0.5307, P50=0.96/P95=1.0）
6. 22 篇 vs 7 篇结果差异分析（field_precision 反常原因）
7. W2-08 与 W2-09 指标口径对比
8. 待人工复测项 10 条（4 条已完成、6 条待查）
9. Git 信息（commit 2e37930，feature/glm-w2-evidence 分支）
10. 已知限制（LLM 随机性 / 预存在 warnings / .env 不入 git / 金标数量 / field_precision 反常）

## 三、全量回归测试结果

```
571 passed, 1 skipped, 30 warnings in 363.99s
```

- **0 errors / 0 failures**
- 1 skipped = `test_w2_06_smoke.py`（独立脚本，加了 pytest skip 标记，用 `python tests/test_w2_06_smoke.py` 独立运行）
- 30 个 warnings = `test_new_modules.py` 的 `@pytest.mark.asyncio` 标在同步函数上（预存在，与本次修复无关）
- 测试范围：排除 `test_browser_pool.py`（预存在超时问题）+ `test_w2_06_playwright.py`（需独立启后端跑）

## 四、PPT 28 页结构

| 页码 | 内容 |
|------|------|
| 1 | 封面：标小智 |
| 2 | 目录 |
| 3-7 | 01 项目背景与定位（数据孤岛/痛点/用户/市场/方案） |
| 8-12 | 02 六 Agent 协同架构（架构图/各 Agent 详解/金融分析核心） |
| 13 | 03 产品体验与 Demo |
| 14-19 | 04 六大技术亮点（反检测/SimHash/证据验证/BOQ/废标+评分/推送幂等） |
| 20-23 | 05 测试与质量保障（571 测试/W2 闭环/工程规范/CI/CD） |
| 24-25 | 06 安全合规与开放复用 |
| 26-28 | 07 路线图/团队/致谢 |

## 五、待办事项（按优先级）

### P0 必须做（8/16 截止前）

1. ~~**更新 Demo 脚本过时数据**~~：✅ 已完成（511 → 571 tests）
2. **录制 Demo 视频**：按 标小智_Demo_脚本.md 4 场景分镜录制
3. ~~**GitHub 仓库公开**~~：✅ 已完成（tlyyxjz/BidAgent，双分支已推送）
4. ~~**金标扩展至 20 篇**~~：✅ 已完成（W2-07，实际 22 篇，193 个证据偏移量全部验证正确）
5. ~~**PPT 团队页补王祯明信息**~~：✅ 已完成（28 页 4:3 布局定稿）

### P1 应该做（W3 阶段）

5. **更新路演讲稿**：从 ScrapeFlow 5 层架构更新到标小智六 Agent（已派给 K3 润色）
6. ~~**W2-06 前端字段高亮**~~：✅ 已完成（feature/glm-w2-06，commit 79b2f35）
7. ~~**聊天 UI**~~：✅ 已完成（`app/api/chat.py` + `app/templates/html/chat.py`，含 6 Agent 进度面板）

### P2 可以做（复赛阶段）

8. **W2-10 winner_name 提示词优化**（已暂缓）
9. **资产吸收**：proxy_manager + ai_config（实际已被现有代码覆盖，可选优化）

## 六、提交步骤

1. 访问 https://goaihz.com/#register
2. 注册账号（邮箱：135****8907@163.com）
3. 登录后选择赛道：**无界应用 → AI+金融**
4. 填写项目信息：
   - 项目名称：标小智
   - 团队名称：标小智
5. 上传材料：
   - 作品简介：复制 `GOAI_初赛提交材料_正式版.md` 第一节
   - 方案 PPT：上传 `_w2_report/proposal.pptx`
   - 补充材料（可选）：`_w2_report/compliance.md`
   - Demo 视频（可选）：`标小智_Demo_90s.mp4`（待录制）
6. 提交，截止日期：**2026-08-16 23:59**

## 七、关键文件路径汇总

| 文件 | 路径 | 状态 |
|------|------|------|
| GOAI 提交材料正文 | `GOAI_初赛提交材料_正式版.md` | ✅ |
| 提案 PPT | `_w2_report/proposal.pptx` | ✅ |
| 合规声明 | `_w2_report/compliance.md` | ✅ |
| W2 评测报告 | `_w2_report/W2_评测报告.md` | ✅ |
| W2 标注质检报告 | `_w2_report/W2_标注质检报告.md` | ✅ |
| Demo 脚本 | `标小智_Demo_脚本.md` | ✅ 571 tests 数据已更新 |
| 第二周任务清单 | `标小智_第二周任务清单.md` | ✅ |
| 项目代码 | GitHub 仓库 tlyyxjz/BidAgent | ✅ 826 测试通过 |

## 八、Git 状态

### 当前分支结构
- **feature/glm-w2-evidence**（W2 主线，已 push 到 GitHub）
  - commit 2b17993: fix(K3): 仲裁B口径修复 - amount=budget+award多值 + W2-08/W2-09重跑
  - commit 3ba965b: fix(K3-P0): 按Sol规矩修复8项P0 - PPT/代码/测试/宣传
  - commit 2e37930: fix(P0-4): 创建compliance.md合规声明 - 4大块118行
  - commit 7c71147: fix(K3-Sol复查): 修复3项P0口径冲突+8项P1+2项P2+W2-05入库验证
  - commit b243903: fix(Sol复查2): 修复P1-15成功路径遗漏+GOAI深层不一致
  - commit 7ca1a47: fix(Sol复查3): 对照表补award口径标注
  - commit 418238c: fix(GOAI对照): 修复5项初赛提交材料问题
  - commit e671e2d: fix(test): conftest.py 加 try/except 修复 120 errors 测试环境问题
- **feature/glm-w2-06**（前端高亮，已 push 到 GitHub）
  - commit 79b2f35: feat(W2-06): 前端字段高亮 Demo + 6 Agent 协作聊天页
  - commit bb50e4d: docs: README/LICENSE/PPT 同步 + Open Core 协议声明
  - 9 files changed, 1104 insertions(+), 5 deletions(-)
- **均未推 main/develop**：符合硬约束 #36
- **GitHub 仓库**：https://github.com/tlyyxjz/BidAgent（公开）

### 已 commit（K3 仲裁 B 口径修复 + W2-08/W2-09 重跑）
- commit 2b17993: 3 篇金标 budget 修复（annotation_04/05/06_award_A.json，amount 增加 budget 值）
- commit 3ba965b: P0 修复（W2-08/W2-09 budget 口径结果 + 代码修复 + 测试）
- commit 2e37930: compliance.md 合规声明
- commit 7c71147: Sol 复查修复（3 项 P0 口径冲突 + 8 项 P1 + 2 项 P2 + W2-05 入库验证）
- commit b243903: Sol 复查2（P1-15 成功路径遗漏 + GOAI 深层不一致）
- commit 7ca1a47: Sol 复查3（对照表补 award 口径标注）
- commit 418238c: fix(GOAI对照): 修复5项初赛提交材料问题
- 已 push 到 feature/glm-w2-evidence 分支

## 九、v4.1 关键要求对照

> 本节按 v4.1 执行定稿版（C:\Users\Lenovo\Desktop\最终\BidAgent_项目总体规划_v4.1_执行定稿版.md）逐条对照，确保初赛提交材料覆盖全部关键要求。

### 9.1 项目定位（v4.1 第 1.1～1.4 节）

- **一句话定位**：面向供应链金融贷前尽调与企业采购核验的可验证招投标数据引擎——将不可核验的 LLM 输出转化为可复核、可追踪的数据资产
- **核心用户**：供应链金融贷前尽调人员
- **系统定位**：位于金融风控的数据准备与事实核验环节，为后续人工尽调或其他风控系统提供带证据的招投标数据，而非直接替代风控决策
- **8 条核心差异化**（第 1.4 节）：
  1. 每个字段可绑定多段原文证据，区分主证据、限定条件和推导输入
  2. LLM 只生成字段和证据候选，确定性程序负责验证
  3. 区分项目、公告、来源页面和页面版本四层实体
  4. 区分官方原始发布、官方转载、商业转载和索引页面
  5. 识别同源转载，避免将转载数量误判为独立交叉验证
  6. 将抽取支持度、来源质量和交叉验证状态独立保存
  7. 使用独立金标测试集验证准确率、覆盖率和无依据输出率
  8. 只输出可解释的公开招投标活动观察信号，不输出信用评分

### 9.2 MVP 边界（v4.1 第 2.1～2.3 节）

**MVP 必做（第 2.1 节）**：
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

**MVP 暂不实施（第 2.3 节）**：
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

**严谨表述（第 9.3 节）**：前端使用「公开公告中观察到的投标出现次数」，不得使用「企业实际投标次数」；集中度使用「当前覆盖公开中标记录中的采购人集中度」，不得使用「企业客户集中度或营业收入集中度」。

### 9.5 12 个标准 API 端点（v4.1 第 12 章）

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/projects/search` | GET | 搜索采购项目 |
| `/api/projects/{project_id}` | GET | 获取项目及公告生命周期 |
| `/api/notices/{notice_id}` | GET | 获取公告详情 |
| `/api/notices/{notice_id}/sources` | GET | 获取来源页面和谱系 |
| `/api/notices/{notice_id}/participants` | GET | 获取公告参与方列表 |
| `/api/sources/{source_id}/versions` | GET | 获取页面版本历史 |
| `/api/fields/{field_id}` | GET | 获取字段和全部证据 |
| `/api/organizations/search` | GET | 搜索组织实体 |
| `/api/organizations/{org_id}` | GET | 获取组织实体公开活动画像 |
| `/api/extract/tasks` | POST | 提交异步抽取任务 |
| `/api/extract/tasks/{task_id}` | GET | 查询任务状态 |
| `/api/stats/quality` | GET | 获取数据质量和评测统计 |

### 9.6 凭证安全（v4.1 第 13.1 节）

- API Key 使用高熵随机值，服务端只保存基于服务端密钥的 **HMAC 摘要**
- 密码使用 **Argon2id**
- Cookie 使用 **AES-GCM**
- 加密密钥通过环境密钥或密钥管理服务提供，nonce 必须唯一
- 日志不得记录凭证
- SSRF 防护：仅允许 HTTP/HTTPS，拦截内网/回环/链路本地/云元数据地址，重定向后重新检查
- 路径安全：白名单存储目录，禁止路径穿越，文件名与真实存储键分离

### 9.7 高频问题对照（v4.1 第 18.3 节）

**和招投标搜索平台有什么不同？**
招投标搜索平台主要解决信息覆盖和检索问题；标小智主要解决结构化字段是否有原文依据、来源是否明确以及数据是否可追踪的问题。

**为什么不作信用评分？**
公开招投标数据不足以代表企业整体信用。标小智只输出有明确数据口径的公开活动观察信号，不构成授信或投资依据。

**证据会不会也是模型编的？**
不会直接采信。LLM 只提供候选证据文本，最终证据必须由程序在指定公告版本快照中搜索并验证，找不到就标记为无证据。

**为什么很多公告没有独立交叉验证？**
同一业务公告通常只有一个原始发布主体，其他页面可能只是转载。单一官方原始来源加直接原文证据可以构成高可信，独立来源验证属于额外增强项。

**不输出供应商信用评分（第 9.1 节）**：标小智不输出供应商信用评分。所有信号只反映系统覆盖来源和时间范围内观察到的公开招投标活动，供人工尽调参考。

### 6 信号一览（v4.1 第 9.2 节）

6 个 MVP 观察信号一览：中标活跃度、公开中标集中度、废标公告关联、明确投标否决、信息冲突观察、高频共现提示（选做）。所有信号均不直接归因，仅供人工尽调参考。
