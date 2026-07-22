# BidAgent 金标标注工具

## 概述
纯前端网页标注工具，用于 BidAgent 项目金标数据的人工标注。严格对接 GLM 后端 `backend/schemas.py` 中的 `AnnotationDocument` Schema。

## 文件清单（共 8 个文件）

| 文件 | 说明 |
|------|------|
| `index.html` | 主页面结构 |
| `style.css` | 样式表 |
| `schema.js` | Schema 常量定义（与 `backend/enums.py` 对齐） |
| `sample_data.js` | Mock 示例数据（人工构造，非真实公告） |
| `app.js` | 核心业务逻辑 |
| `test.html` | 自动化测试页面（35+ 测试用例） |
| `tests/` | Playwright 端到端测试目录（12 项交互场景） |
| `validate_schema.py` | Pydantic Schema 校验脚本（强制使用 GLM 真实 Schema，无本地 fallback） |
| `README.md` | 使用说明文档 |

## 功能特性

### ✅ 已实现

1. **双栏布局**
   - 左侧：`clean_raw_text` 原文展示区，等宽字体
   - 右侧：六类核心字段标注面板（六字段紧凑导航 + 单字段编辑器，一次只显示一个字段的完整编辑器）

2. **六类正式核心字段**（与 v4.1 一致）
   - `project_identifier` — 项目编号
   - `purchaser_name` — 采购人名称
   - `winner_name` — 中标人名称
   - `amount` — 金额及金额类型
   - `publish_date` — 发布日期
   - `bid_deadline` — 投标截止日期

3. **六种字段状态**
   - `present` — 存在（绿色）
   - `absent` — 不存在（红色）
   - `not_applicable` — 不适用（灰色）
   - `ambiguous` — 歧义（橙色）
   - `attachment_only` — 仅附件（蓝色）
   - `unreadable` — 无法识别（紫色）

4. **多值字段支持**
   - 每个字段可添加多个值
   - 每个值独立管理证据列表

5. **证据标注（偏移量 100% 准确）**
   - 鼠标选中文本后点击「添加证据」
   - 自动记录 `[start, end)` 半开区间偏移量
   - **基于 DOM Range 的精确定位**：使用 `TreeWalker` 遍历原文容器文本节点，累计 UTF-16 code unit 长度，根据 `Range.startContainer/startOffset` 与 `endContainer/endOffset` 计算绝对偏移
   - **不使用 indexOf 兜底**：同一文本多次出现时也能准确定位到鼠标实际选中的位置；高亮 `<span>` 拆分文本节点后仍能正确计算
   - **强制验证**：保存前必须满足 `rawText.slice(start, end) === evidence_text`，无法唯一、准确定位时禁止保存
   - 覆盖测试：ASCII、中文、换行、全角字符、emoji、特殊空白、相同文本多次出现、跨文本节点选择
   - 支持多段合法证据
   - 五种证据角色：`primary` / `context` / `qualifier` / `derivation_input` / `contradiction`

6. **金额字段专属属性**
   - `amount_type`：budget / ceiling / award / contract / unit_price / unknown
   - `currency`：币种
   - `original_unit`：原始单位（万元、亿元等）
   - `tax_status`：含税状态

7. **其他属性**
   - `lot_id`：分包 ID
   - `note`：字段备注

8. **数据导入导出**
   - 导入 TXT 原文（最大 5 MB，可配置）
   - 导入/导出 JSON（符合 `AnnotationDocument` Schema，extra="forbid" 严格模式）
   - 导出前执行完整前端校验：六类字段齐全、字段名不重复、present 至少一个 value、每个 value 至少一个 primary 证据、非 present 状态 values 为空、枚举合法、`start < end`、`rawText.slice(start, end) === evidence.text`；失败时禁止导出、显示具体字段和错误、自动切换到首个错误字段
   - 导入 JSON 后、写入 state 前执行完整校验；损坏 JSON / 缺失字段 / 非法枚举 / 非 present 残留 values / 偏移不匹配 / `annotation_version` 不兼容均明确报错，**校验失败不覆盖当前草稿**
   - localStorage 自动保存草稿（500ms 防抖）

9. **localStorage 按文档隔离**
   - 草稿按 `document_id` 隔离存储：`bidagent_annotation_draft_${document_id}`
   - 维护轻量文档索引（document_id / title / updated_at / annotation_status）
   - 切换文档前自动保存当前草稿；文档 B 不会恢复文档 A 的草稿
   - 重置只清除当前文档，不影响其他文档
   - **数据仅保存在当前浏览器本机**，不上传任何服务器

10. **证据高亮回显与重叠检测**
    - 已标注的证据片段在原文中按角色着色高亮（primary/ context/ qualifier/ derivation_input/ contradiction）
    - 高亮 `<span>` 拆分文本节点后，DOM Range 仍能正确计算后续选区偏移
    - 检测到证据区间重叠时在原文上方显示警告，不静默跳过

11. **XSS 防护**
    - 公告文本、导入 JSON 内容、验证错误文本均通过 `textContent` / `document.createElement` 渲染
    - 不直接拼入 `innerHTML`；确需 `innerHTML` 时完整转义
    - 用户控制文本（如证据原文）优先禁止进入 `innerHTML`

12. **公告类型与标注状态本地元数据**
    - `noticeType` 与 `annotationStatus` 与 state 同步，保存到独立本地元数据对象 `bidagent_annotation_meta_${document_id}`
    - **不混入 `extra="forbid"` 的导出 JSON**，避免破坏 `AnnotationDocument` Schema 校验

13. **文档级标注状态**
    - 待标 / 已标 / 待仲裁

### 🔒 安全约束

- 纯前端实现，不上传任何数据到服务器
- 不加载远程脚本，所有代码本地运行
- 不读取 `.env` 或 API Key
- 示例数据均为人工构造的 Mock 内容
- 不接触冻结测试集

## 使用说明

### 启动方法

推荐使用本地 HTTP 服务器启动：

```bash
# 进入 annotation_tool 目录
cd annotation_tool

# 启动 Python 内置 HTTP 服务器
python -m http.server 8000 --directory .
```

然后在浏览器中访问：**http://localhost:8000/**

也可以直接双击 `index.html` 用浏览器打开（部分浏览器 localStorage 可能受限）。

### 快速开始
1. 打开页面后默认加载示例数据，可直接体验
2. 点击「导入 TXT」可加载自己的公告 clean_raw_text
3. 在左侧原文中用鼠标选中一段文字
4. 在右侧对应字段中点击「+ 添加选中的文本为证据」
5. 完成后点击「导出 JSON」保存标注结果

### 标注流程
1. **设置文档信息**：填写 document_id、标注员 ID
2. **逐字段标注**：
   - 选择字段状态（present / absent / ...）
   - 若为 present，添加字段值
   - 在原文中选中证据文本，添加到对应值
   - 至少需要一个 `primary` 角色的证据
3. **填写备注**：对不确定的字段添加说明
4. **导出 JSON**：保存标注结果

### 偏移量约定
- 坐标空间：`clean_raw_text`
- 区间格式：`[start, end)` 半开区间（含 start，不含 end）
- 验证规则：`clean_raw_text.slice(start, end) === evidence.text`

## 输出 JSON 格式

严格符合 `backend/schemas.py` 中的 `AnnotationDocument` Schema：

```json
{
  "document_id": "sample-001",
  "annotator_id": "A",
  "annotation_version": "1.0",
  "annotation_time": "2024-03-16T10:30:00.000Z",
  "fields": [
    {
      "field_name": "project_identifier",
      "gold_status": "present",
      "values": [
        {
          "raw_value": "ZFCG-2024-0315",
          "normalized_value": "ZFCG-2024-0315",
          "amount_type": null,
          "currency": null,
          "original_unit": null,
          "tax_status": null,
          "lot_id": null,
          "acceptable_evidence_spans": [
            {
              "role": "primary",
              "start": 32,
              "end": 47,
              "text": "ZFCG-2024-0315"
            }
          ]
        }
      ],
      "note": ""
    }
  ]
}
```

## 与 GLM 后端对齐情况

| 枚举/常量 | GLM 后端 | 标注工具 | 状态 |
|----------|---------|---------|------|
| CoreFieldName（6类） | ✅ | ✅ | 完全对齐 |
| GoldStatus（6种） | ✅ | ✅ | 完全对齐 |
| EvidenceRole（5种） | ✅ | ✅ | 完全对齐 |
| AmountType（6种） | ✅ | ✅ | 完全对齐 |
| TaxStatus（3种） | ✅ | ✅ | 完全对齐 |
| AnnotationDocument Schema | ✅ | ✅ | 导出格式一致 |
| 偏移量 [start, end) | ✅ | ✅ | 约定一致 |
| 坐标空间 clean_raw_text | ✅ | ✅ | 约定一致 |

## 已知限制

1. **无后端集成**：纯前端工具，数据仅保存在浏览器 localStorage 和导出的 JSON 文件中
2. **无双人标注对比**：不支持标注一致性检查（IAA），需后续评测脚本处理
3. **无项目级分组**：当前只处理单公告级别，不涉及项目级去重和关联

## 自动化测试

### 浏览器端测试（test.html）

覆盖 30+ 测试用例，包含：
- ✅ 六类核心字段完整性
- ✅ 六种字段状态枚举
- ✅ 五种证据角色枚举
- ✅ 偏移量验证（ASCII / 中文 / 换行 / 全角 / emoji / 特殊空白）
- ✅ 单值和多值字段
- ✅ 多段证据多角色
- ✅ JSON 导入导出一致性
- ✅ localStorage 保存和恢复
- ✅ present 状态至少一个 value
- ✅ 非 present 状态不得残留 values
- ✅ primary 证据要求
- ✅ 金额字段专属属性
- ✅ lot_id / note 字段

**运行方法**：启动 HTTP 服务器后访问 `http://localhost:8000/test.html`

### Pydantic Schema 校验（validate_schema.py）

使用 **GLM 后端真实 `AnnotationDocument` 模型**（`backend/schemas.py`）对导出 JSON 进行结构校验。

**重要**：脚本已移除本地 Pydantic Schema 副本 fallback。导入真实后端 Schema 失败时直接 `sys.exit(1)`，不得用副本产生假通过。

**运行方法**（必须在仓库根目录运行，以便导入 `backend.schemas`）：
```bash
python annotation_tool/validate_schema.py
```

校验内容：
- 字段枚举值合法性
- extra="forbid" 禁止额外字段
- present 状态 values 非空
- 非 present 状态 values 为空
- 证据列表至少一个 primary
- 偏移量结构合法（end > start）
- 字段名不重复
- UTF-16 code unit 切片验证：`utf16_slice(raw_text, start, end) == evidence.text`
- 原文 SHA256 哈希匹配

### Playwright 端到端测试（tests/）

覆盖真实 DOM 交互的 26 项场景：

1. present 无 value，禁止导出
2. present value 无 primary，禁止导出
3. 非 present 残留 values，禁止导出
4. 合法数据可以导出
5. 非法 JSON 导入不覆盖当前状态
6. 不同 document_id 草稿隔离
7. XSS 文本 `<img src=x onerror=alert(1)>` 不执行
8. 重复文本选择第二处时偏移正确
9. 高亮后继续选择第二段证据
10. noticeType 和 annotationStatus 保存恢复
11. 导出后重新导入数据一致
12. fixture 不被 generate.py 覆盖
13-17. 布局验收（六字段导航区/单字段编辑器/导航切换/值不截断/公告类型推断）
18-26. **跨文档隔离（P0 修复验证）**：
  18. 导入 TXT B 后字段和值全部为空
  19. B 的 document_id 与 A 不同
  20. B 不显示 A 的证据高亮
  21. 重新导入 A 时可恢复 A 自己的草稿
  22. 相同 TXT 再次导入时不创建随机新文档
  23. 导入 B 后刷新只恢复 B，不恢复 A
  24. 新 TXT 导入后完成度不得沿用上一篇
  25. 切换文件前的保存提示正常
  26. 导入空 TXT 或超大 TXT 时明确报错且不覆盖当前文档

**运行方法**：
```bash
cd annotation_tool/tests
npm install
npx playwright test
```

### TXT 导入与跨文档隔离说明

为避免跨公告数据污染（P0），TXT 导入采用以下规则：

- **独立 document_id**：规范化文件名 + 内容 SHA-256 前 12 位，相同文件再次导入识别为同一文档
- **保存提示**：导入前若当前文档有未保存修改，弹窗询问"是否保存当前草稿后再导入新公告？"
- **完整重置**：六类字段及 values、证据、高亮、当前字段索引、标注状态、公告类型、备注、校验错误、重叠提示
- **空状态初始化**：六类字段初始化为 `gold_status=''`（待判断），进度显示 0/6，与 `absent`（不存在，计入完成）区分
- **localStorage 隔离**：草稿键 `bidagent_annotation_draft_${document_id}`，元数据键 `bidagent_annotation_meta_${document_id}`
- **JSON 与 TXT 行为区分**：TXT 创建或恢复文档；JSON 加载完整标注，校验失败不覆盖当前状态
- **页面状态栏**：显示当前文件名、document_id、内容哈希、是否恢复草稿

## 测试结果

### 功能测试清单

- [x] 页面加载正常，双栏布局正确
- [x] 示例数据正确加载，六字段导航 + 单字段编辑器展示正常
- [x] 点击导航项切换当前字段，激活状态高亮正确
- [x] 未选字段只显示名称、状态、值数量、完成状态概要
- [x] 值项完整显示原始值、归一化值、证据列表，不被 overflow 截断
- [x] 示例公告类型默认为 `award`（原文为中标结果公告，根据原文推断）
- [x] noticeType / annotationStatus 保存到独立元数据，不混入导出 JSON
- [x] 字段状态切换功能正常（6种状态）
- [x] present 状态下可添加/删除多个值
- [x] 鼠标选中文本可获取偏移量
- [x] 添加证据功能正常
- [x] 证据编辑弹窗可修改角色和偏移量
- [x] 偏移量实时验证功能正常
- [x] 金额类型等扩展字段可编辑
- [x] 备注字段可编辑
- [x] JSON 导出格式正确
- [x] JSON 导入功能正常
- [x] TXT 原文导入功能正常
- [x] localStorage 自动保存和恢复正常
- [x] 重置功能正常

## 开发基线与分支信息

### GLM 后端基线
- **GLM 最新分支**：`origin/feature/glm-w1-review`
- **GLM 最新提交**：`6b39e5d`（security: 移除报告中 Key 片段显示）
- **Schema 来源**：只读参考 `backend/schemas.py` 和 `backend/enums.py`
- **未修改 GLM 代码**：仅新增 `annotation_tool/` 目录，不触碰 backend 任何文件

### 当前分支
- **分支名**：`feature/doubao-W1-05-annotation-tool`
- **Git 状态说明**：本地仓库因初始浅克隆导致无初始提交，所有文件均为 Added 状态；代码内容基于 GLM 6b39e5d
- **修改目录**：仅 `annotation_tool/`
- **未修改**：`backend/`、`app/`、`tests/`、`models/`、`schemas/`、`migrations/`、`evaluation/`、`extractors/`

## 后续可扩展方向

1. 双人标注一致性检查
2. 标注进度统计和质量看板
3. 键盘快捷键支持
4. 与后端 API 对接，直接入库
