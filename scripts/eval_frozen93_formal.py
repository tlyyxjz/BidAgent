"""正式冻结复测：gold_frozen_v1.json（2026-07-30 冻结, 93 篇 test 集）A/B/C/D。

与 scripts/eval_gold598_retest.py 口径完全一致（同一套 run_group_* / summarize），
区别仅在于：
- 数据源锁定 tests/fixtures/gold/gold_frozen_v1.json（冻结后零改动）
- 独立 checkpoint / 输出文件，不与 598 诊断性复测混用

用途：v4.1 §10 要求的 test-only 冻结运行，产出正式对外评测数字。

用法:
    python scripts/eval_frozen93_formal.py [--concurrency 5] [--limit N] [--fresh]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.eval_ablation import summarize  # noqa: E402
from scripts.eval_ablation_groups import (  # noqa: E402
    run_group_a, run_group_b, run_group_c, run_group_d,
)
from scripts.eval_ablation_types import GoldDoc, GoldField  # noqa: E402
from scripts.eval_gold598_retest import normalize_fields  # noqa: E402  复用三种金标形态归一化

GOLD_PATH = ROOT / "tests" / "fixtures" / "gold" / "gold_frozen_v1.json"
OUT_DIR = ROOT / "_w3_outputs"
CKPT_PATH = OUT_DIR / "frozen93_formal_ckpt.jsonl"
OUT_PATH = OUT_DIR / "frozen93_formal.json"


def load_dataset() -> list[tuple[GoldDoc, str]]:
    """加载冻结金标（93 篇），读取 _w2_raw 原文。"""
    data = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    out: list[tuple[GoldDoc, str]] = []
    for ann in data["annotations"]:
        did = ann.get("document_id")
        if not did:
            continue
        fname = ann.get("file") or (did + ".md")
        fields = normalize_fields(ann.get("fields", {}))
        gd = GoldDoc(document_id=did, file=fname, fields=fields)
        # 原文目录按前缀路由（与 eval_gold598_retest 一致）
        if fname.startswith("w3_"):
            raw_dir = ROOT / "_w3_raw"
        elif fname.startswith("w4_"):
            raw_dir = ROOT / "_w4_raw"
        elif fname.startswith("w5_"):
            raw_dir = ROOT / "_w5_raw"
        else:
            raw_dir = ROOT / "_w2_raw"
        p = raw_dir / fname
        if not p.exists():
            print(f"[WARN] 原文缺失: {fname} ({did})")
            continue
        out.append((gd, p.read_text(encoding="utf-8")))
    return out


def load_checkpoint() -> dict[str, dict]:
    done: dict[str, dict] = {}
    if CKPT_PATH.exists():
        for line in CKPT_PATH.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                done[rec["document_id"]] = rec
            except (json.JSONDecodeError, KeyError):
                continue
    return done


async def process_doc(gd: GoldDoc, rt: str) -> dict:
    rec: dict = {"document_id": gd.document_id, "source": "frozen93"}
    rows_a, meta_a = await run_group_a(gd, rt)
    rec["A"] = {"rows": [asdict(r) for r in rows_a], "meta": meta_a}
    rows_b, meta_b = await run_group_b(gd, rt)
    rec["B"] = {"rows": [asdict(r) for r in rows_b], "meta": meta_b}
    rows_c, meta_c = await run_group_c(gd, rt)
    rec["C"] = {"rows": [asdict(r) for r in rows_c], "meta": meta_c}
    if meta_c.get("invalid"):
        rows_d, meta_d = [], meta_c
    else:
        rows_d, meta_d = await run_group_d(rows_c, meta_c)
    rec["D"] = {"rows": [asdict(r) for r in rows_d], "meta": meta_d}
    return rec


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 篇 (冒烟用)")
    parser.add_argument("--fresh", action="store_true", help="忽略 checkpoint 从头跑")
    args = parser.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    if args.fresh and CKPT_PATH.exists():
        CKPT_PATH.unlink()

    dataset = load_dataset()
    if args.limit:
        dataset = dataset[: args.limit]

    done = load_checkpoint()
    todo = [d for d in dataset if d[0].document_id not in done]
    print("=" * 70)
    print("正式冻结复测 (test-only): gold_frozen_v1.json A/B/C/D")
    print(f"数据集: {len(dataset)} 篇 | 已完成(checkpoint): {len(done)} | 待跑: {len(todo)}")
    print(f"并发: {args.concurrency}")
    print("=" * 70, flush=True)

    sem = asyncio.Semaphore(args.concurrency)
    ckpt_lock = asyncio.Lock()
    t0 = time.time()
    finished = len(done)
    total = len(dataset)

    async def worker(gd: GoldDoc, rt: str) -> None:
        nonlocal finished
        async with sem:
            try:
                rec = await process_doc(gd, rt)
            except Exception as e:  # noqa: BLE001
                rec = {"document_id": gd.document_id, "source": "frozen93", "fatal_error": str(e)}
            async with ckpt_lock:
                with open(CKPT_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                finished += 1
                if finished % 5 == 0 or finished == total:
                    el = time.time() - t0
                    rate = (finished - len(done)) / max(el, 1)
                    eta = (total - finished) / rate if rate > 0 else 0
                    print(f"[{finished}/{total}] {gd.document_id} "
                          f"elapsed={el/60:.1f}min eta={eta/60:.1f}min", flush=True)

    await asyncio.gather(*(worker(gd, rt) for gd, rt in todo))

    # ---- 汇总 ----
    all_recs = list(load_checkpoint().values())
    from scripts.eval_ablation_types import GroupResult

    def rebuild(group: str, recs: list[dict]):
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

    result: dict = {
        "task": "frozen93_formal",
        "gold_file": "tests/fixtures/gold/gold_frozen_v1.json",
        "gold_frozen_at": json.loads(GOLD_PATH.read_text(encoding='utf-8')).get("frozen_at"),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "summaries": {},
        "docs": len(all_recs),
    }
    for grp in "ABCD":
        rows, metas, invalid = rebuild(grp, all_recs)
        result["summaries"][grp] = asdict(summarize(grp, rows, metas, invalid))
        result["rows_" + grp] = [asdict(r) for r in rows]

    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 70)
    print("正式冻结复测完成 - 汇总")
    print("=" * 70)
    for grp in "ABCD":
        s = result["summaries"][grp]
        print(f"{grp} 组: precision={s['field_precision']:.2%} unjustified={s['unjustified_rate']:.2%} "
              f"ev_prec={s['evidence_precision']:.2%} null_fp={s['null_false_positive_rate']:.4f} "
              f"mv_f1={s['multi_value_f1_avg']:.4f} invalid={s['invalid_docs_count']}")
    print(f"\n结果: {OUT_PATH}")
    print(f"耗时: {(time.time()-t0)/60:.1f} 分钟 (本轮增量)")


if __name__ == "__main__":
    asyncio.run(main())
