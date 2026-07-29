# Turbo-W3-07 display_grade 字段接入 任务提示词

## 任务背景

按 v4.1 第八章"展示等级"要求，需基于支持度、来源质量、交叉验证状态将字段分为 high/review/low 三级，用于选择性输出策略（v4.1 10.7 节）。当前数据库 schema 缺 display_grade 字段，需迁移并接入计算逻辑。

## 输入

- 现有数据库模型：`app/models/tender.py`、`app/models/database.py`
- ExtractedField 模型：`app/models/tender.py` 中的 ExtractedField 类
- EvidenceLocation 支持度枚举：`app/processors/evidence_locator.py` 中的 SupportLevel

## 实现要求

### 1. 数据库 Schema 迁移

**新增字段**：ExtractedField 表添加 `display_grade` 字段

```python
# app/models/tender.py
class ExtractedField(Base):
    # ... 现有字段 ...
    display_grade = Column(String(16), nullable=False, default="review")  # high/review/low
```

**迁移脚本**：`scripts/migrate_add_display_grade.py`
- 检测字段是否已存在（幂等）
- ALTER TABLE 添加字段，默认值 "review"
- 验证迁移成功

### 2. display_grade 计算逻辑

**新文件**：`app/processors/display_grade.py`

```python
from app.processors.evidence_locator import SupportLevel

def compute_display_grade(
    support_level: SupportLevel,
    source_role: str,  # official_original/official_repost/commercial_repost/...
    cross_verified: bool,  # 是否被多源交叉验证
    field_status: str,  # present/absent/...
) -> str:
    """计算字段展示等级。

    判定规则（v4.1 第八章）:
    - high: support_level=STRONG + source_role=official_original + cross_verified=True
    - high: support_level=STRONG + source_role=official_original（无交叉验证也视为 high）
    - review: support_level=MEDIUM 或 source_role=official_repost
    - review: support_level=STRONG 但 source_role=commercial_repost
    - low: support_level=WEAK 或 source_role=unknown
    - low: field_status in (absent, ambiguous, unreadable)

    Returns:
        "high" | "review" | "low"
    """
```

### 3. 接入抽取流水线

在 `app/llm/extractor.py` 的 `call_extraction_llm` 返回前，对每个 field 计算 display_grade：

```python
# extractor.py 中 ExtractionResult 构造时
for field in result.fields:
    field.display_grade = compute_display_grade(
        support_level=field.support_level,
        source_role=source_role,  # 来自 source_lineage
        cross_verified=field.cross_verified,
        field_status=field.field_status,
    )
```

### 4. 选择性输出策略

**新文件**：`app/api/output_strategies.py`

```python
def filter_by_strategy(fields: list, strategy: str = "default") -> list:
    """按输出策略过滤字段。

    策略（v4.1 10.7 节）:
    - strict: 仅输出 high
    - default: 输出 high + 满足条件的 review
    - loose: 输出 high + 全部 review
    - audit: 输出所有字段（含 low）
    """
```

## 单元测试要求

`tests/test_display_grade.py`：
1. **compute_display_grade 测试**：
   - STRONG + official_original → high
   - MEDIUM + official_original → review
   - STRONG + commercial_repost → review
   - WEAK + any → low
   - field_status=absent → low
2. **filter_by_strategy 测试**：
   - strict 只返回 high
   - audit 返回全部
   - default 至少返回 high
3. **迁移脚本测试**：
   - 幂等性（跑两次不报错）
   - 空数据库迁移成功

## 验收标准

- [ ] `scripts/migrate_add_display_grade.py` 幂等执行成功
- [ ] `app/processors/display_grade.py` 实现
- [ ] `app/api/output_strategies.py` 实现
- [ ] ExtractedField 模型添加 display_grade 字段
- [ ] `tests/test_display_grade.py` 至少 8 个测试通过
- [ ] pytest --cov=app.processors.display_grade 覆盖率 ≥90%
- [ ] 不破坏现有测试（回归测试通过）

## 约束

1. **不修改 source_lineage.py**（已接入，只读取其输出）
2. **不修改 schema 已有字段**，只新增
3. **迁移脚本必须幂等**（约束：数据库迁移必须验证幂等性）
4. **分支**：feature/glm-w2-evidence，commit: `feat(W3-07): display_grade字段接入+选择性输出策略`
5. **不调用 LLM**

## 待查项

- cross_verified 字段当前是否存在于 ExtractedField？如不存在，需在迁移脚本中一并添加（默认 False）
- output_strategies 是否需要接入 API 路由？W3 阶段先实现纯函数，路由接入留到 W4
