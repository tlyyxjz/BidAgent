"""W3-03 错误模式分析。

分析哪些字段/公告类型/错误模式导致 recall=0.64 偏低，找优化点。
"""
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "_w3_outputs" / "w3_03_evidence_full.json"
GOLD = ROOT / "tests" / "fixtures" / "gold" / "k3_annotations_batch2.json"
RAW_DIR = ROOT / "_w3_raw"

with open(REPORT, encoding="utf-8") as f:
    report = json.load(f)
with open(GOLD, encoding="utf-8") as f:
    gold_data = json.load(f)

doc_metrics = report["doc_metrics"]
gold_by_id = {d["document_id"]: d for d in gold_data if isinstance(d, dict) and not d.get("_is_meta")}

# ========== 1. 按字段统计 recall ==========
print("=" * 70)
print("1. 按字段统计 recall（哪些字段最难抽）")
print("=" * 70)
field_stats = defaultdict(lambda: {"present": 0, "found": 0})
for dm in doc_metrics:
    doc_id = dm["doc_id"]
    gd = gold_by_id.get(doc_id)
    if not gd:
        continue
    # 从 gold 取每个字段状态
    gold_field_status = {f["field_name"]: f["gold_status"] for f in gd.get("fields", [])}
    # doc_metrics 只有汇总，没有 per-field；需要从原 report 取
    # 实际 report 里没有 per-field，重新跑一遍逻辑太重
    # 改用 doc 级别的失败模式分析

# doc_metrics 没有 per-field 明细，改用其他角度分析
print("(注: report 中无 per-field 明细，改用 doc 级别分析)")
print()

# ========== 2. 按 recall 分桶 ==========
print("=" * 70)
print("2. 90篇按 recall 分桶")
print("=" * 70)
buckets = Counter()
for dm in doc_metrics:
    r = dm["recall"]
    if r == 0:
        buckets["0.0 (全失败)"] += 1
    elif r < 0.5:
        buckets["0.0-0.5 (较差)"] += 1
    elif r < 0.8:
        buckets["0.5-0.8 (中等)"] += 1
    elif r < 1.0:
        buckets["0.8-1.0 (较好)"] += 1
    else:
        buckets["1.0 (全中)"] += 1
for k in ["0.0 (全失败)", "0.0-0.5 (较差)", "0.5-0.8 (中等)", "0.8-1.0 (较好)", "1.0 (全中)"]:
    print(f"  {k}: {buckets.get(k, 0)} 篇")
print()

# ========== 3. 全失败的篇目 ==========
print("=" * 70)
print("3. recall=0 的篇目（完全失败，需重点分析）")
print("=" * 70)
zero_docs = [dm for dm in doc_metrics if dm["recall"] == 0]
print(f"共 {len(zero_docs)} 篇")
for dm in zero_docs[:10]:
    gd = gold_by_id.get(dm["doc_id"])
    print(f"  {dm['doc_id']} ({dm['notice_type']}): present={dm['fields_present']} found=0 pred={dm['evidences_pred']}")
    if gd:
        statuses = {f["field_name"]: f["gold_status"] for f in gd.get("fields", [])}
        present_fields = [k for k, v in statuses.items() if v == "present"]
        print(f"    present字段: {present_fields}")
print()

# ========== 4. 按公告类型 + precision 分析 ==========
print("=" * 70)
print("4. 按公告类型的 recall / precision / IoU")
print("=" * 70)
type_stats = defaultdict(lambda: {"docs": 0, "recall_sum": 0, "precision_sum": 0, "iou_sum": 0, "low_recall": 0})
for dm in doc_metrics:
    t = dm["notice_type"]
    type_stats[t]["docs"] += 1
    type_stats[t]["recall_sum"] += dm["recall"]
    type_stats[t]["precision_sum"] += dm["precision"]
    type_stats[t]["iou_sum"] += dm["iou_avg"]
    if dm["recall"] < 0.5:
        type_stats[t]["low_recall"] += 1
for t, s in sorted(type_stats.items()):
    n = s["docs"]
    print(f"  {t}: docs={n} avg_recall={s['recall_sum']/n:.4f} avg_precision={s['precision_sum']/n:.4f} avg_iou={s['iou_sum']/n:.4f} low_recall_count={s['low_recall']}")
print()

# ========== 5. evidences_pred=0 的篇目（LLM没输出证据）==========
print("=" * 70)
print("5. evidences_pred=0 的篇目（LLM 没输出候选证据）")
print("=" * 70)
no_pred = [dm for dm in doc_metrics if dm["evidences_pred"] == 0]
print(f"共 {len(no_pred)} 篇")
for dm in no_pred[:5]:
    print(f"  {dm['doc_id']} ({dm['notice_type']}): present={dm['fields_present']}")
print()

# ========== 6. evidences_located < evidences_pred（locator 定位失败）==========
print("=" * 70)
print("6. locator 定位失败比例（evidences_located / evidences_pred）")
print("=" * 70)
total_pred = sum(dm["evidences_pred"] for dm in doc_metrics)
total_located = sum(dm["evidences_located"] for dm in doc_metrics)
total_matched = sum(dm["evidences_matched"] for dm in doc_metrics)
print(f"  总候选证据: {total_pred}")
print(f"  locator 定位成功: {total_located} ({total_located/max(total_pred,1)*100:.1f}%)")
print(f"  与金标匹配: {total_matched} ({total_matched/max(total_pred,1)*100:.1f}%)")
print(f"  定位成功但未匹配: {total_located - total_matched} (IoU<0.5)")
print()

# ========== 7. IoU 分布 ==========
print("=" * 70)
print("7. 匹配证据的 IoU 分布")
print("=" * 70)
all_ious = []
for dm in doc_metrics:
    all_ious.extend(dm.get("iou_list_matched", []))
if all_ious:
    sorted_ious = sorted(all_ious)
    n = len(sorted_ious)
    print(f"  匹配证据总数: {n}")
    print(f"  IoU=1.0 (完美匹配): {sum(1 for x in all_ious if x == 1.0)} ({sum(1 for x in all_ious if x == 1.0)/n*100:.1f}%)")
    print(f"  IoU>=0.8: {sum(1 for x in all_ious if x >= 0.8)} ({sum(1 for x in all_ious if x >= 0.8)/n*100:.1f}%)")
    print(f"  IoU 0.5-0.8: {sum(1 for x in all_ious if 0.5 <= x < 0.8)} ({sum(1 for x in all_ious if 0.5 <= x < 0.8)/n*100:.1f}%)")
    print(f"  P50: {sorted_ious[n//2]}")
    print(f"  P95: {sorted_ious[int(n*0.95)]}")
print()

# ========== 8. 最差的 10 篇 ==========
print("=" * 70)
print("8. recall 最差的 10 篇（优化重点）")
print("=" * 70)
worst = sorted(doc_metrics, key=lambda d: d["recall"])[:10]
for dm in worst:
    print(f"  {dm['doc_id']} ({dm['notice_type']}): recall={dm['recall']} precision={dm['precision']} present={dm['fields_present']} pred={dm['evidences_pred']} located={dm['evidences_located']} matched={dm['evidences_matched']}")
