# K3 代码 review 报告 — W2-08/W2-09 修复（commit c5a6c9b）

- **review 对象**：commit `c5a6c9bf37c0a9f69af2189bfceba0ed31c2daf9`（feature/glm-w2-evidence）
- **review 人**：K3（独立 review，未参与修复）
- **review 方式**：`git show c5a6c9b` 全量 diff + c5a6c9b 版本文件通读 + 硬约束逐条对照
- **后续影响确认**：`git log c5a6c9b..HEAD -- <4文件>` 为空，后续 commit（6729824 / 10ba132）未再修改这 4 个文件，本报告结论对 HEAD 仍有效

---

## 一、逐文件 review

### scripts/eval_ablation.py

| 行号(约) | 问题 | 严重程度 | 修复建议 |
|---|---|---|---|
| 277-286 (run_group_c) | **C 组无 invalid 检测**。`meta` 字典没有 `"invalid"` 键，而 main() 中 `if meta_c.get("invalid"):` 恒为 None（falsy），C 组失败文档永远不会被跳过。C 组 LLM 失败时 6 字段全 missing 会被静默算入评测——这正是 P2 在 B 组修掉的问题，在 C 组原样残留。main() 里写了 `invalid_c` 列表和打印分支，给人"三组都有检测"的错觉，实际是 dead path | **P0** | run_group_c 开头补与 a/b 完全一致的检测：`is_invalid = bool(result.error) or result.total_tokens == 0 or len(result.fields) == 0`，写入 meta 并提前 `return [], meta` |
| 214-225 (run_group_a) | A 组未校验 LLM 是否真的"无证据"。`call_extraction_llm_no_evidence` 的 docstring 声称返回 `candidate_evidences 为空列表`，但若 LLM 幻觉输出 evidences，parse 层会保留；run_group_a 直接硬编码 `has_evidence=False` 不检查。实验纯洁性依赖"LLM 大概率不会输出"的假设 | P1 | 在 `call_extraction_llm_no_evidence` 返回前强制清空：`for f in result.fields: f.candidate_evidences = []`，或在 run_group_a 断言为空并记录违规次数 |
| 188-205 / 231-250 | invalid 检测逻辑（is_invalid 判断 + 跳过 + 计数）**本身无单元测试**。test_extractor.py 只测了 LLM 调用层返回 error result，没测"error result → 跳过评测 → 计入 invalid_docs"这条链路 | P1 | 新增脚本级测试：mock call_extraction_llm 返回 error/tokens=0/fields空 三种 case，断言 run_group_b 返回空 rows + meta["invalid"]=True，断言 summarize 的 invalid_docs_count 正确 |
| 460-466 (main) | invalid 文档的 meta 仍被 append 进 metas，`total_tokens` / `latency_ms_avg` 混入失败调用的数据（失败时 tokens=0、latency 为超时值），拉低均值 | P2 | latency_ms_avg 只对 valid meta 求平均；tokens 可分列 valid_tokens / wasted_tokens |
| 370 (summarize) | B 组 `evidence_precision` 硬编码 0.0，报告中易误读为"B 组证据全错"，实际语义是"未验证" | P2 | 字段类型改 `Optional[float]`，B 组置 None，打印时显示 N/A |
| commit message | "原实现导致 A 组'无依据率 100%'是评测逻辑制造的假结果"——**修复后 A 组无依据率仍约 100%**（`unjustified=has_value`，by design）。P1 真正修复的是 field_precision 的可比性（97.62%→90.48%），不是无依据率。措辞误导后来者 | P2 | commit/报告中改写为"P1 修复 A 组 prompt 与实验目的不符，使 field_precision 真实反映 Direct LLM 能力" |

### scripts/eval_evidence.py

| 行号 | 问题 | 严重程度 | 修复建议 |
|---|---|---|---|
| 181-191 (evaluate_doc) | **完全没有 LLM 失败检测**。`call_extraction_llm` 失败时 `result.fields=[]` → `evidences_pred=0`、`fields_found=0` → 该篇 recall=0、precision=0 被静默算入汇总。commit 声称 "recall 83.87%→87.10%（multi_lot_02 修复后能找到更多字段）"，但本脚本**未修复任何失败处理问题**——recall 提升只是这次 LLM 碰巧成功。下次任何一篇失败，recall 再次被静默拉低，无警告、无 invalid 记录。P2 修复模式未从 eval_ablation.py 迁移到本脚本 | **P0** | evaluate_doc 开头加同款检测：`if result.error or result.total_tokens == 0 or not result.fields: 返回 invalid 标记`；OverallMetric 加 invalid_docs 字段；main() 打印警告（与 eval_ablation.py 对齐） |
| 247-250, 330-333 | 注释与 commit 声称 iou_avg 口径"未定位/**未匹配算 0**"，但实现是"未定位算 0（不进 iou_list 但计入分母），**未匹配按实际 IoU 计入**"（IoU=0.3 的证据以 0.3 进分子，见 225 行 `iou_list.append(iou)` 无条件执行）。实现本身更合理（部分得分），但文档口径描述与实现不符 | P1 | 改注释和报告措辞为"未定位算 0，未匹配按实际 IoU 计入"；或若口径要求严格"未匹配算 0"，则 225 行移入 `if matched:` 分支——二选一，必须一致 |
| 347-348 | iou_p50/p95 改用 `all_ious_matched`（仅匹配证据），与 iou_avg 的 overall 口径（含未匹配）不一致。报告同一屏展示两种口径的分位数和均值，读者易误对比 | P2 | 打印处显式标注"p50/p95 为仅匹配证据口径"；或补一组 overall 口径的 p50/p95 |
| 269-275 (percentile) | 预存在问题（非本次引入）：偶数长度列表 p50 取上中位数而非均值；`int(len*p)` 在 len=100,p=0.95 时 idx=95（第 96 个），口径偏粗 | P2 | 记录为已知近似，或换 statistics.quantiles |

### app/llm/extractor.py

| 行号(约) | 问题 | 严重程度 | 修复建议 |
|---|---|---|---|
| 545-627 (call_extraction_llm_no_evidence) | 与 `call_extraction_llm` 重复约 80 行（headers/payload/httpx 调用/异常处理/logger 全量复制）。后续若改 temperature、max_tokens、超时、重试，极易改一处忘一处（本次 P2 正是"修 B 忘 C"的同类模式） | P1 | 提取公共 `async def _call_llm(system_prompt, user_prompt, prompt_hash) -> ExtractionResult`，两个公开函数各保留 3 行 |
| 586 | payload 中 `temperature=0.1`、`max_tokens=8000`、`response_format` 未记录进 result/meta/结果 JSON，违反硬约束 #49"记录模型标识、**请求参数**、token、延迟"的字面要求（ExtractionResult 无参数字段） | P1 | ExtractionResult 增加 `request_params` 字段（temperature/max_tokens），或评测脚本在 meta 中补记 |
| 176-215 (EXTRACTION_SYSTEM_PROMPT_NO_EVIDENCE) | 正面确认 ✓：全文无 candidate_evidences 要求、无证据角色（primary/context/qualifier）要求，六字段定义与有证据版一致，few-shot 6 字段均无 candidate_evidences 键。P1 修复核心成立 | — | — |
| 217-296 (compute_prompt_hash 签名变更) | 正面确认 ✓：默认参数保持原行为（向后兼容），A 组 hash 与 B/C 组可区分（有测试锁定） | — | — |

### tests/test_extractor.py

| 行号(约) | 问题 | 严重程度 | 修复建议 |
|---|---|---|---|
| 369-373 (test_no_evidence_prompt_no_evidence_role) | 断言逻辑无效：`assert "primary" not in X or "primary" in "amount_type"`——后半恒为 False，断言实际只等价于前半；注释"amount_type 不含 primary，所以这条断言验证 prompt 简化了"与代码逻辑不符。当前碰巧通过，属于凑数测试（假完成 #2） | P1 | 直接写 `assert "primary" not in EXTRACTION_SYSTEM_PROMPT_NO_EVIDENCE`，并补 `assert "context" not in ... and "qualifier" not in ...` |
| 388-392 (test_no_evidence_fewshot_no_candidate_evidences) | 断言弱化：`"candidate_evidences" not in f or f["candidate_evidences"] == []` 允许空数组通过，削弱"无证据 few-shot"保证。当前 few-shot 本无此键，弱断言让未来退化（有人加回空数组）无法被发现 | P2 | 收紧为 `"candidate_evidences" not in f`（与 prompt 常量测试的严格度对齐） |
| 整体 | 17 个新测试与 commit 声明数量一致 ✓；但全部集中在 extractor 层，**对 P2 修复（ablation 的 invalid 链路）和 P3 修复（evidence 的 iou 新口径）零直接测试**——修复的核心逻辑靠"跑了一遍真实数据"验证，无回归保护 | P1 | 见 eval_ablation.py 行的测试建议；eval_evidence.py 的 compute_iou / match_evidence / iou_avg 分母口径补纯函数单测（不依赖 LLM，可 deterministic 验证） |

---

## 二、硬约束违反清单

| 约束 | 状态 | 说明 |
|---|---|---|
| #36 feature 分支提交 | ✓ 符合 | 当前在 feature/glm-w2-evidence |
| #38 API key 不入库 | ✓ 符合 | .env 不在 git；代码无硬编码 key |
| #48 git diff --stat 核对 | ✓ 符合 | 4 文件与 commit 声明一致，无越界修改 |
| #49 冒烟记录模型标识/请求参数/token/延迟 | ⚠️ 部分违反 | model_id/tokens/latency/prompt_hash 有记录；**temperature/max_tokens 等请求参数未记录** |
| #33 LLM 响应格式校验/JSON 截断/超时处理 | ✓ 符合 | json.loads 失败、choices 缺省均被 except 捕获转为 error result |
| #42 token 计数用 API usage | ✓ 符合 | `data["usage"]["total_tokens"]` |
| #18 few-shot 输出 json.dumps | ✓ 符合 | build_extraction_prompt_no_evidence 使用 json.dumps |
| #47 验收报告含逐篇结果与错误案例 | ⚠️ 部分符合 | commit 记录了三组汇总数据，但 invalid 场景（尤其 C 组无检测）无法产生错误案例记录 |
| project_memory「Failure cases must be recorded with success=False」 | ✗ 违反风险 | C 组与 eval_evidence.py 的失败不会被记录为 invalid——正是 P0-1/P0-2 |

---

## 三、13 类假完成清单核查

| # | 类型 | 命中 | 说明 |
|---|---|---|---|
| 1 | 函数 stub | 否 | 无 stub |
| 2 | 测试不足 | **是** | test_no_evidence_prompt_no_evidence_role 断言无效；P2/P3 修复核心逻辑（invalid 链路、iou 新口径）无直接单测 |
| 3 | mock 数据 | 否 | 单测用 mock 合理；冒烟为真实调用（21+7 次，commit 有记录） |
| 4 | 占位实现 | 否 | — |
| 5 | 硬编码 | 轻微 | DEFAULT_DOCS 7 篇硬编码（评测脚本可接受）；temperature=0.1 硬编码且未入记录 |
| 6 | 口径不一致 | **是** | iou_avg 注释"未匹配算 0" vs 实现"未匹配按实际 IoU 计入"；p50/p95 与 iou_avg 口径分裂；commit 对 A 组无依据率的表述与实际不符 |
| 7 | 迁移不完整 | **是** | P2 invalid 检测修了 A/B 组、漏了 C 组，且完全未迁移到 eval_evidence.py（P0-1/P0-2） |
| 8 | 真实 LLM 未验证 | 否 | 有 21+7 次真实调用记录 |
| 9 | 真实数据未验证 | 否 | 7 篇真实金标 |
| 10 | ID 不合格 | 否 | — |
| 11 | 约束冲突 | **是** | main() 对 C 组写了 invalid 分支但 run_group_c 不产生 invalid 标记——代码结构自相矛盾 |
| 12 | 算法错误 | 否 | compute_iou / match_evidence 逻辑正确（交并比、阈值判定复核无误） |
| 13 | warnings 未清理 | 非本次 | test_new_modules.py 预存在 warnings，commit 已声明无关 |

---

## 四、安全/性能问题

- **N+1 查询**：不涉及（无数据库操作）。
- **异步阻塞**：无同步阻塞调用；三组为逐文档串行 await（21 次调用串行），评测脚本可接受，非问题。
- **SQL 注入**：不涉及。
- **路径遍历（低危）**：`load_raw_text` / `load_gold_doc` 的 `doc_prefix` 直接来自 CLI `--docs` 参数，未净化即参与 `Path` 拼接与 glob（`RAW_DIR / f"{doc_prefix}.txt"`）。本地评测工具攻击面极低，但严格说不符合约束 #13 的净化精神。建议加 `assert "/" not in prefix and ".." not in prefix`。
- **无 API key 时行为不一致**：`call_extraction_llm*` 在无 key 时 `raise RuntimeError`（而非返回 error result），run_group_* 未 try/except，脚本会直接崩溃。fail-fast 可接受，但与"失败记 error"的模式不统一，建议在脚本入口显式检查 key 并给出清晰报错。

---

## 五、总体评分与 P0 清单

**总体评分：7 / 10**

P1（无证据 prompt）修复真实、完整、有测试锁定，是合格修复；P3（iou_avg 口径）实现正确但文档口径失真；**P2（invalid 检测）只修了一半**——修了出事的 B 组，漏了同结构的 C 组和同病的 eval_evidence.py，属于典型的"修点不修改"视角盲点，且 main() 里 C 组的 invalid 分支制造了"三组都修了"的假象。

### 必须修复项（P0，均可立即修复）

1. **eval_ablation.py run_group_c 补 invalid 检测**（约 277-286 行，复制 a/b 组的 4 行模式）
2. **eval_evidence.py evaluate_doc 补 invalid 检测 + OverallMetric 补 invalid_docs + main() 打印警告**（与 eval_ablation.py 对齐；修完后 W2-09 的 recall=87.10% 才具备"失败可见"的可信基础）

### 建议修复项（P1，择要）

3. call_extraction_llm_no_evidence 返回前强制清空 candidate_evidences（或 run_group_a 断言）
4. 提取 _call_llm 公共函数消除 80 行重复（防再次"修一忘一"）
5. ExtractionResult/meta 补记 temperature、max_tokens（约束 #49）
6. test_no_evidence_prompt_no_evidence_role 断言重写；为 invalid 链路和 iou 口径补直接单测
7. iou_avg 注释/报告措辞与实现对齐（"未匹配按实际 IoU 计入"）

---

**按 Sol 铁律声明**：本报告仅基于代码与 commit 记录 review，未重新执行 pytest 与 LLM 冒烟；commit 声称的 "571 passed / 21+7 次调用成功" 未由 K3 独立复跑，标记 **待查**（建议人工按 commit 验收方法复跑后关闭）。
