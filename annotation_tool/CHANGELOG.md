# 变更记录

本文件记录 BidAgent W1-05 标注工具（annotation_tool）的变更历史。

## [Unreleased] - 2026-07-22

### P0 JSON 导入完整性修复（重置后无法导入）

#### 背景
人工测试发现：导出 JSON 后点击重置，JSON 无法重新导入。导出的 JSON 不包含 clean_raw_text，重置后 state.rawText 被清空，导致 evidence 切片校验全部失败。

#### 根因
1. **导出的 JSON 不包含 clean_raw_text**：`exportJson()` 只导出 `document_id, annotator_id, annotation_version, annotation_time, fields`（符合 Schema `extra="forbid"`），但 JSON 无法自恢复原文。
2. **重置后 rawText 被清空**：`resetAnnotation()` 执行 `state.rawText = '';` 和 `clearStorage(docId)`，导致 rawText 为空。
3. **JSON 导入因 evidence 切片不匹配而拒绝**：`importJsonFile()` 使用 `state.rawText`（空字符串）校验，`Schema.verifyEvidenceSpan('', start, end, text)` 返回 `actualText: ''`，与 `evidence.text` 不匹配，所有 evidence 校验失败。
4. **文件 input 未清空 value**：虽然每次创建新 input，但为稳健性应在每次处理后清空 value。

#### 修复（app.js / index.html / tests/e2e.spec.js）

1. **新增"导出标注包"功能（exportBundle）**
   - 导出 bundle JSON，包含 `manifest_version`、`exported_at`、`annotation`（纯净 Schema 格式）、`meta`（`raw_text`、`content_hash`、`source_file_name`）
   - 导出前校验 `state.rawText` 非空，校验标注数据完整性
   - 计算原文 SHA-256 哈希并写入 `meta.content_hash`
   - `annotation` 仍使用 `stripUiMetadataForExport` 剥离 `ui_id`

2. **修改 importJsonFile() 支持两种格式**
   - **标注包（bundle）**：检测 `manifest_version` + `meta` + `annotation`，用 `meta.raw_text` 恢复原文，校验 `meta.content_hash` 与 `computeSha256(meta.raw_text)` 一致（不得跳过），然后校验 `annotation`
   - **纯标注 JSON**：
     - 若当前 `state.rawText` 非空，用它校验
     - 若 `state.rawText` 为空，尝试从 localStorage 按 `data.document_id` 恢复原文
     - 若均失败，明确提示"缺少匹配的 TXT 原文"，显示期望 `document_id` 和哈希前缀
   - 校验失败显示具体原因（错误字段 + 消息 + 原文来源）
   - 校验通过后更新 `state.rawText`（标注包或 localStorage 恢复时）、`state.annotation`、`state.docMeta`

3. **修改 resetAnnotation() 明确提示**
   - 重置前显示详细提示：清除原文、字段标注、证据、备注、localStorage 草稿、文档状态栏
   - 若当前文档有标注数据，建议先"导出标注包"备份

4. **文件 input 清空 value**
   - TXT 和 JSON 导入的 `input.onchange` 处理后执行 `e.target.value = ''`
   - 确保同一文件可再次选择触发 change 事件

5. **新增 index.html 按钮**
   - 工具栏新增"导出标注包"按钮（`#btnExportBundle`）

6. **新增 5 项 Playwright 测试（tests/e2e.spec.js，场景 49-53）**
   - 49. TXT → 标注 → 导出 JSON → 重置 → 先导入 TXT → 导入 JSON，成功
   - 50. 重置后直接导入 JSON，显示明确的缺少原文提示
   - 51. 导入错误 TXT 后再导入 JSON，显示哈希/偏移量不匹配
   - 52. 同一 JSON 连续选择两次，change 事件正常触发
   - 53. 完整标注包导出再导入，可恢复原文、字段和证据

#### 测试结果

- 35 项浏览器单元测试：全部通过 ✅
- 53 项 Playwright 端到端测试（原 48 + 新增 5）：全部通过 ✅
- 后端 519 项回归测试：全部通过 ✅

### 不变项

- JSON Schema（未修改 `backend/schemas.py`）
- UTF-16 偏移逻辑（未修改 `validate_schema.py` / `common.py`）
- 未修改 `backend/`
- 未合并 `main` / `develop`
- 不得为了允许导入而跳过哈希和证据切片校验

### P0-1 / P0-2 阻塞缺陷修复（多值滚动 + 证据操作后字段保持）

#### 背景
人工标注中发现两个 P0 阻塞缺陷：
1. **P0-1**：打开"金额及金额类型"后连续添加多个金额值，右侧只能看到第一个值，后续新增值无法滚动查看或编辑。
2. **P0-2**：在非第一个字段（金额/中标人等）选中原文并添加证据保存后，页面自动跳回第一个字段"项目编号"。

#### 根因

**P0-1（多值滚动失效）**
- `.text-panel` / `.fields-panel` 是 `display: flex; flex-direction: column`，但子项 `.text-container` / `.fields-container` 缺少 `min-height: 0`，导致 flex column 子项默认 `min-height: auto` 撑开父容器，`overflow-y: auto` 无法正确生效，内容溢出而非滚动。
- `.fields-nav` 缺少 `flex-shrink: 0`，在空间不足时被压缩，进一步破坏滚动布局。
- `.field-card` 使用 `overflow: hidden` 截断值列表，导致多值无法完整展示。

**P0-2（添加证据后跳回项目编号）**
- `closeEvidencePreview()` 和 `closeEvidenceModal()` 在关闭弹窗时执行 `state.currentFieldIndex = -1` 和 `state.currentValueIndex = -1`。
- 随后 `renderFields()` 检测到 `currentFieldIndex < 0` 时回退到 `0`，导致页面始终跳回第一个字段"项目编号"。
- 缺少跨 `renderFields()` 的稳定值标识，无法在重新渲染后恢复滚动位置和当前值项。

#### 修复（app.js / style.css / tests/e2e.spec.js）

1. **CSS 滚动容器修复（style.css）**
   - `.text-panel` / `.fields-panel`：新增 `min-width: 0`，避免 flex 子项溢出
   - `.text-container`：新增 `min-height: 0`，使 `overflow: auto` 正确生效，左侧原文独立滚动
   - `.fields-nav`：新增 `flex-shrink: 0`，字段导航在空间不足时不被压缩，保持可见
   - `.fields-container`：新增 `min-height: 0`，使 `overflow-y: auto` 正确生效，右侧字段编辑区独立纵向滚动
   - `.field-card`：`overflow: hidden` → `overflow: visible`，不再截断值列表
   - 新增 `.value-item.collapsed`、`.value-collapse-btn`、`.value-item.value-just-added` 样式，支持值项折叠/展开和新增值闪烁动画

2. **ui_id 稳定标识系统（app.js）**
   - `generateUiId()`：生成 `v_<timestamp>_<counter>` 格式的唯一 ID
   - `ensureValueUiId(value)` / `ensureAllValuesHaveUiId(annotation)`：为所有 value 分配 ui_id
   - `stripUiMetadataForExport(fields)`：导出 JSON 时剥离 `ui_id` 等 UI 元数据，确保符合 Schema `extra="forbid"` 约束
   - ui_id 仅存在于前端内存和 localStorage 草稿中，不混入导出 JSON

3. **状态保持的 renderFields()（app.js）**
   - 重新渲染前保存 `container.scrollTop` 和 `state.currentFieldIndex`
   - 只在 `currentFieldIndex < 0` 或越界时回退到 0，不主动重置有效值（P0-2 核心）
   - 渲染后仅在字段未变时恢复 `scrollTop`，避免跨字段切换时滚动位置错乱
   - 处理 `pendingFocusUiId`：添加新值后自动滚动到新增值 + 展开并聚焦 `raw_value` 输入框

4. **值项折叠状态保持（app.js）**
   - `state.valueCollapsed`：以 `ui_id` 为键记录折叠状态，跨 `renderFields()` 保持
   - `createValueItem()` 根据 `valueCollapsed[ui_id]` 恢复折叠状态
   - 折叠/展开按钮切换 `state.valueCollapsed[ui_id]` 并更新 DOM

5. **添加新值后自动展开+滚动+聚焦（app.js）**
   - `addFieldValue()`：设置 `state.pendingFocusUiId = newValue.ui_id`，确保新值不折叠
   - `renderFields()` 末尾处理 `pendingFocusUiId`：滚动到新增值、添加闪烁动画、聚焦 `raw_value` 输入框

6. **证据操作后保持当前字段（app.js）**
   - `closeEvidencePreview()`：移除 `state.currentFieldIndex = -1` 和 `state.currentValueIndex = -1`，只重置 `editingEvidenceIndex`
   - `closeEvidenceModal()`：同上
   - 添加/编辑/删除证据、添加/编辑字段值、展开/折叠值、自动保存后均不改变 `currentFieldIndex`

7. **导出 JSON 剥离 UI 元数据（app.js）**
   - `exportJson()` 使用 `stripUiMetadataForExport(state.annotation.fields)` 输出纯净 fields 数组
   - 确保导出 JSON 不包含 `ui_id`、`valueCollapsed`、`pendingFocusUiId`、滚动位置等 UI 元数据

8. **新增 12 项 Playwright 测试（tests/e2e.spec.js，场景 37-48）**
   - 37. 金额字段添加 5 个值均可滚动到并编辑
   - 38. 添加第 5 个值后自动滚动并聚焦 raw_value
   - 39. 在金额字段添加 primary 证据后仍停留在金额字段
   - 40. 在中标人第二个值添加证据后仍停留在中标人和第二个值
   - 41. 编辑证据后不跳回项目编号
   - 42. 删除证据后不跳回项目编号
   - 43. 添加/删除值后当前字段不变
   - 44. 自动保存后当前字段不变
   - 45. 左右滚动位置不互相影响
   - 46. 1366×768 下所有多值内容可访问
   - 47. 导出 JSON 不包含 ui_id 等 UI 元数据
   - 48. 原有测试无回归（综合验证）
   - 新增辅助函数 `switchToFieldByName` 和 `getCurrentFieldName`，通过 `window.App` 直接切换字段并验证

#### 测试结果

- 35 项浏览器单元测试：全部通过 ✅
- 48 项 Playwright 端到端测试（原 36 + 新增 12）：全部通过 ✅
- GLM Schema 校验：全部通过 ✅
- 后端 519 项回归测试：全部通过 ✅
- 1366×768 下 5 个金额值截图：`tests/1366x768_5values.png` ✅

### 不变项

- JSON Schema（未修改 `backend/schemas.py`）
- UTF-16 偏移逻辑（未修改 `validate_schema.py` / `common.py`）
- 未修改 `backend/`
- 未合并 `main` / `develop`

### 证据质量控制（证据不完整/高亮错位修复）

#### 背景
针对人工标注中"证据不完整或高亮错位"问题，新增证据保存前的预览与质量提示，支持多段证据、点击定位与失效检测，并补充真实鼠标操作测试。

#### 修改（app.js / index.html / style.css / tests/e2e.spec.js）

1. **保存证据前完整预览弹窗（#evidencePreviewModal）**
   - 显示选中文字、前后各 20 字符上下文（CONTEXT_RADIUS=20）
   - 显示 start / end / 长度（readonly）
   - 显示 `rawText.slice(start, end)` 与选中文字的一致性验证结果
   - 显示非阻塞质量提示（仅提示不阻止保存）

2. **向左/向右扩展1字 + 重新选择按钮**
   - `expandEvidenceLeft()` / `expandEvidenceRight()`：每次扩展 1 个字符，方便补齐标签、标点和单位（如"万元"、"采购人"）
   - `reselectEvidence()`：关闭预览弹窗且不保存，等待用户重新选中文本

3. **金额/企业/日期证据非阻塞质量提示**
   - `EVIDENCE_QUALITY_KEYWORDS`：金额（预算/限价/中标/成交/合同/单价等）、企业（采购人/中标人/供应商/代理机构等）、日期（发布/截止/开标等）三类词表
   - `inferEvidenceCategory(fieldName)`：字段名 → 质量检查类别映射
   - `checkEvidenceQuality(fieldName, start, end)`：在"选中文字 + 前后 20 字符"窗口内检查是否含类型词，缺失时显示提示
   - **只提示，不阻止保存，不强制固定格式**

4. **多段证据支持**
   - 证据角色选择：primary（主证据）/ qualifier（角色、金额类型、分包等限定证据）/ context / derivation_input / contradiction
   - 一个字段的一个值可保存多段证据

5. **点击已保存证据滚动 + 高亮 + 失效检测**
   - `focusEvidenceInText(evIndex, fieldIndex, valueIndex)`：点击证据项 → `scrollIntoView({behavior:'smooth', block:'center'})` + 1.2s flash 闪烁动画
   - 通过 `Schema.verifyEvidenceSpan(state.rawText, start, end, text)` 检查 slice 一致性
   - **失效时**：证据项添加 `.evidence-invalid` 红色标记，弹窗提示"证据已失效"，**不展示错误高亮**
   - 通过 `textContent` 匹配高亮 span（避免高亮被拆分后偏移变化）

6. **新增 10 项真实鼠标操作 Playwright 测试（场景 27-36）**
   - 27. 金额标签和数值完整选择（真实鼠标操作）
   - 28. 公司角色和名称完整选择（真实鼠标操作）
   - 29. 跨行选择证据（真实鼠标操作）
   - 30. 重复文本选择第二处（真实鼠标操作）
   - 31. 高亮后再次选择第二段证据（真实鼠标操作）
   - 32. 导出再导入后仍逐字一致
   - 33. 向左/向右扩展1字按钮正常工作
   - 34. 重新选择按钮关闭预览弹窗且不保存
   - 35. 点击已保存证据滚动并高亮原文
   - 36. 证据失效检测（slice 不一致时显示"证据已失效"）
   - 新增 `selectTextViaDomRange` 辅助函数：通过 DOM Range + TreeWalker 精确选中文本并触发 mouseup

#### 测试结果

- 35 项浏览器单元测试：全部通过 ✅
- 36 项 Playwright 端到端测试（原 26 + 新增 10）：全部通过 ✅
- GLM Schema 校验：7 条证据偏移量全部通过 ✅
- 后端 519 项回归测试：全部通过 ✅

### 不变项

- JSON Schema（未修改 `backend/schemas.py`）
- UTF-16 偏移逻辑（未修改 `validate_schema.py` / `common.py`）
- 未修改 `backend/`
- 未合并 `main` / `develop`

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
