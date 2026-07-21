# 变更记录

本文件记录 BidAgent W1-05 标注工具（annotation_tool）的变更历史。

## [Unreleased] - 2026-07-21

### 布局修复（人工验收反馈）

#### 根因
- 交付报告声称实现了"六字段紧凑导航 + 单字段编辑器"，但实际代码只有单个 `#fieldsContainer` 容器
- `renderFields()` 一次性渲染 6 个字段卡片纵向堆叠，无独立导航区
- `state.currentFieldIndex` 存在但只用于证据弹窗，从未控制字段编辑器显示
- 35 单元测试 + 12 Playwright 测试只覆盖功能逻辑，未断言布局结构

#### 修复
- **index.html**：新增 `<nav id="fieldsNav">` 独立导航区，位于 `#fieldsContainer` 上方
- **app.js**：
  - `renderFields()` 拆分为 `renderFieldsNav()` + 单字段编辑器渲染
  - 新增 `switchToField(fieldIndex)` 切换当前编辑字段
  - 新增 `flashFieldError(fieldIndex)` 校验失败时切换 + 闪烁
  - `state.currentFieldIndex` 控制只显示当前字段的完整编辑器
  - 导航项显示字段名称、状态徽章、值数量、完成状态
- **style.css**：
  - 新增 `.fields-nav` / `.field-nav-item` / `.field-nav-item.active` 样式
  - `.fields-panel` 改为 flex column 布局，导航区在上、编辑器在下

### 示例公告类型修正

#### 根因
- `sample-001.txt` 原文第 2 行明确写"中标（成交）结果公告"
- 但 `app.js` `init()` 中 `noticeType` 默认硬编码为 `tender`（招标公告）
- 导致页面加载时公告类型显示错误

#### 修复
- **generate.py**：新增 `infer_notice_type(raw_text)` 函数，根据原文关键词推断公告类型
  - 包含"中标"+"结果公告" → `award`
  - 包含"更正公告" → `correction`
  - 包含"招标公告" → `tender`
  - 其他 → `other`
- **generate.py**：`sample_data.js` 新增 `SAMPLE_NOTICE_TYPE` 常量，导出到 `window.SampleData`
- **app.js**：`init()` 使用 `SampleData.SAMPLE_NOTICE_TYPE` 作为默认 `noticeType`（回退 `tender`）
- `noticeType` 仍保存到独立元数据 `bidagent_annotation_meta_${document_id}`，不混入 `extra="forbid"` 的导出 JSON

### 证据核查

- `publish_date`（发布日期）：原文第 50 行"发布日期：2024年3月15日"，证据存在 ✅
- `bid_deadline`（投标截止日期）：原文第 51 行"投标截止日期：2024年3月10日"，证据存在 ✅
- 7 条证据偏移量全部通过 UTF-16 切片验证（`rawText.slice(start, end) === evidence.text`）

### 测试

- 保留原有 35 项浏览器单元测试，全部通过
- 保留原有 12 项 Playwright 端到端测试，全部通过
- 新增布局验收 Playwright 测试（`tests/e2e.spec.js` 场景 13-17）：
  13. 六字段导航区存在且独立
  14. 只显示一个字段编辑器
  15. 点击导航切换字段
  16. 值项完整显示不被截断
  17. 示例公告类型默认为 award

### 不变项

- JSON Schema（未修改 `backend/schemas.py`）
- UTF-16 偏移逻辑（未修改 `validate_schema.py` / `common.py`）
- 现有 35 + 12 = 47 项测试全部通过，无回归
- 未修改 `backend/`
- 未合并 `main` / `develop`
