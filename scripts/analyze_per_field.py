"""对最差篇目做 per-field 详细对比，定位 IoU=0 根因。"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.llm.extractor import call_extraction_llm
from app.processors.evidence_locator import EvidenceLocator

GOLD = ROOT / "tests" / "fixtures" / "gold" / "k3_annotations_batch2.json"
RAW_DIR = ROOT / "_w3_raw"

with open(GOLD, encoding="utf-8") as f:
    gold_data = json.load(f)
gold_by_id = {d["document_id"]: d for d in gold_data if isinstance(d, dict) and not d.get("_is_meta")}


def get_body_offset(raw_text: str) -> int:
    return raw_text.find("## ")


async def analyze_doc(doc_id: str):
    gd = gold_by_id[doc_id]
    raw_path = RAW_DIR / f"{doc_id}.txt"
    raw_text = raw_path.read_text(encoding="utf-8")
    body_offset = get_body_offset(raw_text)
    body = raw_text[body_offset:]

    print(f"\n{'='*70}")
    print(f"分析: {doc_id} ({gd['notice_type']})")
    print(f"body_offset={body_offset}, body长度={len(body)}")
    print(f"{'='*70}")

    # 调 LLM
    result = await call_extraction_llm(raw_text)
    print(f"LLM 输出: {len(result.fields)} 个字段, tokens={result.total_tokens}")

    locator = EvidenceLocator(raw_text)

    # 金标字段
    gold_fields = {f["field_name"]: f for f in gd.get("fields", [])}

    for pred_field in result.fields:
        fname = pred_field.field_name
        gf = gold_fields.get(fname)
        if not gf:
            continue

        gold_status = gf.get("gold_status")
        gold_values = gf.get("values", [])

        print(f"\n--- 字段: {fname} (gold_status={gold_status}) ---")

        if gold_status != "present":
            print(f"  金标非present, 跳过")
            continue

        # 金标 spans
        gold_spans = []
        for v in gold_values:
            for span in v.get("acceptable_evidence_spans", []):
                gold_spans.append((span["start"], span["end"], span["text"]))
        print(f"  金标 spans: {len(gold_spans)}")
        for gs, ge, gt in gold_spans[:3]:
            print(f"    [{gs}:{ge}] {gt[:50]!r}")

        # LLM 候选证据
        pred_evs = pred_field.candidate_evidences or []
        print(f"  LLM 候选证据: {len(pred_evs)}")
        for ce in pred_evs[:3]:
            print(f"    text={ce.evidence_text[:50]!r}")

        # locator 定位
        for ce in pred_evs[:3]:
            loc = locator.locate(ce.evidence_text, search_from=0)
            if loc.found and loc.location:
                abs_start = loc.location.start
                abs_end = loc.location.end
                rel_start = abs_start - body_offset
                rel_end = abs_end - body_offset
                actual_text = raw_text[abs_start:abs_end]

                # 与每个金标 span 比 IoU
                best_iou = 0.0
                best_gold = None
                for gs, ge, gt in gold_spans:
                    inter_s = max(rel_start, gs)
                    inter_e = min(rel_end, ge)
                    inter = max(0, inter_e - inter_s)
                    union = max(rel_end, ge) - min(rel_start, gs)
                    iou = inter / union if union > 0 else 0.0
                    if iou > best_iou:
                        best_iou = iou
                        best_gold = (gs, ge, gt)

                print(f"    locator: abs[{abs_start}:{abs_end}] rel[{rel_start}:{rel_end}]")
                print(f"      actual={actual_text[:50]!r}")
                print(f"      best_gold=[{best_gold[0]}:{best_gold[1]}] {best_gold[2][:50]!r}")
                print(f"      IoU={best_iou:.4f}")

                # 如果 IoU=0，检查是否文本多次出现
                if best_iou == 0:
                    occurrences = []
                    pos = 0
                    while True:
                        idx = body.find(ce.evidence_text, pos)
                        if idx < 0:
                            break
                        occurrences.append(idx)
                        pos = idx + 1
                    print(f"      候选文本在body中出现 {len(occurrences)} 次: {occurrences[:5]}")
                    # 检查金标 span 是否在某个出现位置附近
                    for gs, ge, gt in gold_spans[:2]:
                        for occ in occurrences:
                            if abs(occ - gs) < 50:
                                print(f"      金标span[{gs}] 接近出现位置[{occ}], diff={gs-occ}")


async def main():
    # 分析3篇最差的
    targets = ["w3_award_016", "w3_correction_081", "w3_correction_034"]
    for doc_id in targets:
        await analyze_doc(doc_id)
        await asyncio.sleep(1.0)


if __name__ == "__main__":
    asyncio.run(main())
