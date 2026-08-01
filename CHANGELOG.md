# 变更日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范。

## [4.1.0] - 2026-07-31 (W3 交付)

### 新增
- 四组消融实验 A/B/C/D（99 篇金标，unjustified_rate 从 100% 降至 0%）
- Bootstrap CI 置信区间评测（recall 67.96%，IoU avg 0.5339，P50=0.71）
- 证据验证完整闭环：5 级降级匹配 + 双坐标映射 + IoU 边界评测
- 组织画像页：6 维度信用评分 + ECharts 雷达图 + 真实数据接入
- 质量评测页：四组消融对比图 + Bootstrap CI 图 + 错误归因图
- 版本历史页：SHA256 指纹 + 版本时间线 + 附件变更追踪
- 工作台首页：KPI 卡片 + 主链路 Pipeline + 四大能力卡片
- 统一 sidebar 布局（AntD Pro 风格，8 个页面一致）
- 来源谱系：SimHash + source_role + source_group + fact_assertion_key
- 知识增强规划：三大知识库架构设计（法规库/BOQ价格库/供应商图谱）

### 优化
- 项目更名为"标小智"
- 消融实验从三组扩展为四组，金标从 22 篇扩展到 99 篇
- 测试用例扩充至 826 项（含 parametrize 展开），覆盖率提升至 97%
- 前端品牌名统一、Stepper 残留清理、emoji 清理
- PPT 数据全面对齐 W3 最新结果

### 修复
- 消融实验图表 legend 与 yAxis 重叠问题
- 组织画像页数据加载失败（默认组织名 + 真实 API 接入）
- 版本历史页 DOM 元素缺失导致 JS 渲染失败
- FastAPI 依赖注入：别名路由未注入 db 参数
- 搜索页布局对齐问题（旧 header/nav CSS 清理）

## [4.0.0] - 2026-07-24 (W2 交付)

### 新增
- 证据验证闭环：normalizer + evidence_locator + field_validator
- 三组消融实验 A/B/C（22 篇金标）
- IoU 边界质量评测
- 反幻觉校验：金额/日期归一化 + 原文事实比对
- SimHash 64 位去重（汉明距离 ≤3）
- BOQ 报价异常检测（20 类基准价格库）
- 废标风险预警（18 条规则）
- 供应商信用评分（三维度加权）
- 推送幂等：content_hash + 30 分钟去重窗口
- SSRF 三层防护

## [3.0.0] - 2026-07-17 (W1 交付)

### 新增
- 6 Agent 协同架构（意图/采集/加工/质量/金融/交付）
- AgentGraph 编排器 + ExecutionTrace
- 4 平台采集器（ccgp/chinabidding/ggzy/千里马）
- 千里马登录态持久化（16 cookies）
- patchright + stealth 反检测
- Docker 多阶段构建 + 三角色部署（web/worker/scheduler）
- 826 项测试用例（含 parametrize 展开），核心模块覆盖率 97%+
