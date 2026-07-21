# 豆包 Turbo 代码审查 Prompt

> 复制下方框内全部内容到豆包 Turbo 任务模式，让它审查代码

---

## 审查 Prompt（复制以下内容到豆包）

```
你是资深 Python 企业级代码审查专家。请审查一个为「2026 AI 先锋未来人才大赛 · 超聚变命题」开发的招投标信息聚合工具的代码。

【项目背景】
- 命题：实现一个可运行的招投标信息聚合工具
- 技术栈：FastAPI + SQLAlchemy 2.x async + Playwright + python-docx + DeepSeek LLM
- 项目路径：C:\Users\Lenovo\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\work-mode-projects\6a57291a0778ce48bfe693d2\ppp_dev\scrapeflow
- 现有测试：53 个全部通过

【审查重点（按优先级）】
1. **安全性**：SQL 注入、API Key 泄露、敏感信息明文存储、CORS 配置、SSRF 漏洞
2. **async 正确性**：是否阻塞事件循环、AsyncSession 使用是否正确、连接泄漏
3. **异常处理**：是否吞掉异常、是否有降级路径、日志是否完整
4. **性能**：N+1 查询、未加索引的字段、内存泄漏、连接池配置
5. **企业级规范**：PEP8、类型注解、错误响应格式统一性、配置管理
6. **命题覆盖**：6 项硬要求是否完整实现（意图解析5槽位/2+网站1+登录/5字段汇总/定时执行/增量推送/Word命名）

【需要审查的核心文件】
1. app/llm/parser.py — LLM 意图解析（DeepSeek + 关键词降级）
2. app/llm/prompts.py — Few-shot Prompt 模板
3. app/llm/schemas.py — ParsedFilters 5 槽位
4. app/report/docx_generator.py — Word 报告主逻辑（命题命名规则）
5. app/report/docx_components.py — 5 字段明细表
6. app/scheduler/subscription.py — 定时订阅 + 增量推送
7. app/api/subscribe.py — 订阅 API（6 个端点）
8. app/api/tender.py — 招标信息管理 API
9. app/models/tender.py — Tender 表（23 字段）
10. app/models/subscription.py — Subscription + PushLog
11. app/processors/attachment_downloader.py — 附件下载
12. app/main.py — FastAPI 入口

【输出格式要求】
按以下格式输出审查报告：

## 一、严重问题（Critical，必须修复）
- 文件路径:行号
- 问题描述
- 修复建议（含代码示例）

## 二、重要问题（Major，建议修复）
- 同上格式

## 三、一般问题（Minor，可选修复）
- 同上格式

## 四、亮点（做得好的地方）
- 简短列出

## 五、命题覆盖度评估
| 命题硬要求 | 实现状态 | 评分 |
|---|---|---|
| 1. 意图解析5槽位 | ✅/⚠️/❌ | 1-10 |
| 2. 2+网站1+登录 | ✅/⚠️/❌ | 1-10 |
| 3. 内容清洗去重 | ✅/⚠️/❌ | 1-10 |
| 4. 5字段汇总 | ✅/⚠️/❌ | 1-10 |
| 5. 定时执行 | ✅/⚠️/❌ | 1-10 |
| 6. 增量推送 | ✅/⚠️/❌ | 1-10 |

## 六、总体评分
- 代码质量：__/10
- 命题覆盖：__/10
- 企业级成熟度：__/10
- 综合建议（200 字以内）

请逐文件审查，不要遗漏。审查要严格、专业、可执行。
```

---

## 使用步骤

1. 打开豆包（doubao.com）
2. 切换到 **任务模式**，选择 **豆包 Turbo**
3. 复制上方代码块内的全部内容粘贴到豆包
4. 等待豆包完成审查（约 3-5 分钟）
5. 把审查结果复制贴回来给我
6. 我根据审查结果修复问题

## 审查预期耗时

- 豆包 Turbo 审查：3-5 分钟
- 我修复问题：10-30 分钟（取决于问题数量）
- 最终全量测试验证：2 分钟

## 备选方案

如果豆包 Turbo 不可用，可以用：
- **GPT-5.6 Sol**：粘贴同样 prompt
- **Claude Fable 5**：粘贴同样 prompt
- **我自审**：直接告诉我"你自己审查"，我用 GLM-5.2 + WebSearch 查最佳实践自审
