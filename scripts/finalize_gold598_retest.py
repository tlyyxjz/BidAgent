"""gold598 复测 checkpoint 去重汇总。

双进程并发写 checkpoint 产生了重复记录，本脚本按 document_id 去重
（同一 id 保留最后一条，即最新一次完整运行结果），再汇总 A/B/C/D 指标。

用法: python scripts/finalize_gold598_retest.py [--require-full]
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.eval_ablation import summarize  # noqa: E402
from scripts.eval_ablation_types import GroupResult  # noqa: E402

CKPT_PATH = ROOT / "_w3_outputs" / "gold598_retest_ckpt.jsonl"
OUT_PATH = ROOT / "_w3_outputs" / "gold598_retest.json"
GOLD_PATH = ROOT / "tests" / "fixtures" / "gold" / "gold_dataset_v4.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-full", action="store_true",
                        help="要求唯一 id 覆盖全部 598 篇，否则报错退出")
    args = parser.parse_args()

    # 1. 读取 + 去重（同 id 保留最后一条）
    dedup: dict[str, dict] = {}
    total_lines = 0
    for line in CKPT_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        total_lines += 1
        try:
            rec = json.loads(line)
            dedup[rec["document_id"]] = rec
        except (json.JSONDecodeError, KeyError):
            continue
    all_recs = list(dedup.values())
    print(f"总行数: {total_lines} | 去重后: {len(all_recs)}")

    # 2. 覆盖率校验
    gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    gold_ids = {x["document_id"] for x in gold["annotations"]
                if isinstance(x, dict) and not x.get("_is_meta")}
    missing = gold_ids - set(dedup.keys())
    print(f"金标总数: {len(gold_ids)} | 缺失: {len(missing)}")
    if missing:
        print("缺失样例:", sorted(missing)[:10])
    if args.require_full and missing:
        print("ERROR: 存在未覆盖文档，拒绝汇总（去掉 --require-full 可强制）")
        sys.exit(1)

    # 3. 汇总
    def rebuild(group: str, recs: list[dict]) -> tuple[list[GroupResult], list[dict], list[str]]:
        rows, metas, invalid = [], [], []
        for rec in recs:
            g = rec.get(group, {})
            meta = g.get("meta", {})
            metas.append(meta)
            if meta.get("invalid") or rec.get("fatal_error"):
                invalid.append(rec["document_id"])
                continue
            rows.extend(GroupResult(**r) for r in g.get("rows", []))
        return rows, metas, invalid

    result: dict = {"summaries": {}, "by_source": {}, "docs": len(all_recs),
                    "ckpt_total_lines": total_lines, "missing_docs": sorted(missing)}
    for grp in "ABCD":
        rows, metas, invalid = rebuild(grp, all_recs)
        result["summaries"][grp] = asdict(summarize(grp, rows, metas, invalid))
        result["rows_" + grp] = [asdict(r) for r in rows]

    for src in ("frozen93", "w3", "w4", "w5"):
        sub = [r for r in all_recs if r.get("source") == src]
        if not sub:
            continue
        result["by_source"][src] = {"docs": len(sub)}
        for grp in "ABCD":
            rows, metas, invalid = rebuild(grp, sub)
            s = summarize(grp, rows, metas, invalid)
            result["by_source"][src][grp] = {
                "field_precision": s.field_precision,
                "unjustified_rate": s.unjustified_rate,
                "evidence_precision": s.evidence_precision,
                "null_false_positive_rate": s.null_false_positive_rate,
                "multi_value_f1_avg": s.multi_value_f1_avg,
                "fields_evaluable": s.fields_evaluable,
                "invalid_docs_count": s.invalid_docs_count,
            }

    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 70)
    print("汇总完成")
    print("=" * 70)
    for grp in "ABCD":
        s = result["summaries"][grp]
        print(f"{grp} 组: docs={s['docs_count']} precision={s['field_precision']:.2%} "
              f"unjustified={s['unjustified_rate']:.2%} ev_prec={s['evidence_precision']:.2%} "
              f"null_fp={s['null_false_positive_rate']:.4f} mv_f1={s['multi_value_f1_avg']:.4f} "
              f"invalid={s['invalid_docs_count']}")
    print("\n按来源 (D 组 field_precision):")
    for src, d in result["by_source"].items():
        print(f"  {src:<10} docs={d['docs']:<4} D={d['D']['field_precision']:.2%}")
    print(f"\n结果: {OUT_PATH}")


if __name__ == "__main__":
    main()
