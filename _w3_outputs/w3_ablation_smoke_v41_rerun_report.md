# v4.1 真实 LLM 消融实验重跑报告 — null_false_positive_rate 指标验证

## 1. 实验元信息

| 项目 | 值 |
|---|---|
| 实验时间 (request_time, UTC) | 2026-08-03T08:18:17Z |
| 实验时间 (本地 Asia/Shanghai) | 2026-08-03 16:18 (CST) |
| 模型标识 (model_id) | `deepseek-v4-flash` |
| provider | `deepseek` |
| model_role | `primary` |
| 代码提交 (code_commit) | `caeacde` |
| 数据源 | W3 评测集 (`_w3_raw/`, 金标 `tests/fixtures/gold/k3_annotations_batch2.json`) |
| 公告数量 | 5 篇 |
| invalid_docs_count | 0（全部 5 篇 LLM 调用成功，无失败排除） |
| 主脚本 | `scripts/eval_ablation.py` |
| 输出文件 | `_w3_outputs/w3_ablation_smoke_v41_rerun.json` |
| 环境变量 | `TMPDIR=C:\\Users\\Lenovo\\Desktop\\BidAgent\\_tmp_pytest`, `PYTHONIOENCODING=utf-8` |

### 选取的 5 篇公告（覆盖 4 种类型）

| document_id | 类型 | 原文字符数 | should_not_have_value 字段数 |
|---|---|---|---|
| w3_tender_001 | 招标 | 2010 | 1 (winner_name=not_applicable) |
| w3_tender_002 | 招标 | 2883 | 1 (winner_name=not_applicable) |
| w3_award_016 | 中标 | 2672 | 1 (bid_deadline=not_applicable) |
| w3_correction_031 | 更正 | 1681 | 2 (amount=absent, winner_name=not_applicable) |
| w3_award_consortium_001 | 联合体中标 | 2843 | 1 (bid_deadline=not_applicable) |
| **合计** | — | 12089 | **6** |

## 2. 请求参数与 LLM 调用统计（按组）

| 指标 | A 组 (Direct LLM) | B 组 (LLM+候选证据) | C 组 (LLM+程序验证) | D 组 (完整 BidAgent) |
|---|---|---|---|---|
| prompt_hash | `657cb4edb732edf5` | `cea0a3657b4c768c` | `cea0a3657b4c768c` | `cea0a3657b4c768c` |
| temperature (记录值) | 0.0 | 0.0 | 0.0 | 0.0 |
| top_p | 1.0 | 1.0 | 1.0 | 1.0 |
| LLM 调用次数 | 5 (每篇 1 次) | 5 (每篇 1 次) | 5 (每篇 1 次) | 0 (复用 C 组) |
| total_tokens | 20250 | 29750 | 32599 | 32599 (复用 C 组) |
| latency_ms_avg | 15148.4 | 27515.0 | 32954.8 | 32954.8 (复用 C 组) |
| normalizer_version | 1.0 | 1.0 | 1.0 | 1.0 |
| evidence_rule_version | evidence_locator_v1.0 | evidence_locator_v1.0 | evidence_locator_v1.0 | evidence_locator_v1.0 |
| display_rule_version | v1.0-frozen | v1.0-frozen | v1.0-frozen | v1.0-frozen |

> 说明：D 组基于 v4.1 §10.11 Baseline 公平性约束，复用 C 组 LLM 调用结果，仅增加 display_grade 计算与选择性输出，不重新调用 LLM。

## 3. null_false_positive_rate 指标验证（核心）

### 3.1 指标定义（v4.1 §10）

> 金标为 `absent` / `not_applicable` 的字段（即 `should_not_have_value` 类）中，系统错误输出值（`has_value=True`）的比例。

计算公式：`null_false_positive_rate = null_false_positives / should_not_have_value_fields`

### 3.2 本次重跑结果

| 组 | should_not_have_value_fields | null_false_positives | null_false_positive_rate (JSON 存储值) | null_false_positive_rate (从 rows 重算) |
|---|---|---|---|---|
| A | 6 | 0 | **0.0000** | 0.0 |
| B | 6 | 0 | **0.0000** | 0.0 |
| C | 6 | 0 | **0.0000** | 0.0 |
| D | 6 | 0 | **0.0000** | 0.0 |

**验证结论**：
- ✅ `null_false_positive_rate` 字段在 JSON summaries 中存在（A/B/C/D 四组均有）
- ✅ 值与从 rows 重算的结果一致（JSON 存储值 = 重算值 = 0.0）
- ✅ 6 个 should_not_have_value 字段全部正确预测为 `absent`（has_value=False），0 误报

### 3.3 should_not_have_value 字段明细（Group A，全部正确）

| document_id | field_name | gold_status | pred_status | has_value |
|---|---|---|---|---|
| w3_award_consortium_001 | bid_deadline | not_applicable | absent | False |
| w3_tender_001 | winner_name | not_applicable | absent | False |
| w3_tender_002 | winner_name | not_applicable | absent | False |
| w3_award_016 | bid_deadline | not_applicable | absent | False |
| w3_correction_031 | amount | absent | absent | False |
| w3_correction_031 | winner_name | not_applicable | absent | False |

## 4. 完整指标汇总（A/B/C/D 四组对比）

| 指标 | A 组 | B 组 | C 组 | D 组 |
|---|---|---|---|---|
| docs_count | 5 | 5 | 5 | 5 |
| fields_total | 30 | 30 | 30 | 30 |
| fields_with_value | 24 | 24 | 24 | 24 |
| fields_with_evidence | 0 | 24 | 24 | 24 |
| fields_evidence_verified | 0 | 0 | 24 | 24 |
| fields_field_validated | 0 | 0 | 23 | 23 |
| fields_unjustified | 24 | 0 | 1 | 0 |
| unjustified_rate | 100.00% | 0.00% | 4.17% | 0.00% |
| fields_correct | 29 | 29 | 29 | 23 |
| fields_evaluable | 30 | 30 | 30 | 24 |
| field_precision | 96.67% | 96.67% | 96.67% | 95.83% |
| evidence_precision | N/A | N/A | 100.00% | 100.00% |
| multi_value_f1_avg | 0.7000 | 0.7000 | 0.7000 | 0.7000 |
| **null_false_positive_rate** | **0.0000** | **0.0000** | **0.0000** | **0.0000** |

## 5. 与上次冒烟结果对比（稳定性验证）

| 指标 | 本次 rerun (5 篇) A 组 | 上次 prev (3 篇) A 组 | 本次 rerun (5 篇) C 组 | 上次 prev (3 篇) C 组 |
|---|---|---|---|---|
| docs_count | 5 | 3 | 5 | 3 |
| fields_total | 30 | 18 | 30 | 18 |
| **null_false_positive_rate** | **0.0** | **0.0** | **0.0** | **0.0** |
| fields_with_value | 24 | 13 | 24 | 13 |
| field_precision | 0.9667 | 0.7778 | 0.9667 | 0.8333 |
| multi_value_f1_avg | 0.7000 | 0.8611 | 0.7000 | 0.8056 |
| total_tokens | 20250 | 15623 | 32599 | 25139 |
| latency_ms_avg | 15148.4 | 17778.7 | 32954.8 | 42101.3 |
| model_id | deepseek-v4-flash | deepseek-v4-flash | deepseek-v4-flash | deepseek-v4-flash |
| prompt_hash | 657cb4edb732edf5 | 657cb4edb732edf5 | cea0a3657b4c768c | cea0a3657b4c768c |
| code_commit | caeacde | caeacde | caeacde | caeacde |

**上次 prev 公告**：w3_correction_093, w3_award_consortium_001, w3_tender_091
**本次 rerun 公告**：w3_award_consortium_001, w3_tender_001, w3_tender_002, w3_award_016, w3_correction_031

### 稳定性判断

- ✅ **null_false_positive_rate 稳定**：两次均为 0.0（A/B/C/D 四组一致），系统对 absent/not_applicable 字段未产生误报
- ✅ **model_id / prompt_hash / code_commit 一致**：实验配置可复现
- ℹ️ **field_precision 本次更高**（A 组 0.9667 vs 0.7778）：因两次公告集不同（本次 5 篇覆盖更广类型），非指标不稳定，属正常波动
- ℹ️ **multi_value_f1_avg 本次略低**（0.7 vs 0.8611）：同样因公告集差异（多值字段分布不同），非指标不稳定

## 6. 结论

1. **null_false_positive_rate 指标计算正确并已输出**：在 `_w3_outputs/w3_ablation_smoke_v41_rerun.json` 的 `summaries.{A,B,C,D}.null_false_positive_rate` 字段中，值为 0.0000，与从 rows 重算的结果一致。
2. **指标稳定**：本次 5 篇（覆盖 tender/award/correction/consortium 四种类型，6 个 should_not_have_value 字段）与上次 3 篇结果一致，null_false_positive_rate 均为 0.0。
3. **真实 LLM 调用**：model=deepseek-v4-flash，共 15 次 LLM 调用（A×5 + B×5 + C×5，D 复用 C），total_tokens A=20250/B=29750/C=32599，无 invalid docs，无 mock 数据。
4. **实验复现信息完整**：记录了 model_id、prompt_hash、total_tokens、latency_ms_avg、code_commit、request_time 等 v4.1 §10.12 要求的复现字段。

## 7. 观察备注（非本次任务范围，仅记录）

- `temperature` 在 JSON 中记录为 0.0，但 `app/llm/extractor.py` 实际 LLM 调用使用 `temperature=0.1`（line 563/649）。这是 `experiment_meta.py` 的记录口径与实际请求参数的轻微不一致，不影响 null_false_positive_rate 指标验证结论，但建议后续修复以提升复现信息准确性。
- 金标文件实际为 `tests/fixtures/gold/k3_annotations_batch2.json`（100 篇），非任务描述中的 `gold_frozen_v1.json`；脚本 `eval_ablation.py` line 51 硬编码读取 `k3_annotations_batch2.json`，本次实验遵循脚本实际行为。
