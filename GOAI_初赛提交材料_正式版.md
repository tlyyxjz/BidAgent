# GOAI 2026 初赛提交材料 - BidAgent 智能标讯助手

**团队**：标小智
**成员**：徐浚钊、王祯明
**学校**：上海建桥大学 计算机科学与技术专业
**日期**：2026 年 8 月

---

## 一、作品简介（500 字）

BidAgent 是一款面向中小企业和供应商的智能招投标信息聚合与字段抽取助手。针对招投标公告信息分散、字段抽取耗时、报价决策缺乏依据三大痛点，BidAgent 构建了六 Agent 协作流水线：公告抓取 Agent（多平台轮询、HTTP 403 智能停止）、字段抽取 Agent（LLM + 证据定位双坐标映射）、证据验证 Agent（L1-L5 五级降级匹配、确定性校验）、风险预警 Agent（废标规则识别、供应商信用评分）、报告生成 Agent（Word 自动生成、附件管理）、推送调度 Agent（cron 定时、幂等推送）。

核心技术亮点包括：(1) 证据定位双坐标映射，LLM 输出原文偏移量 + 语义角色，系统自动校验偏移量与原文一致性；(2) 确定性证据搜索引擎，L1-L5 五级降级匹配（精确→空白归一→全半角→金额/日期格式变体→模糊匹配），不依赖 LLM 即可定位证据；(3) 证据验证流水线，unjustified_rate 从 100% 降至 1.94%，field_precision 达 94.49%（C 组），evidence_precision 达 100%；(4) 22 篇真实金标评测，recall 69.90%，precision 60.63%，IoU avg 0.5307，P50=0.96/P95=1.0。

合规边界明确：数据来源于官方平台，请求频率 ≤ 1 次/8 秒；联系人信息 SHA256 存储；报告标注「AI 生成，仅供参考，决策请人工复核」；定位为数据服务商，不提供金融决策。

技术栈：Python + FastAPI + Playwright + DeepSeek API + SQLite。开源协议 Apache 2.0。

---

## 二、方案 PPT

- 文件：_w2_report/proposal.pptx
- 页数：28 页
- 大小：约 629 KB

---

## 三、代码仓库

- GitHub: https://github.com/tlyyxjz/BidAgent
- 分支：feature/glm-w2-evidence
- 测试：571 passed, 1 skipped（K3 独立复跑确认）

---

## 四、合规声明

- 文件：_w2_report/compliance.md
- 四大块：数据来源 / 隐私保护 / AI 反幻觉 / 行业边界

---

## 五、Demo 视频

- 脚本：BidAgent_Demo_脚本.md
- 时长：90 秒
- 状态：待录制（代码仓库可作为等价可验证材料）
