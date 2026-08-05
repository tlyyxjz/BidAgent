"""金标 v4 合集 598 篇全量复测（A/B/C/D 四组消融）。

口径与 scripts/eval_ablation.py 完全一致（复用同一套 run_group_* / summarize），
仅数据源换成 tests/fixtures/gold/gold_dataset_v4.json（598 篇合集）。

用法:
    python scripts/eval_gold598_retest.py [--concurrency 5] [--limit N] [--docs w3_tender_001 ...]

特性:
    - 并发: asyncio.Semaphore 控制 LLM 并发数 (默认 5)
    - 断点续跑: 每篇完成即写 checkpoint JSONL, 重跑自动跳过已完成 document_id
    - 分组汇总: 全局 + 按来源 (frozen93/w3/w4/w5) 分别 summarize
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

from scripts.eval_ablation import summarize  # noqa: E402  (必须先导入, groups 依赖 sys.modules)
from scripts.eval_ablation_groups import (  # noqa: E402
    run_group_a, run_group_b, run_group_c, run_group_d,
)
from scripts.eval_ablation_types import GoldDoc, GoldField  # noqa: E402

GOLD_PATH = ROOT / "tests" / "fixtures" / "gold" / "gold_dataset_v4.json"
OUT_DIR = ROOT / "_w3_outputs"
CKPT_PATH = OUT_DIR / "gold598_retest_ckpt.jsonl"
OUT_PATH = OUT_DIR / "gold598_retest.json"


def source_of(doc_id: str, fname: str) -> str:
    if fname.startswith("w3_"):
        return "w3"
    if fname.startswith("w4_"):
        return "w4"
    if fname.startswith("w5_"):
        return "w5"
    return "frozen93"


def _norm_values(vals) -> list[dict]:
    """把三种金标值形态归一为 [{"raw_value": str}]。"""
    out: list[dict] = []
    if isinstance(vals, list):
        for v in vals:
            if isinstance(v, dict):
                if v.get("raw_value"):
                    out.append({"raw_value": v["raw_value"]})
            elif isinstance(v, str) and v.strip():
                out.append({"raw_value": v})
    elif isinstance(vals, str) and vals.strip():
        out.append({"raw_value": vals})
    return out


def normalize_fields(fields_raw) -> list[GoldField]:
    """归一化三种金标 fields 形态:

    A) list[{field_name, gold_status, values:[{raw_value,...}]}]  (w3/batch2)
    B) dict{field_name: {status, values:[str,...]}}               (w4/w5)
    C) list[{field_name, gold_status, value: str|list}]           (frozen93)
    """
    gfs: list[GoldField] = []
    if isinstance(fields_raw, dict):
        for name, spec in fields_raw.items():
            if not isinstance(spec, dict):
                continue
            status = spec.get("status") or spec.get("gold_status") or "absent"
            vals = _norm_values(spec.get("values"))
            gfs.append(GoldField(field_name=name, gold_status=status, values=vals))
        return gfs
    for f in fields_raw or []:
        if not isinstance(f, dict):
            continue
        name = f.get("field_name")
        status = f.get("gold_status") or f.get("status") or "absent"
        vals = _norm_values(f.get("values") if "values" in f else f.get("value"))
        gfs.append(GoldField(field_name=name, gold_status=status, values=vals))
    return gfs


def load_dataset() -> list[tuple[GoldDoc, str, str]]:
    """返回 [(GoldDoc, raw_text, source)]。"""
    data = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    ann = data["annotations"]
    out: list[tuple[GoldDoc, str, str]] = []
    for item in ann:
        if not isinstance(item, dict) or item.get("_is_meta"):
            continue
        fields = normalize_fields(item.get("fields"))
        gd = GoldDoc(document_id=item["document_id"], file=item.get("file", ""), fields=fields)
        fname = gd.file
        if fname.startswith("w3_"):
            raw_dir, src = ROOT / "_w3_raw", "w3"
        elif fname.startswith("w4_"):
            raw_dir, src = ROOT / "_w4_raw", "w4"
        elif fname.startswith("w5_"):
            raw_dir, src = ROOT / "_w5_raw", "w5"
        else:
            raw_dir, src = ROOT / "_w2_raw", "frozen93"
        p = raw_dir / fname
        if not p.exists():
            print(f"[WARN] 原文缺失: {fname} ({gd.document_id})")
            continue
        out.append((gd, p.read_text(encoding="utf-8"), src))
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


async def process_doc(gd: GoldDoc, rt: str, src: str) -> dict:
    """单篇跑 A/B/C/D 四组, 返回记录。"""
    rec: dict = {"document_id": gd.document_id, "source": src}

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
    parser.add_argument("--docs", nargs="*", default=None, help="只跑指定 document_id")
    parser.add_argument("--fresh", action="store_true", help="忽略 checkpoint 从头跑")
    args = parser.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    if args.fresh and CKPT_PATH.exists():
        CKPT_PATH.unlink()

    dataset = load_dataset()
    if args.docs:
        wanted = set(args.docs)
        dataset = [d for d in dataset if d[0].document_id in wanted]
    if args.limit:
        dataset = dataset[: args.limit]

    done = load_checkpoint()
    todo = [d for d in dataset if d[0].document_id not in done]
    print("=" * 70)
    print("金标 v4 合集 598 篇全量复测 (A/B/C/D)")
    print(f"数据集: {len(dataset)} 篇 | 已完成(checkpoint): {len(done)} | 待跑: {len(todo)}")
    print(f"并发: {args.concurrency}")
    print("=" * 70, flush=True)

    sem = asyncio.Semaphore(args.concurrency)
    ckpt_lock = asyncio.Lock()
    t0 = time.time()
    finished = len(done)
    total = len(dataset)

    async def worker(gd: GoldDoc, rt: str, src: str) -> None:
        nonlocal finished
        async with sem:
            try:
                rec = await process_doc(gd, rt, src)
            except Exception as e:  # noqa: BLE001
                rec = {"document_id": gd.document_id, "source": src, "fatal_error": str(e)}
            async with ckpt_lock:
                with open(CKPT_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                finished += 1
                if finished % 10 == 0 or finished == total:
                    el = time.time() - t0
                    rate = (finished - len(done)) / max(el, 1)
                    eta = (total - finished) / rate if rate > 0 else 0
                    print(f"[{finished}/{total}] {gd.document_id} "
                          f"elapsed={el/60:.1f}min eta={eta/60:.1f}min", flush=True)

    await asyncio.gather(*(worker(gd, rt, src) for gd, rt, src in todo))

    # ---- 汇总 ----
    all_recs = list(load_checkpoint().values())
    from scripts.eval_ablation_types import GroupResult

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

    result: dict = {"summaries": {}, "by_source": {}, "docs": len(all_recs)}
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
            }

    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 70)
    print("复测完成 - 汇总")
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
