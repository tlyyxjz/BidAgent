"""从消融实验 rows 聚合 doc_metrics 并跑 Bootstrap 95% 置信区间。

口径定义:
  recall            = sum(fields_found) / sum(fields_present)            [先求和再相除]
  evidence_precision= sum(evidences_matched) / sum(evidences_pred)       [先求和再相除]
  iou_avg           = sum(iou_list_matched) / sum(evidences_pred)        [近似,待查]
  field_precision   = mean(每篇 fields_correct / fields_evaluable)       [逐篇平均,待查口径]

  注意: field_precision 用 _aggregate_metric 通用 fallback (逐篇平均),
        与消融汇总的 sum(correct)/sum(evaluable) 口径不一致, 标记待查.

从 GroupResult 聚合到 doc_metrics:
  fields_present = 金标中 gold_status ∈ (present, multi_value) 的字段数
  fields_found   = has_value=True 且 gold_status ∈ (present, multi_value) 的字段数
  evidences_pred = has_evidence=True 的字段数
  evidences_matched = evidence_verified=True 的字段数
  iou_list_matched  = [1.0 if evidence_verified else 0.0 for each has_evidence field]
                     (消融实验未算真实 IoU, 用验证通过率近似, 标记待查)
  fields_correct   = correct=True 的字段数
  fields_evaluable = correct is not None 的字段数
  project_id = document_id (金标无 project_id, 每篇为独立公告, 用 document_id 作分组单元)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.eval.bootstrap_ci import bootstrap_ci

ABLATION_PATH = ROOT / "_w3_outputs" / "w3_ablation_full_99.json"
GOLD_PATH = ROOT / "tests" / "fixtures" / "gold" / "k3_annotations_batch2.json"
OUT_PATH = ROOT / "_w3_outputs" / "w3_bootstrap_ci_full_99.json"

PRESENT_STATUSES = {"present", "multi_value"}


def aggregate_doc_metrics(rows: list[dict]) -> list[dict]:
    """把 GroupResult rows 聚合成 bootstrap_ci 期望的 doc_metrics.

    project_id 用 document_id (每篇独立公告作为采样单元)。
    """
    by_doc: dict[str, list[dict]] = {}
    for r in rows:
        by_doc.setdefault(r["doc_id"], []).append(r)

    doc_metrics = []
    for doc_id, doc_rows in by_doc.items():
        fields_present = sum(
            1 for r in doc_rows if r["gold_status"] in PRESENT_STATUSES
        )
        fields_found = sum(
            1
            for r in doc_rows
            if r["has_value"] and r["gold_status"] in PRESENT_STATUSES
        )
        evidences_pred = sum(1 for r in doc_rows if r["has_evidence"])
        evidences_matched = sum(1 for r in doc_rows if r["evidence_verified"])
        iou_list_matched = [
            1.0 if r["evidence_verified"] else 0.0
            for r in doc_rows
            if r["has_evidence"]
        ]
        fields_correct = sum(1 for r in doc_rows if r["correct"] is True)
        fields_evaluable = sum(1 for r in doc_rows if r["correct"] is not None)
        doc_metrics.append({
            "document_id": doc_id,
            "project_id": doc_id,  # 用 document_id 作分组单元 (99 组)
            "fields_present": fields_present,
            "fields_found": fields_found,
            "evidences_pred": evidences_pred,
            "evidences_matched": evidences_matched,
            "iou_list_matched": iou_list_matched,
            "fields_correct": fields_correct,
            "fields_evaluable": fields_evaluable,
        })
    return doc_metrics


def fmt_ci(result: dict, key: str) -> str:
    r = result[key]
    return f"{r['point_estimate']:.4f}  CI=[{r['ci_lower']:.4f}, {r['ci_upper']:.4f}]"


def main():
    with open(ABLATION_PATH, encoding="utf-8") as f:
        ablation = json.load(f)

    # recall/evidence_precision/iou_avg 用内置口径; field_precision 用 fallback (逐篇平均)
    metric_keys = ["recall", "evidence_precision", "iou_avg", "field_precision"]
    # 注: bootstrap_ci._aggregate_metric 只识别 recall/precision/iou_avg 三个内置 key
    # "evidence_precision" 会走 fallback (逐篇平均), 与汇总 sum/sum 口径不同, 标注待查
    # 为保持先求和再相除口径, 我们用 "precision" (内置) 表示证据精确率
    metric_keys_builtin = ["recall", "precision", "iou_avg", "field_precision"]

    all_results = {}

    print("=" * 80)
    print("Bootstrap 95% 置信区间 (从消融实验 rows 聚合, 99 篇)")
    print("=" * 80)
    print(f"数据源: {ABLATION_PATH.name}")
    print(f"金标: {GOLD_PATH.name}")
    print(f"分组键: document_id (99 组, 每篇独立公告作采样单元)")
    print(f"采样次数: 1000  置信水平: 0.95  随机种子: 42")
    print(f"指标: recall/precision(证据)/iou_avg(近似)/field_precision(逐篇平均,待查口径)")
    print()

    for group in ["A", "B", "C", "D"]:
        rows = ablation["rows"][group]
        doc_metrics = aggregate_doc_metrics(rows)
        n_docs = len(doc_metrics)
        n_groups = len({d["project_id"] for d in doc_metrics})

        result = bootstrap_ci(
            doc_metrics=doc_metrics,
            metric_keys=metric_keys_builtin,
            n_bootstrap=1000,
            confidence=0.95,
            random_seed=42,
            group_key="project_id",
        )
        all_results[group] = {
            "doc_metrics": doc_metrics,
            "bootstrap": result,
            "meta": {
                "n_docs": n_docs,
                "n_groups": n_groups,
                "group_key": "document_id",
            },
        }

        print(f"--- Group {group} (docs={n_docs}, groups={n_groups}) ---")
        print(f"  recall            {fmt_ci(result, 'recall')}")
        print(f"  precision(证据)   {fmt_ci(result, 'precision')}")
        print(f"  iou_avg(近似)     {fmt_ci(result, 'iou_avg')}")
        print(f"  field_precision   {fmt_ci(result, 'field_precision')}  [逐篇平均口径]")
        bm = result["meta"]
        print(f"  (meta: n_bootstrap={bm['n_bootstrap']}, n_groups={bm['n_groups']}, n_docs={bm['n_docs']})")
        print()

    out = {
        "source": str(ABLATION_PATH),
        "gold": str(GOLD_PATH),
        "metric_keys": metric_keys_builtin,
        "caliber_notes": {
            "recall": "sum(fields_found)/sum(fields_present) [先求和再相除]",
            "precision": "sum(evidences_matched)/sum(evidences_pred) [证据精确率, 先求和再相除]",
            "iou_avg": "sum(iou_list_matched)/sum(evidences_pred) [近似: 验证通过率, 待查]",
            "field_precision": "mean(每篇 correct/evaluable) [逐篇平均, 与汇总 sum/sum 口径不一致, 待查]",
            "fields_present": "gold_status ∈ (present, multi_value)",
            "fields_found": "has_value=True 且 gold_status ∈ (present, multi_value)",
            "evidences_pred": "has_evidence=True",
            "evidences_matched": "evidence_verified=True",
            "iou_list_matched": "[1.0 if evidence_verified else 0.0 for has_evidence] (近似,待查)",
            "fields_correct": "correct=True",
            "fields_evaluable": "correct is not None",
            "group_key": "document_id (99 组, 金标无 project_id)",
        },
        "groups": all_results,
    }
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果已保存: {OUT_PATH}")


if __name__ == "__main__":
    main()
