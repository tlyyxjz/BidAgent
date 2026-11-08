# -*- coding: utf-8 -*-
"""大件五（证据再加固·链条）：项目生命周期链。

按 bid_number 把同一项目的公告串成链：招标(tender) → 更正(correction)
→ 中标(award) → 合同(contract)。每一环节附 SHA-256 存证、入库时间、
来源链接与重放命令——从"单点核验"升级为"链条可审计"。

诚实声明：链条只陈述库内观察到的环节；缺失环节可能是"未发生"
也可能是"库未覆盖"，报告里明确区分不下结论。

用法：python scripts/lifecycle_chain.py --bid-number XXX [--db PATH] [--json]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "bidagent.db"

# 生命周期阶段排序（未知类型排最后但保留）
STAGE_ORDER = {"tender": 1, "correction": 2, "award": 3, "contract": 4}
STAGE_LABEL = {"tender": "招标", "correction": "更正", "award": "中标",
               "contract": "合同"}


def _normalize_bid_number(value: str) -> str:
    return "".join(str(value or "").split()).upper()


def _has_evidence_cols(cur) -> bool:
    cols = [r[1] for r in cur.execute("PRAGMA table_info(tenders)")]
    return "content_sha256" in cols


def build_lifecycle_chain(bid_number: str, db_path: Path = DEFAULT_DB) -> dict:
    """按编号构建生命周期链。"""
    target = _normalize_bid_number(bid_number)
    if not target:
        return {"bid_number": bid_number, "stages": [],
                "status": "cannot_build", "reason": "未提供项目编号",
                "report_text": ""}

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    with_evidence = _has_evidence_cols(cur)
    sha_cols = ", content_sha256, raw_text_sha256" if with_evidence else ""
    rows = cur.execute(
        "SELECT id, project_name, bid_number, notice_type, publish_time, "
        f"created_at, source_url, win_company, win_amount{sha_cols} "
        "FROM tenders WHERE bid_number IS NOT NULL",
    ).fetchall()
    conn.close()

    matched = []
    for r in rows:
        if _normalize_bid_number(r[2]) == target:
            # r: id, project_name, bid_number, notice_type, publish_time,
            #    created_at, source_url, win_company, win_amount[, sha×2]
            stage_type = (r[3] or "unknown").lower()
            stage = {
                "row_id": r[0],
                "project_name": r[1],
                "notice_type": stage_type,
                "stage_label": STAGE_LABEL.get(stage_type, stage_type),
                "stage_order": STAGE_ORDER.get(stage_type, 99),
                "publish_time": r[4],
                "ingested_at": r[5],
                "source_url": r[6],
                "win_company": r[7],
                "win_amount": r[8],
                "content_sha256": (r[9] if with_evidence else None),
                "raw_text_sha256": (r[10] if with_evidence else None),
                "replay_cmd": f"python scripts/replay_evidence.py --id {r[0]}",
            }
            matched.append(stage)
    matched.sort(key=lambda s: (s["stage_order"], s["publish_time"] or "",
                                s["row_id"]))

    if not matched:
        return {
            "bid_number": bid_number, "stages": [],
            "status": "not_found",
            "reason": "库内未见该编号的任何公告（无法证伪，需人工补查）",
            "report_text": _render(bid_number, [], [], with_evidence),
        }

    kinds = [s["notice_type"] for s in matched]
    gaps = []
    for key, label in STAGE_LABEL.items():
        if key not in kinds:
            gaps.append(f"未观察到{label}公告（可能未发生，也可能库未覆盖）")

    return {
        "bid_number": bid_number,
        "stages": matched,
        "status": "chained",
        "chain_kinds": sorted(set(kinds)),
        "gaps": gaps,
        "evidence_enabled": with_evidence,
        "report_text": _render(bid_number, matched, gaps, with_evidence),
    }


def _render(bid_number: str, stages: list, gaps: list,
            with_evidence: bool) -> str:
    line = "=" * 62
    out = [line, f"项目生命周期链 · 编号 {bid_number}", line]
    if not stages:
        out.append("库内未见该编号的任何公告（无法证伪，需人工补查）。")
        out.append(line)
        return "\n".join(out)

    for i, s in enumerate(stages, 1):
        out.append(
            f"{i}. [{s['stage_label']}] {s['project_name']}"
        )
        if s.get("publish_time"):
            out.append(f"   发布时间：{s['publish_time']}")
        if s.get("ingested_at"):
            out.append(f"   入库时间：{s['ingested_at']}")
        if s.get("win_company"):
            out.append(f"   中标人：{s['win_company']}")
        if s.get("win_amount") is not None:
            out.append(f"   中标金额：{s['win_amount']}")
        if s.get("source_url"):
            out.append(f"   来源链接：{s['source_url']}")
        if with_evidence and s.get("content_sha256"):
            out.append(f"   存证 SHA-256：{s['content_sha256']}")
            out.append(f"   重放命令：{s['replay_cmd']}")
        elif with_evidence:
            out.append("   存证 SHA-256：（该记录无正文，未存证）")

    if gaps:
        out.append("")
        out.append("── 诚实声明 ──")
        for g in gaps:
            out.append(f"  · {g}")
    out.append(line)
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="项目生命周期链")
    ap.add_argument("--bid-number", required=True, help="项目编号")
    ap.add_argument("--db", default=None, help="DB 路径（默认 data/bidagent.db）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()
    db_path = Path(args.db) if args.db else DEFAULT_DB
    if not db_path.exists():
        print(f"DB 不存在: {db_path}", file=sys.stderr)
        sys.exit(1)
    result = build_lifecycle_chain(args.bid_number, db_path)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["report_text"])


if __name__ == "__main__":
    main()
