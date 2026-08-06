"""display_rule v1.0 校准（v4.1 §6.6/§10.1）。

离线复算（零 LLM 成本）：
- 数据源: _w3_outputs/gold598_retest.json 的 D 组 rows（诊断性复测产物）
- 校准集: tests/fixtures/gold/dataset_split.json 的 calibration 划分
- 产出: _w3_outputs/display_rule_v1.0_calibration.json
  - 各 grade 的字段数 / 精确率 / 无依据率
  - strict(仅 high) / default(high+review) / loose(全部) 三工作点的覆盖率与精确率

校准结论用于佐证 DISPLAY_RULE_VERSION="v1.0-frozen"（app/processors/display_grade.py）。

用法: python scripts/calibrate_display_rule.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SPLIT_PATH = ROOT / "tests" / "fixtures" / "gold" / "dataset_split.json"
RETEST_PATH = ROOT / "_w3_outputs" / "gold598_retest.json"
OUT_PATH = ROOT / "_w3_outputs" / "display_rule_v1.0_calibration.json"


def derive_grade(row: dict) -> str:
    """从 D 组 row 的证据状态推导展示等级。

    与 app/processors/display_grade.py 规则对齐：
    - 无值 / 金标 absent → low（不进入输出统计）
    - 有值但证据未验证 → low（WEAK）
    - 证据验证 + 确定性校验通过 → high（STRONG 直证）
    - 证据验证但校验未通过 → review
    """
    if row.get("gold_status") == "absent":
        return "low"
    if not row.get("has_value"):
        return "low"
    if not row.get("evidence_verified"):
        return "low"
    if row.get("field_validated"):
        return "high"
    return "review"


def main() -> None:
    split = json.loads(SPLIT_PATH.read_text(encoding="utf-8"))
    calib = set(split["calibration"])
    retest = json.loads(RETEST_PATH.read_text(encoding="utf-8"))

    rows = [r for r in retest.get("rows_D", []) if r.get("doc_id") in calib]
    if not rows:
        raise SystemExit("rows_D 中无校准集文档，先跑 eval_gold598_retest.py")

    # 各 grade 统计
    grades: dict[str, list[dict]] = {"high": [], "review": [], "low": []}
    for r in rows:
        grades[derive_grade(r)].append(r)

    def stat(rs: list[dict]) -> dict:
        judged = [r for r in rs if r.get("correct") is not None]
        correct = sum(1 for r in judged if r["correct"])
        unjust = sum(1 for r in rs if r.get("unjustified"))
        return {
            "fields": len(rs),
            "judged": len(judged),
            "precision": (correct / len(judged)) if judged else None,
            "unjustified": unjust,
            "unjustified_rate": (unjust / len(rs)) if rs else 0.0,
        }

    grade_stats = {g: stat(rs) for g, rs in grades.items()}

    # 三工作点：coverage = 输出的 present 金标字段 / 全部 present 金标字段
    present_rows = [r for r in rows if r.get("gold_status") == "present"]
    total_present = len(present_rows)

    def workpoint(allowed: set) -> dict:
        out = [r for r in present_rows if derive_grade(r) in allowed]
        judged = [r for r in out if r.get("correct") is not None]
        correct = sum(1 for r in judged if r["correct"])
        return {
            "output_fields": len(out),
            "coverage": (len(out) / total_present) if total_present else 0.0,
            "precision": (correct / len(judged)) if judged else None,
        }

    report = {
        "task": "display_rule_calibration",
        "rule_version": "v1.0-frozen",
        "calibration_docs": len(calib),
        "rows_evaluated": len(rows),
        "grade_stats": grade_stats,
        "workpoints": {
            "strict": workpoint({"high"}),
            "default": workpoint({"high", "review"}),
            "loose": workpoint({"high", "review", "low"}),
        },
        "note": (
            "基于 598 诊断性复测的 D 组 rows 在校准集上离线复算，"
            "佐证 display_grade 三级规则与四策略工作点；test 集未参与。"
        ),
    }
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"校准集: {len(calib)} 篇 | rows: {len(rows)}")
    for g, s in grade_stats.items():
        prec = f"{s['precision']:.2%}" if s["precision"] is not None else "N/A"
        print(f"  {g}: fields={s['fields']} precision={prec} unjustified_rate={s['unjustified_rate']:.2%}")
    for wp, s in report["workpoints"].items():
        prec = f"{s['precision']:.2%}" if s["precision"] is not None else "N/A"
        print(f"  工作点 {wp}: coverage={s['coverage']:.2%} precision={prec} out={s['output_fields']}")
    print(f"报告: {OUT_PATH}")


if __name__ == "__main__":
    main()
