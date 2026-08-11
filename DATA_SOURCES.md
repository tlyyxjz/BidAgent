# 标小智 - 数据来源与合规说明

> 本文档说明标小智系统的数据来源、采集合规措施、数据范围、隐私保护与数据口径，确保数据的合法性与透明度。

---

## 1. 数据源清单

### MVP 冻结范围（2 个官方平台）

| 数据源 | 域名 | 类型 | 适配器文件 | 说明 |
|---|---|---|---|---|
| 中国政府采购网 | ccgp.gov.cn | 官方公开 | `app/templates/ccgp.py` | MVP 主适配器，已灌库 701 条 |
| 全国公共资源交易平台 | ggzy.gov.cn | 官方公开 | `app/templates/ggzy.py` | MVP 适配器，已实现未大量灌库 |

### 复赛规划（暂未接入）

| 数据源 | 类型 | 状态 |
|---|---|---|
| 千里马招标网 (qianlima.com) | 商业平台 | 模板已实现（`templates/qianlima.py`），需登录态，MVP 不接入 |
| 中国招标投标公共服务平台 (chinabidding.com) | 商业平台 | 模板已实现（`templates/chinabidding.py`），MVP 不接入 |

> **口径说明**：初赛 MVP 范围明确为 2 个官方平台。视频中"实时抓取 2 个官方平台（ccgp + ggzy_national）"即为此范围，不提"30+ 平台"（后者为复赛规划，现在提会混淆范围边界）。

---

## 2. 采集合规措施

### 2.1 采集流程（`app/core/scraper.py`）

```
URL 输入
  ↓
① SSRF 防护（url_safety.py）
   - 仅允许 HTTP/HTTPS 协议
   - 拦截内网/回环/链路本地/云元数据地址
   - 重定向后重新检查目标 IP
  ↓
② robots.txt 合规检查（robots_checker.py）
   - 30 分钟域名级缓存
   - 不可达时默认允许（fail-open）
   - 遵守 Disallow 规则
  ↓
③ 来源白名单检查（source_whitelist.py）
   - 维护允许采集的来源平台/域名清单
   - 支持运行时下架/重新启用
  ↓
④ 域名级频率限制（rate_limiter.py）
   - 默认 8 秒间隔
   - 按域名独立计数
   - 失败时回滚 reservation（不占用下次配额）
   - 403/封禁时立即停止，不重试，不规避
  ↓
⑤ 模板合并（templates/ccgp.py 等）
   - 站点特定选择器与解析规则
  ↓
⑥ Playwright / httpx 抓取
   - Playwright: 处理 JS 渲染页面
   - httpx: 处理静态页面
   - 默认 UA，不使用代理池绕过
  ↓
⑦ 页面快照存储（snapshot_manager.py）
   - 原文快照 + SHA256 哈希
   - 版本追踪，历史版本不被覆盖
```

### 2.2 合规红线

| 红线 | 实现 | 验证命令 |
|---|---|---|
| 不绕过登录墙 | 不采集付费内容，千里马需登录态的部分不接入 | - |
| 不抓取付费内容 | 仅采集公开公告页面 | - |
| 遵守 robots.txt | `robots_checker.py` 30 分钟缓存 | `grep -n "RobotFileURLopener\|robotparser" app/core/robots_checker.py` |
| 频率限制 | 域名级 8 秒间隔 | `grep -n "DomainRateLimiter\|8" app/core/rate_limiter.py` |
| 403 即停 | 不重试，不规避 | `grep -n "HttpForbiddenError\|403\|raise" app/core/scraper.py` |
| 默认 UA | 不伪装浏览器 | `grep -n "User-Agent\|user_agent" app/core/scraper.py` |
| 不使用代理池绕过 | PROXY_LIST 留空 | `.env.example` 中 `PROXY_LIST=` |

---

## 3. 数据采集范围

### 3.1 公告类型

| 类型 | notice_type | 数据库数量 | 说明 |
|---|---|---|---|
| 招标公告 | tender | 516 | 公开招标/竞争性谈判/单一来源等 |
| 中标公告 | award | 104 | 中标结果/成交公告 |
| 更正公告 | correction | 79 | 澄清/更正/废标公告 |
| 未分类 | None | 2 | - |
| **合计** | - | **701** | 全部来自 ccgp |

### 3.2 抽取字段（6 类核心字段）

| 字段名 | 数据库数量 | 说明 |
|---|---|---|
| publish_date | 154 | 发布日期 |
| amount | 114 | 金额（预算/中标金额）|
| purchaser_name | 99 | 采购人名称 |
| project_identifier | 92 | 项目编号 |
| bid_deadline | 63 | 投标截止日期 |
| winner_name | 60 | 中标人名称 |
| **合计** | **582** | - |

### 3.3 证据

- 证据总数：586 条
- 每条证据包含：原文片段、上下文、字符偏移量（raw_start/raw_end）、匹配方法、验证状态、快照 SHA256
- 100% 可回溯公告原文

---

## 4. 数据口径说明

### 4.1 多口径区分

| 口径 | 数值 | 说明 |
|---|---|---|
| 数据库公告总数 | 701 | `tenders` 表全量（全部 ccgp）|
| 有抽取字段的公告数 | 154 | 至少有 1 条 `extracted_fields` 记录的公告 |
| 抽取字段总数 | 582 | `extracted_fields` 表记录数 |
| 证据总数 | 586 | `evidence` 表记录数 |
| 金标评测集 | 598 | `tests/fixtures/gold/gold_dataset_v4.json`（评测用，非库内数据）|
| W3 评测子集 | 100 | 金标子集（ccgp_w3 来源）|
| 实时采集示例 | 7 | Demo 演示用实时抓取的公告 |
| 组织机构 | 113 | `organizations` 表记录数 |

### 4.2 视频与材料口径

字幕与提交材料中使用的口径：
- "初赛库内 162 篇真实公告" → 指 SimHash 去重后的有效公告
- "实测 582 个抽取字段" → `extracted_fields` 表真实记录
- "586 条可溯源证据" → `evidence` 表真实记录
- "598 篇全量金标" → 评测用金标集（非库内数据）
- "2 个官方平台" → ccgp + ggzy_national（MVP 范围）

> **注意**：数据库实际有 701 条 tender 记录，但部分为同源转载/重复抓取，SimHash 去重后有效公告约 162 篇。视频口径以 162 篇为准（去重后），不使用 701（含重复）。

---

## 5. 隐私保护

### 5.1 不采集的隐私数据

- 身份证号码
- 手机号码（联系人电话用 SHA256 hex 存储）
- 家庭住址
- 个人财务信息

### 5.2 凭证安全存储

| 数据类型 | 存储方式 | 说明 |
|---|---|---|
| 联系人电话 | SHA256 hex | 不可逆，无法还原原号 |
| 联系人邮箱 | SHA256 hex | 不可逆 |
| API Key | HMAC-SHA256 摘要 | 服务端密钥 + `secrets.compare_digest` 防时序攻击 |
| 用户密码 | Argon2id 哈希 | 防彩虹表/暴力破解 |
| Cookie | AES-GCM 加密 | nonce 唯一，`os.urandom` 生成 |
| SECRET_KEY | 64 字符 hex | `secrets.token_hex(32)` 生成 |

### 5.3 日志规范

- 结构化日志带 `request_id` 上下文
- **日志不得记录凭证**（API Key / 密码 / Cookie）
- 日志不记录完整手机号/邮箱

---

## 6. 数据删除

### 6.1 数据删除服务（`app/services/data_deletion/`）

支持 5 种范围删除，记录审计日志：

| 删除范围 | 实现文件 | 说明 |
|---|---|---|
| 按来源 URL | `_source_url.py` | 删除指定 URL 的所有数据 |
| 按来源平台 | `_source_platform.py` | 删除整个平台的数据 |
| 按公告来源实例 | `_notice_source_instance.py` | 删除特定来源实例 |
| 按页面快照 | `_page_snapshot.py` | 删除特定版本快照 |
| 按用户授权数据 | `_user_authorized_data.py` | 用户授权删除 |

### 6.2 审计日志

所有删除操作记录审计日志，包含：操作时间、操作者、删除范围、删除数据量。

---

## 7. 风险提示

### 7.1 输出声明

- 报告输出明确标注「AI 生成，仅供参考，决策请人工复核」
- 定位为数据服务商，不提供金融建议，不承担金融决策责任
- 不输出信用评分，不判断围标，不提供授信建议

### 7.2 6 个观察信号（v4.1 §9.2，严格不输出信用评分）

| 信号 | 说明 |
|---|---|
| 中标活跃度 | 近 90 天公开中标次数和金额趋势，不作正负定性 |
| 公开中标集中度 | 当前覆盖数据中 Top 3 采购人及地区占比 |
| 废标公告关联 | 企业在废标或流标公告中被观察到的次数，不直接归因 |
| 明确投标否决 | 公告明确写明企业投标被否决，并记录原因 |
| 信息冲突观察 | 相同事实断言在不同有效来源中出现矛盾 |
| 高频共现提示（选做）| 企业与其他企业在同一标段被反复观察到，不用于判断围标 |

> **严谨表述**：使用「公开公告中观察到的投标出现次数」，不得使用「企业实际投标次数」；高频共现必须附带说明「仅凭共现不能判断企业关联关系或围标行为」。

---

## 8. 数据验证方法

评委可通过以下方式验证数据真实性：

```bash
# 1. 查询数据库总数
cd BidAgent
python -c "import sqlite3; c=sqlite3.connect('data/bidagent.db').cursor(); print('tenders:', c.execute('SELECT COUNT(*) FROM tenders').fetchone()[0]); print('fields:', c.execute('SELECT COUNT(*) FROM extracted_fields').fetchone()[0]); print('evidence:', c.execute('SELECT COUNT(*) FROM evidence').fetchone()[0])"

# 2. 验证来源平台（预期：全部 ccgp）
python -c "import sqlite3; c=sqlite3.connect('data/bidagent.db').cursor(); [print(r) for r in c.execute('SELECT source_platform, COUNT(*) FROM tenders GROUP BY source_platform')]"

# 3. 验证公告类型分布
python -c "import sqlite3; c=sqlite3.connect('data/bidagent.db').cursor(); [print(r) for r in c.execute('SELECT notice_type, COUNT(*) FROM tenders GROUP BY notice_type')]"

# 4. 对照 examples/ 示例
python -c "import json; d=json.load(open('examples/01_tender_sample.json')); print('字段:', len(d['extracted_fields']), '证据:', len(d['evidence']))"

# 5. 启动服务后通过 API 验证
# GET /api/notices/114  → 东南大学网络中心招标公告
# GET /api/notices/25   → 东南大学苏州校区中标公告
# GET /api/stats/quality → 数据质量统计
```

---

## 9. 合规审查参考

| 文档 | 位置 | 说明 |
|---|---|---|
| 合规声明 | `_w2_report/compliance.md` | 数据来源 / 隐私保护 / AI 反幻觉 / 行业边界 |
| 验证规则清单 | `docs/验证规则清单_v1.0.md` | 验证引擎 34 条规则显性化 |
| 系统架构 | `ARCHITECTURE.md` | 系统架构与技术实现 |
| 示例数据 | `examples/` | 3 条真实公告示例 |
