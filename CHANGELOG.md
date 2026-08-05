# 变更日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范。

## [Unreleased] - 2026-08-05

### Added
- GitHub Actions CI（.github/workflows/ci.yml）：pytest 全量 + 覆盖率 90% 阈值 + pip-audit 依赖漏洞扫描

### Fixed
- 版本历史假"复查无变化"版本：created_at/updated_at 双默认值微秒级差值导致新建记录被误判为已更新，增加 10ms 容差（real_demo_versions / v41_sources）
- 两个时间戳依赖用例改为显式设值，消除时钟 flaky（test_real_versions_single_version / test_versions_with_updated_at）；新增微秒差/超容差两个边界回归用例

### Changed
- 清理全部 pytest warnings：移除 test_new_modules.py 模块级 asyncio mark 误标；datetime.utcnow() 弃用调用改为 datetime.now(timezone.utc)（v41_extract / v41_stats / organization_profile）；app/scheduler/utils.py docstring 非法转义改为 raw string
- README 评测数据同步至 final5 实测（field_precision 98.08%、null_false_positive_rate 0.63%），并补充 span 级与字段级证据指标口径说明
- 测试 1937 passed · 0 warnings · 覆盖率见 README（含 2 个容差边界回归用例）

## [4.1.1] - 2026-08-04

### Changed
- 大规模模块拆分：28个超过300行的文件拆分为约90个文件，每个文件≤300行
- 所有拆分通过 re-export 保持公开接口不变，业务逻辑零修改
- 测试通过 1884 passed, 覆盖率 90.42%

### 拆分详情
- app/api/demo_api.py (1391行) → 12个子模块
- app/processors/field_validator.py (708行) → 5个子模块
- app/llm/extractor.py (707行) → 4个子模块
- app/services/data_deletion.py (670行) → 9个子模块（mixin包）
- app/processors/evidence_locator.py (663行) → 6个子模块（mixin包）
- app/processors/source_lineage.py (640行) → 3个子模块
- app/core/scraper.py (512行) → 5个子模块
- 其他21个文件同步拆分

### Removed
- 删除临时文件：.bak/.new/空文件/_inspect*/_tmp_pytest/_test_isolation_backup

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
- 测试用例扩充至 1882 项（含 parametrize 展开），覆盖率提升至 90.17%
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
- 1882 项测试用例（含 parametrize 展开），核心模块覆盖率 90.17%
