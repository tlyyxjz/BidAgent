"""批量验证所有 22 篇金标的证据偏移量"""
import json
import os
from pathlib import Path

WORK_DIR = Path(r"C:\Users\Lenovo\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\work-mode-projects\6a57291a0778ce48bfe693d2")
RAW_DIR = WORK_DIR / "_w2_raw"
ANNOT_DIR = WORK_DIR / "_w2_annotations"


def check_one(annotation_file: Path) -> dict:
    """检查一篇金标的所有偏移量"""
    with open(annotation_file, encoding="utf-8") as f:
        gold = json.load(f)
    doc_id = gold["document_id"]

    # 找原文：尝试多种匹配方式
    raw_path = None
    # 方式1: document_id.txt (W2 新金标)
    p1 = RAW_DIR / f"{doc_id}.txt"
    if p1.exists():
        raw_path = p1
    # 方式2: 从 annotation 文件名推断
    if raw_path is None:
        # annotation_07_correction_001_A.json -> 07_correction_001
        # annotation_award_05_43a28fb1c8af.json -> award_05
        # annotation_tender_06_4e47868721c5_1784968050520.json -> tender_06
        fname = annotation_file.stem  # 去掉 .json
        if fname.startswith("annotation_"):
            fname = fname[len("annotation_"):]
        # 去掉 _A/_B 后缀
        if fname.endswith("_A") or fname.endswith("_B"):
            fname = fname[:-2]
        # 去掉 _数字时间戳后缀
        import re
        fname = re.sub(r"_\d{10,}$", "", fname)
        # 去掉 _hash 后缀 (8位以上十六进制)
        fname = re.sub(r"_[0-9a-f]{8,}$", "", fname)
        p2 = RAW_DIR / f"{fname}.txt"
        if p2.exists():
            raw_path = p2
    # 方式3: 用金标中第一个证据文本搜索所有原文
    if raw_path is None:
        for field in gold["fields"]:
            if field["gold_status"] in ("present", "multi_value") and field["values"]:
                first_span_text = field["values"][0]["acceptable_evidence_spans"][0]["text"]
                for raw_file in RAW_DIR.glob("*.txt"):
                    content = raw_file.read_text(encoding="utf-8")
                    if first_span_text in content:
                        raw_path = raw_file
                        break
                break
    if raw_path is None:
        return {"doc_id": doc_id, "error": f"raw file not found for {doc_id}"}

    with open(raw_path, encoding="utf-8") as f:
        raw = f.read()

    issues = []
    field_count = 0
    span_count = 0
    ok_count = 0

    for field in gold["fields"]:
        if field["gold_status"] in ("present", "multi_value"):
            field_count += 1
            for v in field["values"]:
                rv = v["raw_value"]
                # 检查 raw_value 是否在原文中
                rv_pos = raw.find(rv)
                if rv_pos < 0:
                    issues.append({
                        "field": field["field_name"],
                        "issue": "raw_value_not_in_raw",
                        "raw_value": rv,
                    })

                for span in v["acceptable_evidence_spans"]:
                    span_count += 1
                    s, e = span["start"], span["end"]
                    gold_text = span["text"]
                    if e > len(raw):
                        issues.append({
                            "field": field["field_name"],
                            "issue": "offset_out_of_range",
                            "start": s, "end": e, "len_raw": len(raw),
                        })
                        continue
                    actual = raw[s:e]
                    if actual == gold_text:
                        ok_count += 1
                    else:
                        issues.append({
                            "field": field["field_name"],
                            "issue": "text_mismatch",
                            "start": s, "end": e,
                            "gold_text": gold_text,
                            "actual": actual,
                        })

    return {
        "doc_id": doc_id,
        "raw_file": raw_path.name,
        "raw_len": len(raw),
        "field_count": field_count,
        "span_count": span_count,
        "ok_count": ok_count,
        "issues": issues,
        "all_ok": len(issues) == 0,
    }


# 检查所有金标
results = []
for annot_file in sorted(ANNOT_DIR.glob("annotation_*.json")):
    r = check_one(annot_file)
    results.append(r)

# 输出汇总
print("=" * 80)
print(f"共检查 {len(results)} 篇金标")
print("=" * 80)

problem_docs = []
for r in results:
    if r.get("error"):
        print(f"\n[ERROR] {r['doc_id']}: {r['error']}")
        continue
    status = "OK" if r["all_ok"] else "PROBLEM"
    print(f"\n[{status}] {r['doc_id']}")
    print(f"  原文: {r['raw_file']} (len={r['raw_len']})")
    print(f"  字段数: {r['field_count']}, 证据数: {r['span_count']}, OK: {r['ok_count']}")
    if not r["all_ok"]:
        problem_docs.append(r)
        for issue in r["issues"]:
            print(f"  ISSUE: {issue}")

print("\n" + "=" * 80)
print(f"问题金标数: {len(problem_docs)} / {len(results)}")
if problem_docs:
    print("问题篇目:")
    for r in problem_docs:
        print(f"  - {r['doc_id']}: {len(r['issues'])} 个问题")
