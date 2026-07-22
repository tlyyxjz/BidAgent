# 变更记录

本文件记录 BidAgent W1-05 标注工具（annotation_tool）的变更历史。

## [Unreleased] - 2026-07-22

### P0 跨公告数据污染修复（人工验收反馈）

#### 根因
- `importTextFile()` 只替换 `state.rawText`，**未创建新标注文档**
- 未重置 `state.annotation` / `state.docMeta` / `state.currentFieldIndex` / `state.currentValueIndex` / `state.editingEvidenceIndex`
- 未清除 `overlapWarning`、未重置公告类型与备注、未清除校验错误
- 未生成独立 `document_id`，继续沿用上一篇 ID，localStorage 草稿键冲突
- 导入新 TXT 后右侧六类字段仍保留上一篇公告的值、证据、高亮，造成跨公告数据污染（P0）

#### 修复（app.js / index.html / style.css）

1. **新文档独立 document_id（确定性生成）**
   - `sanitizeFileName()`：去除路径和扩展名，非中文/字母/数字/下划线/连字符替换为下划线
   - `generateDocumentId(fileName, contentHash)`：`sanitized_name + '_' + sha256前12位`
   - 相同文件（文件名 + 内容）再次导入时生成相同 document_id，识别为同一文档，询问是否恢复草稿

2. **导入前保存提示**
   - `hasNonEmptyAnnotation()`：判断当前标注是否含非空值/证据/备注
   - 若当前文档非空，弹 `confirm("是否保存当前草稿后再导入新公告？")`，取消则中止导入，不静默丢弃

3. **完整重置状态**
   - 重置 `state.rawText`、`state.annotation`、`state.docMeta`、`state.currentFieldIndex`、`state.currentValueIndex`、`state.editingEvidenceIndex`
   - 清除 `overlapWarning`
   - 通过 `inferNoticeType()` 重新推断公告类型
   - 通过 `createBlankAnnotationForImport()` 初始化六类字段为 `gold_status=''`（待判断），进度显示 0/6，与 `absent`（不存在，计入完成）区分
   - 清空备注、校验错误、重叠提示

4. **localStorage 按新 document_id 隔离**
   - 草稿键：`bidagent_annotation_draft_${document_id}`
   - 元数据键：`bidagent_annotation_meta_${document_id}`
   - 新增 `LAST_ACTIVE_DOC` 键记录最后活动文档，刷新后优先恢复，不再固定加载 `sample-001`

5. **导入 JSON 与 TXT 行为区分**
   - TXT：创建或恢复对应文档（生成新 document_id 或恢复草稿）
   - JSON：加载 JSON 内对应 document_id 的完整标注，校验失败不覆盖当前状态

6. **文件大小校验**
   - 超过 `MAX_IMPORT_SIZE`（5 MB）或为空 TXT 时明确报错，不覆盖当前文档

7. **页面状态显示区**
   - `index.html`：新增 `#docStatusInfo` 状态栏，显示当前文件名、document_id、内容哈希、是否恢复草稿
   - `style.css`：新增 `.doc-status-bar` / `.doc-status-item` / `.doc-status-restored` 样式

8. **`updateDocStatusDisplay()`**：导入后更新状态栏；恢复草稿显示绿色徽章，新建显示灰色徽章

#### 新增测试（tests/e2e.spec.js 场景 18-26）

- 18. 导入 TXT B 后字段和值全部为空（gold_status=''）
- 19. B 的 document_id 与 A 不同
- 20. B 不显示 A 的证据高亮
- 21. 重新导入 A 时可恢复 A 自己的草稿
- 22. 相同 TXT 再次导入时不创建随机新文档
- 23. 导入 B 后刷新只恢复 B，不恢复 A
- 24. 新 TXT 导入后完成度为 0/6（非沿用上一篇）
- 25. 切换文件前的保存提示正常
- 26. 导入空 TXT 或超大 TXT 时明确报错且不覆盖当前文档

#### 测试结果

- 35 项浏览器单元测试：全部通过 ✅
- 26 项 Playwright 端到端测试（原 17 + 新增 9）：全部通过 ✅
- GLM Schema 校验：7 条证据偏移量全部通过 ✅
- 后端 519 项回归测试：全部通过 ✅

### 不变项

- JSON Schema（未修改 `backend/schemas.py`）
- UTF-16 偏移逻辑（未修改 `validate_schema.py` / `common.py`）
- 未修改 `backend/`
- 未合并 `main` / `develop`

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
