# S-2 任务清单：废标风险预警引擎（risk_engine.py）

> **目标**：吸收完整版 `risk_engine.py`，修复 6 个 bug，接入 `finance_agent.py`
> **预估**：15+ 测试，当前 267 → 预期 282+ passed
> **交付方式**：同 S-1，Markdown 格式（代码用 ```python 代码块）

---

## 一、S-1 状态确认

✅ **S-1 已完成并接入**：
- `app/processors/boq_engine.py`（280 行，25 测试全过）
- `app/processors/__init__.py` 已导出 `analyze_boq` / `BOQReport`
- `app/agents/finance_agent.py` 的 `_run_boq_analysis` 已接入真实引擎（不再降级）
- 全量测试：**267 passed, 0 failures**

---

## 二、S-2 交付文件清单

| 文件 | 操作 | 说明 |
|---|---|---|
| `app/processors/risk_engine.py` | 新建 | 废标风险预警引擎（修复 6 个 bug） |
| `app/processors/__init__.py` | 修改 | 增加 `analyze_risk` / `RiskReport` 导出 |
| `tests/test_risk_engine.py` | 新建 | 15+ 测试 |
| `app/agents/finance_agent.py` | 不需要修改 | 现有动态导入会自动调用真实的 `analyze_risk` |

---

## 三、完整版 risk_engine.py 源码（待修复）

> **路径**：`scrapeflow_完整版_20260720/scrapeflow-complete/backend/app/processors/risk_engine.py`
> **行数**：166 行

```python
"""AI废标基因逆向推演引擎 v2 — DeepSeek增强版

DeepSeek V4 Pro 贡献的联合规则：
1. 资格条件与本地业绩叠加 → 废标高发组合
2. 技术参数全部★且无例外 → 疑似量身定做
3. 保证金仅限现金且提前提交 → 投标门槛过高
4. 联合体与分包条款冲突 → 招标文件自相矛盾
5. 否决条款密集且无澄清通道 → 缺乏公平性
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from app.utils.logger import get_logger

logger = get_logger("risk_engine_v2")


@dataclass
class RiskReport:
    tender_id: int | None = None
    project_name: str = ""
    risk_score: float = 0.0
    summary: str = ""
    risk_items: list[dict] = field(default_factory=list)
    qualification_gaps: list[str] = field(default_factory=list)
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": "v2",
            "tender_id": self.tender_id,
            "project_name": self.project_name,
            "risk_score": round(self.risk_score, 1),
            "risk_level": self._level(),
            "summary": self.summary,
            "risk_items": self.risk_items[:10],
            "qualification_gaps": self.qualification_gaps,
            "created_at": self.created_at or time.strftime("%Y-%m-%d %H:%M:%S"),
            "engine": "deepseek_v4_enhanced",
        }

    def _level(self) -> str:
        if self.risk_score >= 60: return "高风险"
        if self.risk_score >= 30: return "中风险"
        return "低风险"


# ====== DeepSeek V4 Pro 优化规则 ======

_RULES = [
    # (关键词, 类型, 描述, 建议, 分数, 引用法规)

    # --- 排他性规则 ---
    (["必须具备"], "exclusive", "排他性资质要求，限制竞争", "核查该条款是否符合《招标投标法》公平竞争要求", 20, "《招标投标法实施条例》第三十二条"),
    (["必须拥有"], "exclusive", "排他性资质要求", "核查法律法规依据", 20, ""),
    (["独有"], "exclusive", "独家授权条款，可能针对特定供应商", "如无法律依据可提出质疑", 25, "《招标投标法》第二十条"),
    (["唯一授权"], "exclusive", "唯一授权要求", "需确认是否属于合法排他", 25, ""),
    (["原厂"], "exclusive", "原厂授权要求，可能限制竞争", "要求提供至少三家品牌竞争", 20, "《招标投标法实施条例》第三十二条"),
    (["指定品牌"], "exclusive", "指定品牌要求，涉嫌量身定做", "建议要求公开品牌选择标准", 20, ""),
    (["注册资金不低于", "注册资本不低于"], "exclusive", "注册资本门槛限制中小企业参与", "除非法律有明确规定，否则涉嫌歧视", 15, "《政府采购促进中小企业发展管理办法》"),

    # --- 付款/保证金风险 ---
    (["付款周期", "付款期限"], "payment", "需核实付款周期是否超过60天", "长期付款影响现金流，请评估", 15, ""),
    (["履约保证金"], "payment", "需确认保证金比例是否超过10%", "超过10%可依法要求降低", 12, "《招标投标法实施条例》第五十八条"),
    (["现金"], "payment", "仅接受现金保证金，增加投标人负担", "建议要求接受银行保函", 10, ""),

    # --- 交货期风险 ---
    (["交货期", "交付期", "供货期"], "deadline", "交货期过短可能导致履约困难", "确认是否有足够生产/备货时间", 12, ""),

    # --- 资质门槛 ---
    (["资质", "许可证"], "qualification", "需确认企业是否具备相关资质", "提前准备资质证明材料", 8, ""),
    (["ISO"], "qualification", "ISO体系认证要求，需确认持有情况", "提前准备认证证书", 8, ""),
    (["CMMI"], "qualification", "CMMI认证要求", "确认CMMI等级要求", 8, ""),
    (["安全生产"], "qualification", "安全生产许可要求", "确认安全生产许可证有效期内", 8, "《安全生产法》"),

    # ===== DeepSeek V4 Pro 联合规则 =====
    (["本地", "本市", "本省"], "exclusive", "本地化业绩要求+资格条件叠加，废标高发", "组合条款可能排斥外地企业，建议评估", 18, "《招标投标法》第六条"),
    (["★", "*"], "exclusive", "全部技术参数标注星号且无偏差条款", "可能针对特定品牌参数设计", 20, "《招标投标法实施条例》第三十二条"),
    (["联合体", "分包"], "other", "联合体投标与分包条款矛盾", "文件允许联合体但禁止分包构成逻辑冲突", 15, ""),
    (["否决"], "other", "否决条款密集但未提及书面澄清渠道", "多项否决权缺乏救济渠道", 12, "《招标投标法》第四十四条"),
]


def _analyze(text: str, project_name: str, qualification: str | None) -> RiskReport:
    """核心分析逻辑"""
    report = RiskReport(project_name=project_name)
    report.created_at = time.strftime("%Y-%m-%d %H:%M:%S")
    content = f"{project_name} {text} {qualification or ''}"
    items = []
    score = 0.0
    seen = set()

    for keywords, rtype, desc, suggestion, points, law in _RULES:
        # 组合规则（多个关键词同时出现才算命中）
        if len(keywords) > 1 and any(kw in keywords[1:] for kw in ["*", "★"]):
            # ★ 规则：检测所有参数+星号
            if any(kw in content for kw in keywords):
                match = True
            else:
                match = False
        elif len(keywords) > 1:
            # 多词组合：必须全部出现
            match = all(kw in content for kw in keywords)
        else:
            match = keywords[0] in content

        if not match:
            continue

        clause = keywords[0]
        if clause in seen:
            continue
        seen.add(clause)

        items.append({
            "clause": clause,
            "risk_level": "high" if points >= 15 else "medium",
            "risk_type": rtype,
            "description": desc,
            "suggestion": suggestion,
            "law_ref": law,
        })
        score += points

    # 资质缺口分析
    gaps = []
    if qualification:
        if "资质" not in content and "许可证" not in content:
            gaps.append("未明确列出所需资质清单，需向招标方确认")
        if "ISO" in content:
            gaps.append("ISO认证（需确认覆盖范围）")
        if "CMMI" in content or "系统集成" in content:
            gaps.append("CMMI或系统集成资质（需确认等级要求）")

    report.risk_score = min(score, 100)
    report.risk_items = items[:10]
    report.qualification_gaps = gaps

    # 智能摘要
    high = sum(1 for i in items if i["risk_level"] == "high")
    medium = sum(1 for i in items if i["risk_level"] == "medium")
    excl = sum(1 for i in items if i["risk_type"] == "exclusive")
    parts = []
    if high > 0: parts.append(f"{high}项高风险")
    if excl > 0: parts.append(f"{excl}项可能涉及排他")
    if medium > 0: parts.append(f"{medium}项中风险")
    if gaps: parts.append(f"{len(gaps)}个资质缺口")
    report.summary = "，".join(parts) + "。" if parts else "未检测到明显风险。"

    return report


async def analyze_risk_engine(
    project_name: str,
    content: str,
    qualification: str | None = None,
    tender_id: int | None = None,
) -> dict[str, Any]:
    """对外接口"""
    report = _analyze(project_name, content, qualification)
    report.tender_id = tender_id
    return report.to_dict()
```

---

## 四、S-2 必须修复的 6 个 Bug

### Bug 1：`_analyze` 参数顺序错误（严重）

**现状**：
```python
def _analyze(text: str, project_name: str, qualification: str | None):
    ...
    content = f"{project_name} {text} {qualification or ''}"

# 调用处：
report = _analyze(project_name, content, qualification)
```

**问题**：定义是 `(text, project_name, qualification)`，调用是 `(project_name, content, qualification)` — 参数错位！`text` 收到的是 `project_name`，`project_name` 收到的是 `content`。

**修复**：统一参数顺序为 `(project_name, content, qualification)`，或改为关键字参数调用。

### Bug 2：星号规则判断逻辑隐晦且错误

**现状**（第 99 行）：
```python
if len(keywords) > 1 and any(kw in keywords[1:] for kw in ["*", "★"]):
    if any(kw in content for kw in keywords):
        match = True
```

**问题**：
- `any(kw in keywords[1:] for kw in ["*", "★"])` 检查的是 keywords 列表本身是否包含星号，而不是检查 content
- 星号规则 `(["★", "*"])` 走这个分支，用 `any` 命中 — 但语义应该是"文本中有星号就命中"，`any` 在这里碰巧对了
- 但这种"检查 keywords 列表内容来决定分支"的写法非常隐晦，且和规则数据的结构耦合

**修复**：改为规则数据加 `mode` 字段（`"any"` / `"all"` / `"star"`），或把星号规则单独处理，不混在通用逻辑里。

### Bug 3：多词组合规则语义错误

**现状**（第 105-107 行）：
```python
elif len(keywords) > 1:
    match = all(kw in content for kw in keywords)
```

**问题**：有些多词规则应该是 OR 关系（任一出现即命中），不是 AND 关系。例如：
- `["注册资金不低于", "注册资本不低于"]` → 应该是 OR（任一出现即命中）
- `["付款周期", "付款期限"]` → 应该是 OR
- `["交货期", "交付期", "供货期"]` → 应该是 OR
- `["本地", "本市", "本省"]` → 应该是 OR
- `["联合体", "分包"]` → 应该是 AND（同时出现才构成冲突）
- `["资质", "许可证"]` → 应该是 OR

**修复**：规则数据增加 `mode` 字段：`"any"`（OR）/ `"all"`（AND）/ `"star"`（星号检测）。默认 `"any"`。

### Bug 4：`seen` 去重逻辑错误

**现状**（第 114-117 行）：
```python
clause = keywords[0]
if clause in seen:
    continue
seen.add(clause)
```

**问题**：
- 用 `keywords[0]` 作为去重键，但不同规则可能第一个关键词相同
- 对于 OR 规则 `["注册资金不低于", "注册资本不低于"]`，如果文本同时包含两个，应该只命中一次 — 当前逻辑能做到（因为同一条规则只匹配一次）
- 但跨规则去重有问题：如果两条不同规则的第一关键词相同，第二条被错误跳过

**修复**：改为 `(rule_index, matched_keyword)` 元组作为去重键，或直接用规则索引去重。

### Bug 5：函数名与 finance_agent.py 不一致

**现状**：
- 完整版导出：`analyze_risk_engine`
- `finance_agent.py` 调用：`analyze_risk`

**修复**：导出为 `analyze_risk`（与 boq_engine 的 `analyze_boq` 命名风格一致）。保留 `analyze_risk_engine` 作为别名兼容。

### Bug 6：同步工作未卸载到 executor（违反硬性规则）

**现状**：
```python
async def analyze_risk_engine(...):
    report = _analyze(...)  # 同步 CPU 工作
    return report.to_dict()
```

**问题**：违反硬性规则"异步函数必须用 `run_in_executor` 卸载同步 CPU/IO 任务"。

**修复**：
```python
async def analyze_risk(...):
    loop = asyncio.get_running_loop()
    task = partial(_analyze, project_name, content, qualification)
    report = await loop.run_in_executor(None, task)
    report.tender_id = tender_id
    return report.to_dict()
```

---

## 五、S-2 测试要求（15+ 测试）

### 测试文件：`tests/test_risk_engine.py`

**测试覆盖点**：

| # | 测试名 | 验证点 |
|---|---|---|
| 1 | `test_empty_text` | 空文本 → score=0, summary="未检测到明显风险。" |
| 2 | `test_exclusive_must_have` | "必须具备XXX资质" → 命中排他性规则, score=20 |
| 3 | `test_exclusive_original_factory` | "原厂授权" → 命中排他性规则, score=20 |
| 4 | `test_payment_risk` | "履约保证金" → 命中付款风险, score=12 |
| 5 | `test_deadline_risk` | "交货期30天" → 命中交货期风险, score=12 |
| 6 | `test_qualification_iso` | "ISO9001认证" → 命中资质门槛, score=8 |
| 7 | `test_qualification_cmmi` | "CMMI3级" → 命中资质门槛, score=8 |
| 8 | `test_combined_rules_all_mode` | "联合体投标"+"禁止分包" → 命中冲突规则(AND), score=15 |
| 9 | `test_or_rule_payment_period` | "付款周期" 或 "付款期限" → 均命中(OR), score=15 |
| 10 | `test_or_rule_deadline` | "交货期"/"交付期"/"供货期" → 均命中(OR), score=12 |
| 11 | `test_star_rule` | "技术参数★" → 命中星号规则, score=20 |
| 12 | `test_risk_level_high` | score >= 60 → "高风险" |
| 13 | `test_risk_level_medium` | 30 <= score < 60 → "中风险" |
| 14 | `test_risk_level_low` | score < 30 → "低风险" |
| 15 | `test_score_capped_at_100` | 多规则命中 → score 不超过 100 |
| 16 | `test_qualification_gaps` | 有 ISO 但无资质清单 → gaps 非空 |
| 17 | `test_no_qualification_gaps_when_complete` | 有资质+许可证 → gaps 为空 |
| 18 | `test_dedup_same_rule_not_double_counted` | 同一规则不重复计分 |
| 19 | `test_summary_content` | summary 包含"高风险"/"排他"等关键词 |
| 20 | `test_analyze_risk_async_public_api` | 异步接口返回 dict, version="v2", engine="deepseek_v4_enhanced" |

**测试要求**：
- 用 `pytest` + `pytest-asyncio`
- 不依赖真实网络
- 每个测试有 docstring

---

## 六、S-2 应用顺序

```bash
# 1. 新建 app/processors/risk_engine.py
# 2. 修改 app/processors/__init__.py（增加 analyze_risk / RiskReport 导出）
# 3. 新建 tests/test_risk_engine.py

# 4. 跑 S-2 测试
pytest tests/test_risk_engine.py -v

# 5. 跑 Agent 集成测试（验证 finance_agent 的 _run_risk_analysis 不再降级）
pytest tests/test_agents.py -v

# 6. 全量回归
pytest -v
```

**预期结果**：267 + 20 = **287 passed**

---

## 七、S-2 关键设计决策

1. **参数顺序统一**：`(project_name, content, qualification)` — project_name 在前，与 `analyze_boq(text, project_name)` 风格一致
2. **规则 mode 字段**：`"any"` / `"all"` / `"star"` 三种模式，默认 `"any"`
3. **去重改为规则索引**：`seen = set()` 存规则索引而非关键词
4. **异步卸载**：`analyze_risk` 用 `run_in_executor` 包装 `_analyze`
5. **函数名**：主导出 `analyze_risk`，别名 `analyze_risk_engine = analyze_risk`
6. **不修改 finance_agent.py**：现有动态导入 `from app.processors.risk_engine import analyze_risk` 会自动成功

---

## 八、当前项目状态

- **测试基线**：267 passed（S-1 已应用）
- **代码文件**：
  - `app/processors/boq_engine.py` ✅
  - `app/processors/__init__.py` ✅（已导出 BOQReport / analyze_boq）
  - `app/agents/finance_agent.py` ✅（_run_boq_analysis 已接入，_run_risk_analysis 待 S-2）
- **设计文档**：`docs/superpowers/specs/2026-07-20-bidagent-goai-design.md` 第 2.2 节 5.2

---

## 九、S-2 之后的任务预告

| 任务 | 内容 | 预估测试 |
|---|---|---|
| **S-3** | supplier_risk.py 重构（去联邦学习，改为采购历史评分） | 15+ |
| **S-4** | sources.py + 7 个新模板（30+ 数据源注册） | 20+ |
| **S-5** | 聊天 UI（`app/api/chat.py` + `chat.html`） + `GET /chat/api/{session_id}` 进度轮询 | 10+ |

**S-3 交付后**：`finance_agent.py` 三子模块全部接入，金融分析 Agent 完整可用。
**S-4 交付后**：采集 Agent 支持 30+ 数据源。
**S-5 交付后**：Demo 视频可录制。
