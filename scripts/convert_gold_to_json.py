"""把 eval_real_smoke.py 中手工提取的7篇金标导出为标准 annotation JSON。

来源：标注员A 的 txt 标注工作表（自然语言描述），手工提取字段值，
      在标准原文中自动定位证据偏移，导出为符合 Schema 的 JSON。

用途：作为 W1 双人标注中"标注员A"的产出，供评测脚本加载。
"""
from __future__ import annotations

import json
import os
import sys
import importlib.util as ilu
import types
from pathlib import Path
from datetime import datetime, timezone

PROJECT_ROOT = Path(r"C:\Users\Lenovo\Desktop\BidAgent")
W1_07_DIR = Path(__file__).parent

# 补齐环境变量
os.environ.setdefault("SECRET_KEY", "a" * 64)
os.environ.setdefault("ADMIN_SECRET", "admin123")

# 绕过 backend/__init__.py 的完整初始化链
_be_dir = str(PROJECT_ROOT / "backend")
_be_pkg = types.ModuleType("backend")
_be_pkg.__path__ = [_be_dir]
sys.modules["backend"] = _be_pkg


def _load_mod(name, path):
    spec = ilu.spec_from_file_location(name, path)
    mod = ilu.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_load_mod("backend.enums", _be_dir + r"\enums.py")
_load_mod("backend.schemas", str(W1_07_DIR / "BidAgent_w1_07_fix") + r"\schemas.py")

from backend.enums import GoldStatus  # noqa: E402

# 复用 eval_real_smoke 的 build_gold_docs 和 collect_raw_texts
# 通过 exec 加载（避免 main() 自动执行）
_ns = {"__file__": str(W1_07_DIR / "BidAgent_w1_07_fix" / "eval_real_smoke.py"), "__name__": "__not_main__"}
_eval_path = W1_07_DIR / "BidAgent_w1_07_fix" / "eval_real_smoke.py"
exec(compile(open(_eval_path, encoding="utf-8").read(),
             str(_eval_path), "exec"), _ns)

build_gold_docs = _ns["build_gold_docs"]
collect_raw_texts = _ns["collect_raw_texts"]

# 输出目录
OUT_DIR = Path(r"C:\Users\Lenovo\Desktop\W1-09_金标JSON_标注员A")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def main():
    print("=" * 60)
    print("转换金标 -> JSON")
    print(f"时间: {datetime.now().isoformat()}")
    print("=" * 60)

    gold_docs = build_gold_docs()
    raw_texts = collect_raw_texts()

    print(f"\n金标: {len(gold_docs)} 篇")
    print(f"原文: {len(raw_texts)} 篇")

    now_iso = datetime.now(timezone.utc).isoformat()

    for doc in gold_docs:
        doc_id = doc.document_id
        raw_text = raw_texts.get(doc_id, "")

        # 统计证据命中率
        total_values = 0
        hit_evidence = 0
        for field in doc.fields:
            if not field.values:
                continue
            for v in field.values:
                total_values += 1
                for ev in v.acceptable_evidence_spans:
                    if ev.text and ev.text in raw_text:
                        hit_evidence += 1
                        break

        # 导出 JSON（符合 AnnotationDocument Schema）
        data = {
            "document_id": doc_id,
            "annotator_id": "A",
            "annotation_version": doc.annotation_version,
            "annotation_time": now_iso,
            "fields": [],
        }

        for field in doc.fields:
            f_data = {
                "field_name": field.field_name,
                "gold_status": field.gold_status,
                "values": [],
                "note": field.note or "",
            }
            for v in field.values:
                v_data = {
                    "raw_value": v.raw_value,
                    "normalized_value": v.normalized_value,
                    "amount_type": v.amount_type,
                    "currency": v.currency,
                    "original_unit": v.original_unit,
                    "tax_status": v.tax_status,
                    "lot_id": v.lot_id,
                    "acceptable_evidence_spans": [],
                }
                for ev in v.acceptable_evidence_spans:
                    v_data["acceptable_evidence_spans"].append({
                        "role": ev.role,
                        "start": ev.start,
                        "end": ev.end,
                        "text": ev.text,
                    })
                f_data["values"].append(v_data)
            data["fields"].append(f_data)

        out_path = OUT_DIR / f"annotation_{doc_id}_A.json"
        out_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        hit_rate = f"{hit_evidence}/{total_values}" if total_values > 0 else "N/A"
        print(f"  ✅ {doc_id}: {len(doc.fields)}字段, 证据命中 {hit_rate}, "
              f"-> {out_path.name}")

    print(f"\n导出完成: {OUT_DIR}")
    print(f"共 {len(gold_docs)} 个 JSON 文件")

    # 汇总证据命中率
    print("\n" + "=" * 60)
    print("证据命中统计（自动定位 vs 标准原文）")
    print("=" * 60)
    total_all = 0
    hit_all = 0
    for doc in gold_docs:
        raw_text = raw_texts.get(doc.document_id, "")
        for field in doc.fields:
            if not field.values:
                continue
            for v in field.values:
                total_all += 1
                for ev in v.acceptable_evidence_spans:
                    if ev.text and ev.text in raw_text:
                        hit_all += 1
                        break
                    else:
                        print(f"  ⚠ 未命中: {doc.document_id} / {field.field_name} / "
                              f"raw_value={v.raw_value[:30]} / ev_text={ev.text[:30] if ev.text else 'None'}")
    print(f"\n总命中率: {hit_all}/{total_all} = {hit_all / total_all:.1%}")


if __name__ == "__main__":
    main()
