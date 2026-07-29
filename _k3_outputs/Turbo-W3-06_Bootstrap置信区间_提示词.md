# Turbo-W3-06 Bootstrap 置信区间实现 任务提示词

## 任务背景

按 v4.1 第十章 10.10 节，需要为关键评测指标计算 95% 置信区间。Bootstrap 以**采购项目为最小重采样单元**（不按单个字段独立采样），同一项目的公告、版本和字段整体参与采样。

## 输入

- W3-03 评测报告：`_w3_outputs/w3_03_evidence_full.json`（我跑完后提供）
  - 结构：`overall`（汇总指标）+ `doc_metrics[]`（逐篇指标）
  - 每篇含 `doc_id`、`notice_type`、`fields_present`、`fields_found`、`evidences_pred`、`evidences_matched`、`recall`、`precision`
- doc_id 命名规则：`w3_{type}_{num}`（如 w3_tender_001），同一 type 内 num 相连的可能是同一项目多个公告（需按 notice_type 分组采样）

## 实现要求

### 文件位置
- `app/eval/bootstrap_ci.py`（新建）
- `tests/test_bootstrap_ci.py`（单元测试）

### 核心函数

```python
def bootstrap_ci(
    doc_metrics: list[dict],
    metric_keys: list[str],  # ["recall", "precision", "iou_avg"]
    n_bootstrap: int = 1000,
    confidence: float = 0.95,
    random_seed: int = 42,
    group_key: str = "notice_type",  # 以公告类型为分组单元（W3数据无project_id）
) -> dict:
    """Bootstrap 置信区间计算。

    Args:
        doc_metrics: 逐篇指标列表（来自W3-03报告的doc_metrics）
        metric_keys: 需要计算CI的指标名
        n_bootstrap: 采样次数（默认1000）
        confidence: 置信水平（默认0.95）
        random_seed: 随机种子（必须记录）
        group_key: 分组字段（W3无project_id，暂用notice_type替代）

    Returns:
        {
            "metric_name": {
                "point_estimate": float,  # 点估计（原始全量计算）
                "ci_lower": float,        # 下界
                "ci_upper": float,        # 上界
                "bootstrap_samples": list[float],  # 所有采样值（用于绘制分布）
            },
            "meta": {
                "n_bootstrap": int,
                "confidence": float,
                "random_seed": int,
                "group_key": str,
                "n_groups": int,
                "n_docs": int,
            }
        }
    """
```

### 算法步骤

1. **分组**：按 `group_key`（notice_type）将 doc_metrics 分组
2. **点估计**：全量计算每个指标的值（aggregate 后除法）
3. **Bootstrap 循环**（n_bootstrap 次）：
   - 有放回采样 n_groups 个组（group 为最小单元）
   - 把采样到的组的所有 doc 拼起来
   - 重新计算每个指标
   - 记录采样值
4. **置信区间**：对每个指标的采样值排序，取 2.5% 和 97.5% 分位数
5. **记录随机种子**和采样次数（v4.1 要求）

### 指标计算口径

- recall = sum(fields_found) / sum(fields_present)
- precision = sum(evidences_matched) / sum(evidences_pred)
- iou_avg = sum(iou_list_matched) / sum(evidences_pred)
- **注意**：是先求和再相除（不是逐篇相除再平均），这与 OverallMetric 的口径一致

### 单元测试要求

1. **基础测试**：构造 10 篇 doc_metrics（3 组），跑 100 次 bootstrap，验证输出结构
2. **边界测试**：单组数据（退化为点估计）、空数据
3. **可复现性**：相同 random_seed 两次运行结果一致
4. **CI 合理性**：ci_lower ≤ point_estimate ≤ ci_upper
5. **分组正确性**：验证采样是按组而非按篇

## 验收标准

- [ ] `app/eval/bootstrap_ci.py` 实现完成
- [ ] `tests/test_bootstrap_ci.py` 至少 5 个测试用例全部通过
- [ ] 用 W3-03 报告数据跑一次，输出 CI 结果到 `_w3_outputs/w3_06_bootstrap_ci.json`
- [ ] 记录 random_seed=42, n_bootstrap=1000
- [ ] pytest --cov=app.eval 覆盖率 ≥90%

## 约束

1. **不修改 W3-03 评测脚本**，只读取其输出 JSON
2. **不调用 LLM**，纯统计算法
3. **分支**：feature/glm-w2-evidence，commit: `feat(W3-06): Bootstrap置信区间实现`
4. **依赖**：numpy（如未安装可用纯 Python，但需在 docstring 说明）

## 待查项

- W3 数据无 project_id 字段，暂用 notice_type 分组（tender/award/correction 三组）。这是 W3 的已知限制，v4.1 要求按采购项目分组，待数据库接入 project_id 后修正。
- n_bootstrap=1000 是否足够稳定，可在测试中跑 5000 次对比。
