# -*- coding: utf-8 -*-
"""证据偏移一致性回归门禁（双信号：字符切片 + 文本哈希）。

坐标基准（2026-10-08 实测确认）：
    evidence 的 [raw_start, raw_end) 以 **tenders.core_content** 为坐标基准，
    不是 source_raw_text（后者顶着 `# 标题 / # URL:` 头部，两者长度不同）。

两个判据（互补，缺一不可）：
  ① 字符切片：core[raw_start:raw_end] == evidence_text
     → 直接验证「偏移指向的正是那段文字」。
  ② 文本哈希：evidence.raw_text_sha256 == sha256(tender.core_content)
     → 验证「这条证据当初就是在**现在这份文本**上抽的」。
     2026-10-08 实测：537 条正确行**全** True、42 条孤儿行**全** False —— 零误判。

     ⚠️ 曾经写错过：不能拿 evidence.raw_text_sha256 去比 tenders.raw_text_sha256
     （后者是 sha256(source_raw_text)，口径不同 ⇒ 579 行全不等、看似"无区分力"）。
     **必须对标 sha256(core_content)**，因为 evidence 的偏移是在 core_content 上算的。

行级分类：
  HEALTHY       切片一致                      —— 正常
  RELOCATABLE   切片不一致，但证据文本仍在 core —— 只是偏移漂移，**可自动重定位修复**
  STALE         切片不一致，文本也不在 core，
                且 evidence 哈希 ≠ sha256(core)  —— 证据属于**另一份文本**，不可恢复
  SUSPECT       其余组合                      —— 需人工判读（规则没覆盖的不确定性）

已知基线（2026-10-08，579 行 / 147 篇有证据的公告）：
  537 HEALTHY · 42 STALE（集中在 tender_id=1..5）· 7 条悬空外键。
根因：**tender_id 复用** —— 这 5 篇文档被整批替换成了别的公告（当前内容与证据
的项目编号都对不上），旧证据仍挂在同一个 id 上；原文本在 data/snapshots/ 里也
查不到 ⇒ 不可恢复，只能隔离或删除。

用法：
    python scripts/check_evidence_offsets.py            # 汇总 + 问题文档清单
    python scripts/check_evidence_offsets.py -v         # 额外打印反例明细
退出码：0 = 只有 HEALTHY（无 RELOCATABLE / STALE / SUSPECT、无悬空外键）；1 = 检出问题
"""
from __future__ import annotations

import hashlib
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "bidagent.db"
SHOW_BAD = 10

HEALTHY, RELOCATABLE, STALE, SUSPECT = "HEALTHY", "RELOCATABLE", "STALE", "SUSPECT"


def sha256(s: str | None) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def main(argv: list[str]) -> int:
    verbose = "-v" in argv or "--verbose" in argv

    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute(
        """select e.id, e.tender_id, e.raw_start, e.raw_end, e.evidence_text,
                  e.raw_text_sha256, t.core_content
           from evidence e join tenders t on t.id = e.tender_id
           order by e.tender_id, e.id"""
    )
    rows = cur.fetchall()
    cur.execute("select count(*) from evidence")
    ev_total = cur.fetchone()[0]
    cur.execute(
        """select e.tender_id, count(*) from evidence e
           left join tenders t on t.id = e.tender_id
           where t.id is null group by e.tender_id order by e.tender_id"""
    )
    dangling_fk = cur.fetchall()
    con.close()
    dangling = ev_total - len(rows)

    counts = defaultdict(int)
    per_doc: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    bads = []
    core_hash_cache: dict[int, str] = {}

    for eid, tid, s, en, txt, ev_hash, core in rows:
        core = core or ""
        if tid not in core_hash_cache:
            core_hash_cache[tid] = sha256(core)
        h_ok = ev_hash is not None and ev_hash == core_hash_cache[tid]

        if not core or txt is None or s is None or en is None:
            verdict = SUSPECT
        else:
            slice_ok = core[s:en] == txt
            if slice_ok:
                verdict = HEALTHY
            elif txt in core:
                verdict = RELOCATABLE          # 文本还在 ⇒ 可重定位
            elif not h_ok:
                verdict = STALE                # 属于另一份文本 ⇒ 不可恢复
            else:
                verdict = SUSPECT
        counts[verdict] += 1
        per_doc[tid][verdict] += 1
        if verdict != HEALTHY and len(bads) < SHOW_BAD:
            bads.append((eid, tid, s, en, txt, core[s:en], verdict))

    docs_with_ev = len(per_doc)
    clean_docs = sum(1 for v in per_doc.values() if v[HEALTHY] and sum(v.values()) == v[HEALTHY])

    def docs_of(kind: str) -> list[int]:
        return sorted(t for t, v in per_doc.items() if v[kind])

    stale_docs = docs_of(STALE)

    print("=" * 62)
    print("证据偏移一致性回归检查（切片 + 哈希 双判据）")
    print("=" * 62)
    print(f"evidence 行数        : {len(rows)}   （悬空外键 {dangling} 条指向不存在的公告）")
    print(f"  ✔ HEALTHY  切片一致            : {counts[HEALTHY]}")
    print(f"  ✎ RELOCATABLE 偏移漂移可重定位  : {counts[RELOCATABLE]}")
    print(f"  ⛔ STALE    属于另一份文本       : {counts[STALE]}")
    print(f"  ? SUSPECT  规则未覆盖           : {counts[SUSPECT]}")
    print(f"有证据的公告数       : {docs_with_ev}   （全部 HEALTHY 的 {clean_docs} 篇）")
    healthy = counts[HEALTHY]
    if healthy + counts[STALE] + counts[RELOCATABLE]:
        denom = healthy + counts[STALE] + counts[RELOCATABLE]
        print(f"同源文档精确率       : {healthy / denom * 100:.2f}%")

    if stale_docs:
        print("\n⛔ STALE 公告（证据属于另一份文本，当前字段与快照里都查不到 ⇒ 不可恢复）：")
        for t in stale_docs:
            print(f"    tender_id={t}  {per_doc[t][STALE]} 条")
        print("    处置建议：先隔离（导出 JSON）再从 evidence 表删除；不要试图重定位。")
    rel_docs = docs_of(RELOCATABLE)
    if rel_docs:
        print(f"\n✎ RELOCATABLE 公告（文本仍在、偏移漂移，可自动重定位）：{rel_docs}")
    sus_docs = docs_of(SUSPECT)
    if sus_docs:
        print(f"\n? SUSPECT 公告：{sus_docs}")
    if dangling_fk:
        ids = ", ".join(f"{t}({c}条)" for t, c in dangling_fk)
        print(f"\n⛔ 悬空外键（证据指向不存在的公告）：{ids}")

    if verbose and bads:
        print(f"\n反例（前 {len(bads)} 条）：")
        for eid, tid, s, en, txt, got, verdict in bads:
            print(f"  [{verdict}] ev#{eid} tender={tid} [{s},{en})")
            print(f"    expect: {txt!r}")
            print(f"    got   : {got!r}")

    problems = counts[RELOCATABLE] + counts[STALE] + counts[SUSPECT] + dangling
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
