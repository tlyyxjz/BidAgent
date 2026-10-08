# -*- coding: utf-8 -*-
"""生成 BidAgent「证据可溯源」自包含演示页（离线可开 / 可挂 Pages）。

数据来源：
  - 抽取字段与证据偏移：examples/*.json（真实采集公告，ccgp.gov.cn）
  - 原文快照：data/bidagent.db 的 tenders.core_content
                （偏移量即以 core_content 为坐标基准，生成前逐条校验）

输出：demo_evidence.html（内嵌全部数据，零外部依赖、零网络请求）
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "bidagent.db"
OUT = ROOT / "demo_evidence.html"

SAMPLES = [
    ("examples/01_tender_sample.json", "招标公告", "tender"),
    ("examples/02_award_sample.json", "中标公告", "award"),
    ("examples/03_correction_sample.json", "更正公告", "correction"),
]

FIELD_CN = {
    "project_identifier": "项目编号",
    "purchaser_name": "采购单位",
    "winner_name": "中标供应商",
    "amount": "金额",
    "publish_date": "公告时间",
    "bid_deadline": "投标截止时间",
    "agency": "代理机构",
    "location": "地区",
}
GRADE_CN = {"review": "需人工复核", "auto": "可自动采信", "reject": "已剔除"}


def load_docs() -> list[dict]:
    con = sqlite3.connect(DB)
    cur = con.cursor()
    docs = []
    for rel, label, ntype in SAMPLES:
        d = json.loads((ROOT / rel).read_text(encoding="utf-8"))
        n = d["notice"]
        tid = n["tender_id"]
        cur.execute("select core_content, source_raw_text from tenders where id = ?", (tid,))
        row = cur.fetchone()
        if not row:
            raise SystemExit(f"tender {tid} not found in db")
        core, srt = row
        core = core or ""

        # 证据：以 field_name 建索引，并校验偏移
        ev_by_field: dict[str, list[dict]] = {}
        checks = []
        for e in d.get("evidence", []):
            s, en = e["raw_start"], e["raw_end"]
            sl = core[s:en]
            checks.append((e["id"], sl == e["evidence_text"]))
            ev_by_field.setdefault(e["field_name"], []).append(
                {
                    "start": s,
                    "end": en,
                    "text": e["evidence_text"],
                    "verified": bool(e.get("verified")),
                    "match_method": e.get("match_method"),
                    "confidence": e.get("confidence"),
                }
            )
        bad = [c for c in checks if not c[1]]
        if bad:
            raise SystemExit(f"tender {tid} 偏移校验失败: {bad}")

        fields = []
        for f in d.get("extracted_fields", []):
            fname = f["field_name"]
            fields.append(
                {
                    "name": fname,
                    "label": FIELD_CN.get(fname, fname),
                    "raw_value": f.get("raw_value"),
                    "normalized_value": f.get("normalized_value"),
                    "amount_type": f.get("amount_type"),
                    "currency": f.get("currency"),
                    "display_grade": f.get("display_grade"),
                    "grade_cn": GRADE_CN.get(f.get("display_grade"), f.get("display_grade")),
                    "support_level": f.get("support_level"),
                    "evidences": ev_by_field.get(fname, []),
                }
            )

        docs.append(
            {
                "id": tid,
                "label": label,
                "notice_type": ntype,
                "project_name": n.get("project_name"),
                "tender_org": n.get("tender_org"),
                "agency": n.get("agency"),
                "location": n.get("location"),
                "bid_number": n.get("bid_number"),
                "publish_time": n.get("publish_time"),
                "source_platform": n.get("source_platform"),
                "source_url": n.get("source_url"),
                "raw": core,
                "raw_len": len(core),
                "fields": fields,
                "evidence_total": sum(len(f["evidences"]) for f in fields),
            }
        )
        print(f"[ok] tender {tid} {label}: {len(fields)} 字段 / {sum(len(f['evidences']) for f in fields)} 证据 / 原文 {len(core)} 字")
    con.close()
    return docs


def build_html(docs: list[dict]) -> str:
    payload = json.dumps(docs, ensure_ascii=False)
    stats = {
        "docs": 3,
        "fields": sum(len(d["fields"]) for d in docs),
        "evidence": sum(d["evidence_total"] for d in docs),
    }
    tpl = (ROOT / "scripts" / "evidence_demo_template.html").read_text(encoding="utf-8")
    return (
        tpl.replace("/*__DATA__*/", payload)
        .replace("__DOC_N__", str(stats["docs"]))
        .replace("__FIELD_N__", str(stats["fields"]))
        .replace("__EV_N__", str(stats["evidence"]))
    )


if __name__ == "__main__":
    docs = load_docs()
    OUT.write_text(build_html(docs), encoding="utf-8")
    print(f"\nwrote {OUT} ({OUT.stat().st_size / 1024:.1f} KB)")
