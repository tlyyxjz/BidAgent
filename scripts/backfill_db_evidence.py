"""DB 证据回填：对 tenders 表全量公告跑产品真实管线并入库。

管线与 C/D 组评测完全一致（零新抽取逻辑）：
    call_extraction_llm（LLM 抽取+候选证据）
    -> EvidenceLocator（span 定位，定位不到即拒绝）
    -> FieldValidator（amount/date/project_identifier 确定性校验）
    -> compute_display_grade（grade=low 拒绝）
    -> create_field_with_evidence 入库（extracted_fields + evidence + link）

目的：前端详情/检索页展示真实字段+证据（此前仅 5 条种子数据有字段）。
用法：
    python scripts/backfill_db_evidence.py --concurrency 5
    python scripts/backfill_db_evidence.py --limit 3 --fresh   # 冒烟
checkpoint：_w3_outputs/backfill_db_ckpt.jsonl（按 tender_id 断点续跑）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app.llm.extractor import call_extraction_llm  # noqa: E402
from app.models.database import AsyncSessionLocal  # noqa: E402
from app.models.evidence import ExtractedField  # noqa: E402
from app.models.tender import Tender  # noqa: E402
from app.processors.display_grade import compute_display_grade  # noqa: E402
from app.processors.evidence_locator import EvidenceLocator  # noqa: E402
from app.processors.evidence_repository import (  # noqa: E402
    EvidenceInput,
    FieldInput,
    create_field_with_evidence,
)
from app.processors.field_validator import (  # noqa: E402
    validate_amount,
    validate_date,
    validate_project_identifier,
)

CKPT = ROOT / "_w3_outputs" / "backfill_db_ckpt.jsonl"
# 与 app/api/real_demo.py FIELD_ORDER 对齐
FIELD_ORDER = [
    "project_identifier", "project_name", "purchaser_name", "winner_name",
    "amount", "publish_date", "bid_deadline",
]
_ckpt_lock = asyncio.Lock()


def _support_from_match(match_type: str) -> str:
    if match_type in ("exact", "stripped"):
        return "direct"
    if match_type == "no_punct":
        return "equivalent"
    return "inferred"


def _enum_val(v) -> str:
    return v.value if hasattr(v, "value") else str(v)


def _build_field_inputs(raw_text: str, result) -> list[FieldInput]:
    """从 LLM 抽取结果构建可入库的 FieldInput 列表（D 组语义：验证不过即拒绝）。"""
    locator = EvidenceLocator(raw_text)
    out: list[FieldInput] = []
    for f in result.fields:
        if f.field_name not in FIELD_ORDER:
            continue
        if f.field_status == "absent" or not f.raw_value:
            continue
        # 1) 证据定位：定位不到 -> 拒绝（选择性输出）
        best = None
        for ce in f.candidate_evidences:
            loc = locator.locate(ce.evidence_text, search_from=0)
            if loc.found and loc.location is not None and _enum_val(loc.location.match_type) != "not_found":
                best = loc.location
                break
        if best is None:
            continue
        # 2) 确定性字段校验
        validated, rule = True, "evidence_locate"
        try:
            if f.field_name == "amount":
                validated = validate_amount(f.raw_value, None).valid
                rule = "validate_amount"
            elif f.field_name in ("publish_date", "bid_deadline"):
                validated = validate_date(f.raw_value).valid
                rule = "validate_date"
            elif f.field_name == "project_identifier":
                validated = validate_project_identifier(f.raw_value).valid
                rule = "validate_project_identifier"
        except Exception:
            validated = False
        if not validated:
            continue
        # 3) display_grade 选择性输出
        support = _support_from_match(_enum_val(best.match_type))
        grade = compute_display_grade(support, "official_original", False, f.field_status)
        if grade == "low":
            continue
        ev = EvidenceInput(
            evidence_text=best.text,
            context_before=raw_text[max(0, best.start - 40):best.start],
            context_after=raw_text[best.end:best.end + 40],
            raw_start=best.start,
            raw_end=best.end,
            normalized_start=getattr(best, "normalized_start", -1),
            normalized_end=getattr(best, "normalized_end", -1),
            match_method=_enum_val(best.match_type),
            confidence=best.confidence,
            verified=True,
            verification_rule=rule,
        )
        fi = FieldInput(
            field_name=f.field_name,
            field_status=f.field_status,
            raw_value=f.raw_value,
            normalized_value=f.normalized_value,
            amount_type=f.amount_type,
            currency=f.currency,
            lot_id=f.lot_id,
            original_unit=f.original_unit,
            tax_status=f.tax_status,
            display_precision=f.display_precision,
            support_level=support,
            support_reason=f"match:{ev.match_method}",
            validator_version="v4.1-backfill",
            cross_verify_status="single_source",
            source_quality_snapshot="official_original",
            field_type=f.field_type,
            semantic_role=f.semantic_role,
            value_count=f.value_count,
            evidences=[(ev, "primary")],
        )
        out.append(fi)
    return out


async def _append_ckpt(rec: dict) -> None:
    async with _ckpt_lock:
        with open(CKPT, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


async def process_tender(sem: asyncio.Semaphore, tender_id: int, raw_text: str) -> dict:
    async with sem:
        try:
            result = await call_extraction_llm(raw_text)
        except Exception as e:  # noqa: BLE001
            rec = {"tender_id": tender_id, "status": "llm_error", "error": str(e), "fields_written": 0}
            await _append_ckpt(rec)
            return rec
        if result.error or result.total_tokens == 0 or not result.fields:
            rec = {"tender_id": tender_id, "status": "invalid", "error": result.error, "fields_written": 0}
            await _append_ckpt(rec)
            return rec
        fis = _build_field_inputs(raw_text, result)
        written = 0
        if fis:
            async with AsyncSessionLocal() as db:
                for fi in fis:
                    await create_field_with_evidence(db, tender_id, fi, snapshot_text=raw_text, raw_text=raw_text)
                await db.commit()
            written = len(fis)
        rec = {"tender_id": tender_id, "status": "ok", "fields_written": written,
               "tokens": result.total_tokens, "latency_ms": result.latency_ms}
        await _append_ckpt(rec)
        return rec


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tenders", type=str, default="", help="逗号分隔 tender id 白名单")
    ap.add_argument("--fresh", action="store_true", help="忽略 checkpoint 全量重跑")
    args = ap.parse_args()

    done_ids: set[int] = set()
    if CKPT.exists() and not args.fresh:
        for line in CKPT.read_text(encoding="utf-8").splitlines():
            try:
                done_ids.add(int(json.loads(line)["tender_id"]))
            except Exception:
                pass

    whitelist = {int(x) for x in args.tenders.split(",") if x.strip()} if args.tenders else None

    async with AsyncSessionLocal() as db:
        covered = {r[0] for r in (await db.execute(
            select(ExtractedField.tender_id).distinct())).all()}
        rows = (await db.execute(
            select(Tender.id, Tender.core_content).order_by(Tender.id))).all()

    todo = []
    for tid, core in rows:
        if tid in covered or tid in done_ids:
            continue
        if whitelist is not None and tid not in whitelist:
            continue
        if not core:
            continue
        todo.append((tid, core))
    if args.limit:
        todo = todo[:args.limit]

    print(f"待回填: {len(todo)} 条（已有字段 {len(covered)} 条，checkpoint 已完成 {len(done_ids)} 条）")
    if not todo:
        return

    sem = asyncio.Semaphore(args.concurrency)
    tasks = [process_tender(sem, tid, core) for tid, core in todo]
    results = []
    for i, fut in enumerate(asyncio.as_completed(tasks), 1):
        rec = await fut
        results.append(rec)
        if i % 10 == 0 or i == len(tasks):
            ok = sum(1 for r in results if r["status"] == "ok")
            fld = sum(r["fields_written"] for r in results)
            print(f"进度 {i}/{len(tasks)}  ok={ok}  累计入库字段={fld}")
    ok = sum(1 for r in results if r["status"] == "ok")
    fld = sum(r["fields_written"] for r in results)
    bad = [r for r in results if r["status"] != "ok"]
    print(f"\n完成: ok={ok}/{len(results)}  入库字段={fld}  非ok={len(bad)}")
    for r in bad[:10]:
        print("  ", r)


if __name__ == "__main__":
    asyncio.run(main())
