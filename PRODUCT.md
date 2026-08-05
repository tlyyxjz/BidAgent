# PRODUCT.md

## Product

**标小智（BidAgent）** — 供应链金融招投标数据引擎。GOAI 2026 大赛参赛作品。

从政府采购/招标公告中抽取结构化字段（金额、日期、编号、机构等），
用零 LLM 依赖的确定性验证引擎校验，并提供 span 级证据溯源。

## Users

- 评委与大赛评审（演示视角，重观感与可信度）
- 供应链金融尽调人员（查公告、核数据、看版本历史）
- 数据运维（质量看板、订阅推送）

## Register

product

## Surfaces

8 个 vanilla HTML 单文件页面（FastAPI StaticFiles 托管，无构建工具）：

| 页面 | 角色 |
|---|---|
| workbench.html | 主入口工作台（侧边导航 + 概览） |
| notice_list.html | 公告列表（筛选/分页） |
| notice_detail.html | 公告详情（字段 + 证据高亮） |
| org_profile.html | 机构画像 |
| quality_dashboard.html | 数据质量看板（echarts） |
| search.html | 语义/关键词搜索 |
| version_history.html | 版本历史时间线 |
| chat.html | 对话式查询 |

## Tech constraints

- 不得引入构建工具/npm 依赖；保持单文件 HTML + 内联 CSS/JS
- 可复用本地 vendor：echarts.min.js、phosphor-icons.min.css
- 现有设计 token 以各页内联 :root CSS 变量为准（Ant 风格色板，主色 #1677ff）
- 后端 API 已稳定（v4.1），页面通过 fetch 调用 /api/* 端点