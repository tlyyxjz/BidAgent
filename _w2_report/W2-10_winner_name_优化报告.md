# W2-10 winner_name 提示词优化报告（K3）

- 生成时间：2026-07-27
- 执行人：K3（独立任务，不修改 extractor.py）
- 模型：deepseek-v4-flash，temperature=0.1，max_tokens=8000，response_format=json_object
- 真实 LLM 调用：43 次（6 篇对比 12 次 + 联合体格式验证 1 次 + 稳定性重复 30 次）
- 结果原始文件：`winner_eval_results.json`、`winner_stability_results.json`（含逐次 tokens/latency/命中明细）
- Ground truth：22 篇金标 winner_name 字段（TASK 1 双标仲裁中 A/K3 两标注员 22/22 完全一致）

---

## 一、失败模式分析（基于现版 prompt 静态审查 + 实测）

现版 prompt（extractor.py L45-98）存在 4 个结构性缺陷：

| # | 缺陷 | 位置 | 风险 |
|---|------|------|------|
| F1 | few-shot 唯一示例中 winner_name=absent，无任何"有中标人"的正例 | EXTRACTION_FEWSHOT_EXAMPLES 仅 1 条招标公告示例 | 模型对多中标人输出格式（多条记录 or 合并字符串）无锚点，靠自由发挥 |
| F2 | "多中标人每个单独输出一条"只在输出要求第 4 条一笔带过，无反合并禁令、无数量核对指令 | system prompt L73-75 | 多中标人场景可能用"、"合并为一个 raw_value，或漏抽部分中标人 |
| F3 | 无"采购人/代理机构/交易平台不得作为中标人"的负例约束 | 全文缺失 | 与 TASK 1 仲裁发现的第一标注员"代理机构混入 purchaser"同类风险，LLM 同样可能混入 |
| F4 | 无联合体结构定义、无 lot_id 使用规范 | 全文缺失 | 联合体中标只能输出字符串，牵头人/成员关系丢失；多分包时中标人与包号对应关系丢失 |

**实测修正（必须如实记录）**：上述缺陷在**当前 22 篇金标的 4 篇多中标人样本上未实际触发**。43 次真实调用中，baseline 的 winner_name 值抽取 36/36 次满分（6 篇对比 6/6 + 稳定性 30/30），无漏抽、无合并、无多抽。任务背景所述"award_05 输出不稳定"在 deepseek-v4-flash @ temperature=0.1 下**未复现**。W2-09 观测到的运行间波动（multi_winner_03 IoU 0.5914→0.2905）发生在**证据边界**维度，不在 winner_name 字段值维度——两者不可混为一谈。

结论：现版 prompt 的多中标人缺陷是**鲁棒性隐患**（无约束、无正例锚点），不是**当前金标上的实测错误**。v2 的定位是**加固防回归 + 补齐联合体/多分包结构**，而非修复已发生的漏抽。

## 二、v2 prompt 设计思路

对应 F1-F4 逐项修复（完整文本见 `app/llm/prompts/winner_name_v2.txt`）：

1. **新增 R1-R6 专项规则**（置于 system prompt 最高优先级位置）：
   - R1 语境限定 + 负例排除（采购人/代理机构/交易中心不得为中标人）→ 修 F3
   - R2 单中标人输出 string → 明确单值形态
   - R3 多中标人输出独立记录数组 + 反合并禁令（严禁"、，和"合并）+ 先数后抽 + lot_id 规范 → 修 F2/F4
   - R4 联合体 `[{"main": ..., "partners": [...]}]` 结构 → 修 F4
   - R6 每条记录必配 primary 证据 + 概要/正文冲突时以正文为准 → 与证据流水线对齐
2. **few-shot 从 1 条扩到 3 条**：保留原 absent 示例（防回归），新增单中标人 present 示例、多中标人 multi_value 3 记录示例 → 修 F1
3. **不变更部分**：六字段定义、field_status 枚举、amount_type/currency、证据 role 体系全部沿用，保证与 extraction_schemas.py 兼容。

## 三、6 篇样本前后对比（真实调用）

| 样本 | 类型 | 金标中标人数 | baseline 命中 | v2 命中 | baseline tokens / 延迟 | v2 tokens / 延迟 |
|------|------|---|---|---|---|---|
| award_05 | 多中标人 | 5 | 5/5 ✅ exact | 5/5 ✅ exact | 7097 / 24857ms | 7889 / 22759ms |
| multi_winner_03 | 多中标人 | 3 | 3/3 ✅ exact | 3/3 ✅ exact | 6659 / 26826ms | 7648 / 29084ms |
| 06_award_003 | 多中标人 | 3 | 3/3 ✅ exact | 3/3 ✅ exact | 4591 / 13374ms | 6054 / 12222ms |
| 04_award_001 | 单中标人 | 1 | 1/1 ✅ exact | 1/1 ✅ exact | 3244 / 9685ms | 4506 / 7596ms |
| award_08 | 单中标人 | 1 | 1/1 ✅ exact | 1/1 ✅ exact | 2612 / 5657ms | 3974 / 5602ms |
| award_09 | 单中标人 | 1 | 1/1 ✅ exact | 1/1 ✅ exact | 2921 / 6351ms | 4306 / 6157ms |

- 命中率（字段值 recall）：baseline 100% → v2 100%，**提升 0 个百分点**（baseline 已饱和，无提升空间）
- 单中标人回归：无（3/3 两组均满分）
- 成本：v2 平均 tokens 5730 vs baseline 4521（**+26.7%**，因 system prompt 加长 + 2 条 few-shot）；平均延迟 13903ms vs 14458ms（-3.8%，误差范围内相当）

## 四、稳定性实验（3 篇多中标人 × 5 次重复）

| 样本 | prompt | 5 次 recall | exact_set | 合并违规 |
|------|--------|------------|-----------|---------|
| award_05 | baseline | 1.0×5 | 5/5 | 0 |
| award_05 | v2 | 1.0×5 | 5/5 | 0 |
| multi_winner_03 | baseline | 1.0×5 | 5/5 | 0 |
| multi_winner_03 | v2 | 1.0×5 | 5/5 | 0 |
| 06_award_003 | baseline | 1.0×5 | 5/5 | 0 |
| 06_award_003 | v2 | 1.0×5 | 5/5 | 0 |

两组均无波动。当前金标多中标人样本（3-5 家、格式规整的"第X包中标人：公司名"）对 deepseek-v4-flash 而言难度不足，无法区分新旧 prompt 的鲁棒性差异。

## 五、联合体场景验证

22 篇金标中**无真实联合体中标样本**（全部"联合体"字样均为招标公告"是否接受联合体投标"条款），用 1 篇构造样本做格式验证：

- v2 输出：`{"main": "甲建设集团有限公司", "partners": ["乙信息技术有限公司"]}` ✅ 正确区分牵头人与成员，符合 R4 结构
- 该验证仅证明格式可用，**不构成真实场景命中率证据**（待查 3）

## 六、验收标准逐项核对

| # | 标准 | 结果 | 说明 |
|---|------|------|------|
| 1 | 多中标人命中率提升 ≥10% | **不达标（待查）** | baseline 实测已 100%（36/36 次），天花板效应，提升 0pp。如实报告，不编造提升 |
| 2 | 单中标人不回归 | ✅ | 3 篇单中标人两组均 1/1 exact |
| 3 | 联合体区分主投标人/合作伙伴 | ✅（格式级） | 构造样本验证通过；无真实金标样本，见待查 3 |
| 4 | 不修改 extractor.py | ✅ | 仅产出 winner_name_v2.txt + 本报告 |
| 5 | 真实 LLM 调用记录 tokens/latency/model | ✅ | 43 次调用逐条记录于两份 results JSON，模型 deepseek-v4-flash |

## 七、待查项（按 Sol 铁律，不宣称"通过"）

1. **验收标准 1 客观不可达**：baseline 在当前金标上已饱和。若要坚持"提升 ≥10%"的验证形式，需要先扩充更难的多中标人金标（如 >5 家入围、名单跨页、表格嵌套、扫描件 OCR 噪声样本），再对比新旧 prompt。建议转交 W3 金标扩充任务。
2. **v2 的加固价值当前无法量化**：R1-R6 防的是现金标之外的失败模式（合并、代理机构混入、联合体），需要对抗性样本才能验证收益，当前只能声明"无回归 + 结构补齐"。
3. **联合体无真实样本**：22 篇金标中无联合体中标公告，R4 仅经 1 篇构造样本验证格式。建议 W3 补 1-2 篇真实联合体中标金标。
4. **tokens 成本 +26.7%**：few-shot 从 1 条增至 3 条所致。若接入 extractor.py，建议评估对 22 篇全量重跑的成本影响后再合并。
5. **集成未做**：winner_name_v2.txt 尚未接入 extractor.py（任务禁止修改）。接入时需同步更新 compute_prompt_hash 调用与 `extraction_schemas.py` 对 raw_value=dict（联合体）的兼容——该兼容性未验证（待查）。
6. **评测脚本与原始结果在 TRAE 工作区**：`_winner_name_eval.py`、`_winner_name_stability.py`、`winner_eval_results.json`、`winner_stability_results.json`，需随报告一并归档。
