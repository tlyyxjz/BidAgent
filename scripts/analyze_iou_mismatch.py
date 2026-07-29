"""分析"定位成功但未匹配"的300个证据——是基准问题还是真的不匹配？"""
import json
from pathlib import Path

REPORT = Path(r"C:\Users\Lenovo\Desktop\BidAgent\_w3_outputs\w3_03_evidence_full.json")
with open(REPORT, encoding="utf-8") as f:
    report = json.load(f)

doc_metrics = report["doc_metrics"]

# 统计每篇的 iou_list_matched vs iou_list
mismatch_docs = []
for dm in doc_metrics:
    located = dm["evidences_located"]
    matched = dm["evidences_matched"]
    if located > matched:
        mismatch_docs.append((dm["doc_id"], dm["notice_type"], located, matched, located - matched))

print(f"=== 定位成功但未匹配的篇目: {len(mismatch_docs)}/90 ===")
print(f"总未匹配证据数: {sum(m[4] for m in mismatch_docs)}")
print()

# 看 iou_list（含未匹配的）的分布
all_ious_including_unmatched = []
for dm in doc_metrics:
    all_ious_including_unmatched.extend(dm.get("iou_list", []))

print(f"=== 所有定位成功的证据 IoU 分布 (含未匹配, n={len(all_ious_including_unmatched)}) ===")
sorted_ious = sorted(all_ious_including_unmatched)
n = len(sorted_ious)
print(f"  IoU=0: {sum(1 for x in sorted_ious if x == 0.0)} ({sum(1 for x in sorted_ious if x == 0.0)/n*100:.1f}%)")
print(f"  IoU 0-0.1: {sum(1 for x in sorted_ious if 0 < x < 0.1)}")
print(f"  IoU 0.1-0.5: {sum(1 for x in sorted_ious if 0.1 <= x < 0.5)}")
print(f"  IoU 0.5-0.8: {sum(1 for x in sorted_ious if 0.5 <= x < 0.8)}")
print(f"  IoU>=0.8: {sum(1 for x in sorted_ious if x >= 0.8)}")
print()

# 关键假设：IoU=0 可能是基准偏移问题
# 如果 LLM 抽到的证据文本在原文中多次出现，locator 取首次，但金标 span 是另一次出现
# 检查 iou_list 中 0 值的比例
zero_iou = [x for x in all_ious_including_unmatched if x == 0.0]
print(f"=== IoU=0 的证据数: {len(zero_iou)} ===")
print(f"占定位成功证据的: {len(zero_iou)/n*100:.1f}%")
print(f"占未匹配证据的: {len(zero_iou)/300*100:.1f}%")
print()
print("假设: IoU=0 多因证据文本多次出现, locator取首次但金标是另一次")
print("      或 LLM 输出的证据文本与原文有细微差异(标点/空格)")
