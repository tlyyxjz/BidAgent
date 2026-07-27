"""修复 04/05/06 award 的 amount 字段证据偏移量

问题：原金标 amount 字段的证据全部标为 start=0, end=1, text='中'
修复：根据原文中实际金额位置修正偏移量和证据文本
"""
import json
from pathlib import Path

ANNOT_DIR = Path(r"C:\Users\Lenovo\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\work-mode-projects\6a57291a0778ce48bfe693d2\_w2_annotations")
RAW_DIR = Path(r"C:\Users\Lenovo\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\work-mode-projects\6a57291a0778ce48bfe693d2\_w2_raw")

# 修正映射：doc_id -> [(raw_value_old, raw_value_new, start, end, evidence_text)]
fixes = {
    "04_award_001": [
        ("386.8648万", "386.864800", 301, 311, "386.864800"),
    ],
    "05_award_002": [
        ("2.1万", "2.100000", 442, 450, "2.100000"),
        ("5.7837万", "5.783700", 512, 520, "5.783700"),
        ("15.3972万", "15.397200", 593, 602, "15.397200"),
        ("10.1328万", "10.132800", 685, 694, "10.132800"),
        ("123.8958万", "123.895800", 756, 766, "123.895800"),
        ("218.94万", "218.940000", 847, 857, "218.940000"),
    ],
    "06_award_003": [
        ("177.449万", "177.449000", 375, 385, "177.449000"),
        ("103.155万", "103.155000", 468, 478, "103.155000"),
        ("122.0055万", "122.005500", 561, 571, "122.005500"),
        ("114万", "114.000000", 635, 645, "114.000000"),
    ],
}

for doc_id, fix_list in fixes.items():
    # 找金标文件
    matches = list(ANNOT_DIR.glob(f"annotation_{doc_id}*.json"))
    if not matches:
        print(f"{doc_id}: file not found")
        continue
    annot_file = matches[0]

    # 读取原文验证
    raw_path = RAW_DIR / f"{doc_id}.txt"
    with open(raw_path, encoding="utf-8") as f:
        raw = f.read()

    # 读取金标
    with open(annot_file, encoding="utf-8") as f:
        gold = json.load(f)

    # 验证所有修正位置
    print(f"\n=== {doc_id} ===")
    all_verified = True
    for old_rv, new_rv, s, e, ev_text in fix_list:
        actual = raw[s:e]
        if actual != ev_text:
            print(f"  VERIFY FAIL: raw[{s}:{e}]={repr(actual)}, expected={repr(ev_text)}")
            all_verified = False
        else:
            print(f"  VERIFY OK: raw[{s}:{e}]={repr(actual)}")

    if not all_verified:
        print(f"  SKIP: verification failed")
        continue

    # 修复 amount 字段
    for field in gold["fields"]:
        if field["field_name"] == "amount" and field["gold_status"] in ("present", "multi_value"):
            for i, v in enumerate(field["values"]):
                old_rv = v["raw_value"]
                # 找对应的修正
                for old_rv_match, new_rv, s, e, ev_text in fix_list:
                    if old_rv == old_rv_match:
                        v["raw_value"] = new_rv
                        v["acceptable_evidence_spans"] = [{
                            "role": "primary",
                            "start": s,
                            "end": e,
                            "text": ev_text,
                        }]
                        print(f"  FIXED value[{i}]: {repr(old_rv)} -> {repr(new_rv)}, span {s}:{e}")
                        break
            # 添加修正说明
            field["note"] = f"修正：原金标 amount 证据偏移量全部错误(start=0,end=1,text=中)。2026-07-27 修正为原文实际金额位置。"

    # 写回文件
    with open(annot_file, "w", encoding="utf-8") as f:
        json.dump(gold, f, ensure_ascii=False, indent=2)
    print(f"  SAVED: {annot_file.name}")

print("\n修复完成。")
