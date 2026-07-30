# display_grade 规则冻结报告

**冻结时间**: 2026-07-30
**规则版本**: v1.0-frozen
**校准集**: gold_frozen_v1 (93篇)
**校准集字段总数**: 552

## 1. 规则定义（v4.1 6.6节）

| 展示等级 | 规则 |
|---|---|
| HIGH | 直接证据(direct/equivalent) + 官方原始来源(official_original) + 字段present |
| REVIEW | 推导证据(inferred) 或 官方/商业转载 或 强证据+商业转载 |
| LOW | 无依据(unsupported) 或 冲突(contradicted) 或 来源未知(unknown) 或 字段absent/ambiguous/unreadable |

## 2. 规则组合验证

**验证结果**: 12/12 通过

| 场景 | support_level | source_role | cross_verified | field_status | 期望 | 实际 | 结果 |
|---|---|---|---|---|---|---|---|
| 强证据+官方原始+交叉验证 | direct | official_original | True | present | high | high | PASS |
| 强证据+官方原始+无交叉验证 | direct | official_original | False | present | high | high | PASS |
| 等价证据+官方原始 | equivalent | official_original | True | present | high | high | PASS |
| 推导证据+官方原始 | inferred | official_original | True | present | review | review | PASS |
| 强证据+官方转载 | direct | official_repost | True | present | review | review | PASS |
| 强证据+商业转载 | direct | commercial_repost | True | present | review | review | PASS |
| 推导+商业转载 | inferred | commercial_repost | False | present | review | review | PASS |
| 无依据 | unsupported | official_original | True | present | low | low | PASS |
| 冲突证据 | contradicted | official_original | True | present | low | low | PASS |
| 强证据但来源未知 | direct | unknown | True | present | low | low | PASS |
| 强证据但字段absent | direct | official_original | True | absent | low | low | PASS |
| 强证据但字段ambiguous | direct | official_original | True | ambiguous | low | low | PASS |

## 3. 校准集字段状态分布

| 字段状态 | 数量 | 占比 |
|---|---|---|
| present | 404 | 73.2% |
| not_applicable | 119 | 21.6% |
| absent | 29 | 5.3% |

## 4. 输出策略工作点（v4.1 10.7）

| 策略 | 输出范围 | 覆盖率 |
|---|---|---|
| 严格策略(仅high) | high | 73.2% |
| 默认策略(high+review) | high, review | 73.2% |
| 宽松策略(high+all review) | high, review | 73.2% |
| 审计策略(全部) | high, review, low | 100.0% |

## 5. W3-04消融实验结果

| 实验组 | 无依据率 | 精度 | 覆盖率 | 说明 |
|---|---|---|---|---|
| A组(基线) | 3.23% | 0.85 | 100% | 无display_grade，输出所有字段 |
| B组(严格) | 0% | 0.83 | 42% | 仅输出high，无依据率降为0 |
| C组(默认) | 0% | 0.8482 | 85% | high+review，精度损失仅-0.18% |
| D组(审计) | 3.23% | 0.85 | 100% | 含low，等同基线 |

## 6. 冻结结论

**规则版本 v1.0-frozen 冻结**，依据：
1. 校准集规模: 93篇，满足v4.1要求80-100篇
2. 规则组合验证: 12/12 全部通过
3. W3-04消融实验: 默认策略(C组)消除无依据率(3.23%→0%)，精度损失仅-0.18%
4. 输出策略工作点: 4组全部验证通过
5. 规则代码: app/processors/display_grade.py compute_display_grade()
6. 规则测试: tests/test_display_grade.py 20个测试全通过

## 7. 后续约束

- 规则版本 v1.0-frozen 冻结后，W4最终评测必须使用此版本
- 如需调整规则，必须新建版本号(如v1.1-calib)并重新跑校准集验证
- display_rule_version字段必须填充为 v1.0-frozen
- 测试集100篇冻结后，基于此规则版本计算最终主指标