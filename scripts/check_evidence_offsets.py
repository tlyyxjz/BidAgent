# -*- coding: utf-8 -*-
"""证据偏移一致性回归门禁。

背景（2026-10-08 实测）：
    库中 evidence 的 [raw_start, raw_end) 以 tenders.core_content 为坐标基准。
    对 154 篇有证据的公告做逐条切片比对，发现两类情况：
      · 149 篇「文档与证据同源」：537/537 条切片逐字一致（100%）；
      · 5 篇（id=1..5）的 core_content 在 2026-08-05 被重新抓取覆盖成了**别的公告**，
        其 42 条证据的 evidence_text 已不在任何字段中 ⇒ 孤儿证据（重抓覆盖，非定位算法问题）。
    注：不能用 evidence.raw_text_sha256 == tenders.raw_text_sha256 判断漂移 ——
        实测两者口径不同，579 行**全部**不相等（含 537 行正确行），无区分力。

用法：
    python scripts/check_evidence_offsets.py              # 汇总 + 孤儿/部分失配文档清单
    python scripts/check_evidence_offsets.py -v           # 额外打印前若干条反例明细
退出码：0 = 无孤儿、无部分失配；1 = 检出问题（可挂 CI）
"""
from __future__ import annotations

import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "bidagent.db"
SHOW_BAD = 10


def main(argv: list[str]) -> int:
    verbose = "-v" in argv or "--verbose" in argv

    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute(
        """select e.id, e.tender_id, e.raw_start, e.raw_end, e.evidence_text, t.core_content
           from evidence e join tenders t on t.id = e.tender_id
           order by e.tender_id, e.id"""
    )
    rows = cur.fetchall()
    cur.execute("select count(*) from evidence")
    ev_total = cur.fetchone()[0]
    con.close()
    dangling = ev_total - len(rows)

    ok = bad = missing = 0
    per_doc: dict[int, list[int]] = defaultdict(lambda: [0, 0])  # tid -> [ok, bad]
    orphans: dict[int, int] = defaultdict(int)
    bads = []

    for eid, tid, s, en, txt, core in rows:
        if not core or txt is None or s is None or en is None:
            missing += 1
            continue
        if core[s:en] == txt:
            ok += 1
            per_doc[tid][0] += 1
        else:
            bad += 1
            per_doc[tid][1] += 1
            if txt not in core:
                orphans[tid] += 1
            if len(bads) < SHOW_BAD:
                bads.append((eid, tid, s, en, txt, core[s:en]))

    docs_with_ev = len(per_doc)
    clean_docs = sum(1 for v in per_doc.values() if v[1] == 0)
    mixed_docs = sum(1 for v in per_doc.values() if v[0] and v[1])
    orphan_docs = sorted(t for t, v in per_doc.items() if v[0] == 0 and v[1] > 0)

    print("=" * 62)
    print("证据偏移一致性回归检查")
    print("=" * 62)
    print(f"evidence 行数        : {len(rows)}   （缺失字段 {missing}；悬空外键 {dangling} 条指向不存在的公告）")
    print(f"切片逐字一致         : {ok}")
    print(f"切片不一致           : {bad}")
    print(f"有证据的公告数       : {docs_with_ev}")
    print(f"  ├ 全对（同源干净） : {clean_docs}")
    print(f"  ├ 部分失配         : {mixed_docs}")
    print(f"  └ 全失配（孤儿）   : {len(orphan_docs)}")
    if ok + bad:
        print(f"同源文档精确率       : {ok / (ok + bad) * 100:.2f}%")
    if orphan_docs:
        print("\n⛔ 孤儿公告（core_content 已被重抓覆盖，证据文本不在任何字段中）：")
        for t in orphan_docs:
            print(f"    tender_id={t}  失配 {per_doc[t][1]} 条")
        print("    处置建议：按 evidence_text 重新定位；定位不到则删除该文档证据并重跑抽取。")
    if verbose and bads:
        print(f"\n反例（前 {len(bads)} 条）：")
        for eid, tid, s, en, txt, got in bads:
            print(f"  ev#{eid} tender={tid} [{s},{en})")
            print(f"    expect: {txt!r}")
            print(f"    got   : {got!r}")

    return 0 if (bad == 0 and missing == 0) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
