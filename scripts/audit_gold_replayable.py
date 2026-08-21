# -*- coding: utf-8 -*-
"""金标可重放审计：全量 present 字段值必须可在原文定位。

审计规则（与评测匹配口径对齐）：
- present 字段至少一个候选值可在原文定位：
  * 直接子串命中，或
  * 日期字段（publish_date/bid_deadline）与原文日期候选归一化匹配，或
  * amount 字段数值（含万/亿单位换算容差）在原文出现
- 支持四种金标值形态：dict.values[].raw_value / dict.values[].value /
  list[].value(str) / list[].value(list[str])（frozen93）

用法:
    python scripts/audit_gold_replayable.py [--json out.json]
退出码: 0=全部可定位, 1=存在不可定位问题
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.eval_ablation_helpers import _date_correct  # noqa: E402

GOLD = ROOT / "tests" / "fixtures" / "gold" / "gold_dataset_v4.json"
RAW_DIRS = {"w3": "_w3_raw", "w4": "_w4_raw", "w5": "_w5_raw", "w6": "_w6_raw"}
DATE_RE = re.compile(
    r"\d{4}年\d{1,2}月\d{1,2}日(?:[ T]?\d{1,2}[:：时]\d{1,2}(?:[:：分]\d{1,2}秒?)?)?")


def field_items(fields):
    """兼容 dict/list 形态，yield (name, spec)。"""
    if isinstance(fields, dict):
        for name, spec in fields.items():
            if isinstance(spec, dict):
                yield name, spec
    else:
        for f in fields or []:
            if isinstance(f, dict) and f.get("field_name"):
                yield f["field_name"], f


def raw_values_of(spec: dict) -> list:
    out = []
    # frozen93 形态：value 键可为 str 或 list[str]
    v0 = spec.get("value")
    if isinstance(v0, str) and v0.strip():
        out.append(v0)
    elif isinstance(v0, list):
        for x in v0:
            if isinstance(x, str) and x.strip():
                out.append(x)
    for v in spec.get("values") or []:
        if isinstance(v, dict):
            rv = v.get("raw_value") or v.get("value")
            if rv:
                out.append(rv)
        elif isinstance(v, str) and v.strip():
            out.append(v)
    return out


def status_of(spec: dict) -> str:
    return spec.get("status") or spec.get("gold_status") or "absent"


def amount_locatable(val: str, raw: str) -> bool:
    m = re.search(r"(\d[\d,]*\.?\d*)", val.replace(",", ""))
    if not m:
        return False
    num = m.group(1).replace(",", "")
    if num in raw.replace(",", ""):
        return True
    # 万元/亿元单位换算容差（与评测 _amount_correct 口径对齐）
    try:
        f = float(num)
    except ValueError:
        return False
    for unit, factor in (("亿", 1e8), ("万", 1e4)):
        if unit in val:
            target = f * factor
            for cand in re.findall(r"\d[\d,]*\.?\d*", raw.replace(",", "")):
                try:
                    if abs(float(cand) - target) < max(0.5, target * 1e-6):
                        return True
                except ValueError:
                    continue
    return False


def audit() -> tuple[int, int, list]:
    """返回 (文档数, present 字段数, 问题清单)。"""
    data = json.loads(GOLD.read_text(encoding="utf-8"))
    ann = [i for i in data["annotations"]
           if isinstance(i, dict) and "document_id" in i]
    problems = []
    total_present = 0
    for item in ann:
        did = item["document_id"]
        fname = item.get("file") or f"{did}.txt"
        prefix = did.split("_")[0]
        p = ROOT / RAW_DIRS.get(prefix, "_w2_raw") / fname
        if not p.exists():
            problems.append({"document_id": did, "field": "*", "reason": "原文文件缺失"})
            continue
        raw = p.read_text(encoding="utf-8")
        for name, spec in field_items(item.get("fields")):
            if status_of(spec) != "present":
                continue
            total_present += 1
            vals = raw_values_of(spec)
            if not vals:
                problems.append({"document_id": did, "field": name,
                                 "reason": "present 但无值"})
                continue
            ok = False
            for v in vals:
                if v in raw:
                    ok = True
                    break
                if name in ("publish_date", "bid_deadline"):
                    if any(_date_correct(v, c) for c in DATE_RE.findall(raw)):
                        ok = True
                        break
                elif name == "amount":
                    if amount_locatable(v, raw):
                        ok = True
                        break
            if not ok:
                problems.append({"document_id": did, "field": name,
                                 "reason": f"值不可定位: {vals[0][:40]}"})
    return len(ann), total_present, problems


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default=None, help="问题清单输出 JSON 路径")
    args = parser.parse_args()
    docs, present, problems = audit()
    print(f"审计文档 {docs} 篇 | present 字段 {present} 个 | 问题 {len(problems)} 个")
    for pr in problems:
        print(f"  {pr['document_id']} | {pr['field']} | {pr['reason']}")
    if args.json:
        Path(args.json).write_text(json.dumps(problems, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
