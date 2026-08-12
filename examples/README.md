# examples/ 示例输入输出

本目录提供 3 条真实公告的抽取结果示例，供评委验证系统功能与数据真实性。所有数据均来自 `data/bidagent.db`（DB 真实记录，非 mock）。

## 示例清单

| 文件 | 公告类型 | 项目名称 | DB id | 字段数 | 证据数 |
|---|---|---|---|---|---|
| `01_tender_sample.json` | 招标公告 (tender) | 东南大学网络与信息中心数据中心云平台维保服务采购项目 | 114 | 5 | 5 |
| `02_award_sample.json` | 中标公告 (award) | 东南大学苏州校区教室设备升级改造采购项目 | 25 | 5 | 5 |
| `03_correction_sample.json` | 更正公告 (correction) | 华北电力大学超临界CO2氛围可光学观测燃烧器更正公告 | 97 | 2 | 2 |

## 数据结构说明

每个 JSON 文件包含以下顶层字段：

- `meta`: 示例元信息（sample_id / notice_type / description / source / fetched_at）
- `notice`: 公告基本信息（tender_id / project_name / notice_type / source_platform / source_url / publish_time / tender_org / 金额等）
- `extracted_fields`: LLM 抽取 + 确定性程序验证后的字段列表
  - `field_name`: 字段名（project_identifier / purchaser_name / winner_name / amount / publish_date / bid_deadline）
  - `raw_value`: 原文抽取值
  - `normalized_value`: 归一化后的数值（金额类字段）
  - `display_grade`: 展示等级（high / review / low）
  - `support_level`: 抽取支持度（direct / inferred / derived）
  - `evidence_id`: 关联的证据 id
- `evidence`: 字段证据列表
  - `evidence_text`: 公告原文证据片段
  - `match_method`: 匹配方法（exact / stripped / no_punct / substring / fuzzy）
  - `verified`: 是否通过确定性程序验证（true / false）
  - `confidence`: 置信度（0-100）
  - `raw_start` / `raw_end`: 原文快照中的字符偏移量（可在快照中稳定复现）

## 验证方法

评委可通过以下方式验证示例真实性：

```bash
# 1. 查询数据库验证字段数与证据数
cd BidAgent
python -c "import sqlite3; c=sqlite3.connect('data/bidagent.db').cursor(); print('字段:', c.execute('SELECT COUNT(*) FROM extracted_fields WHERE tender_id=114 AND is_current=1').fetchone()[0]); print('证据:', c.execute('SELECT COUNT(*) FROM evidence WHERE tender_id=114').fetchone()[0])"

# 2. 启动服务后访问 API 获取同一公告
# GET /api/notices/114  返回公告详情
# GET /api/fields/{field_id}  返回字段和全部证据

# 3. 对照 Web Demo
# http://localhost:8000/ui  → 招标检索 → 搜索"东南大学" → 点击公告查看证据定位
```

## 核心差异化体现

1. **LLM 只生成候选，确定性程序验证**：所有证据 `verified=true`，`match_method=exact`，表明字段值在原文中精确匹配到。
2. **证据可回溯**：每条证据带 `raw_start`/`raw_end` 偏移量，可在页面快照中高亮定位，不依赖实时网页 DOM。
3. **展示等级**：示例中字段均为 `review` 级（direct 支持），系统会根据支持度 + 来源质量 + 交叉验证状态综合分级。
4. **更正公告语义**：`03_correction_sample.json` 的证据文本包含"更正为"字样，体现更正公告的字段语义识别。
