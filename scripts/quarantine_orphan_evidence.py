# -*- coding: utf-8 -*-
"""隔离/清理「孤儿证据」—— 判定依据同 check_evidence_offsets.py（切片 + 哈希 双判据）。

背景：tender_id 复用导致 evidence 行挂到了别的公告上（详见 check_evidence_offsets.py 头部）。
2026-10-08 实测确认：这些证据的原文在当前字段与 data/snapshots/ 里都查不到 ⇒ **不可恢复**。

安全设计：
  · 默认 **不改库**：只做体检 + 把待处理行导出成 JSON（含完整字段，可事后追查）。
  · 加 --apply 才真正删除；删除前**先整库备份**到 data/backups/。
  · 删除范围严格限定为「STALE（属于另一份文本）」+「悬空外键（指向不存在的公告）」
    两类；RELOCATABLE（只是偏移漂移）**不在删除范围内**，会被明确地排除并提示。

用法：
    python scripts/quarantine_orphan_evidence.py                    # 体检 + 导出 JSON
    python scripts/quarantine_orphan_evidence.py --apply            # 备份后删除
    python scripts/quarantine_orphan_evidence.py --apply --no-backup
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "bidagent.db"
QUARANTINE_DIR = ROOT / "data" / "quarantine"
BACKUP_DIR = ROOT / "data" / "backups"


def sha256(s: str | None) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def classify(con: sqlite3.Connection) -> tuple[list[dict], list[dict], dict]:
    """返回 (stale_rows, dangling_rows, summary)。只读。"""
    cur = con.cursor()
    rows = cur.execute(
        """select e.id, e.tender_id, e.raw_start, e.raw_end, e.evidence_text,
                  e.context_before, e.context_after, e.match_method, e.confidence,
                  e.raw_text_sha256, t.project_name, t.core_content
           from evidence e join tenders t on t.id = e.tender_id
           order by e.tender_id, e.id"""
    ).fetchall()
    core_hash: dict[int, str] = {}
    stale, relocatable = [], []
    for (eid, tid, s, en, txt, cb, ca, mm, conf, ev_hash, pname, core) in rows:
        core = core or ""
        core_hash.setdefault(tid, sha256(core))
        slice_ok = bool(core) and txt is not None and s is not None and en is not None and core[s:en] == txt
        if slice_ok:
            continue
        h_ok = ev_hash is not None and ev_hash == core_hash[tid]
        rec = {
            "evidence_id": eid, "tender_id": tid, "raw_start": s, "raw_end": en,
            "evidence_text": txt, "context_before": cb, "context_after": ca,
            "match_method": mm, "confidence": conf,
            "evidence_raw_text_sha256": ev_hash,
            "current_core_sha256": core_hash[tid],
            "current_project_name": pname,
        }
        if txt in core:
            relocatable.append(rec)          # 文本还在 ⇒ 不删，另行重定位
        elif not h_ok:
            stale.append(rec)                # 属于另一份文本 ⇒ 隔离
        else:
            relocatable.append(rec)          # 规则未覆盖，保守归入"不删"

    dangling = []
    for eid, tid in cur.execute(
        """select e.id, e.tender_id from evidence e
           left join tenders t on t.id = e.tender_id
           where t.id is null order by e.tender_id, e.id"""
    ).fetchall():
        dangling.append({"evidence_id": eid, "tender_id": tid})

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "db": str(DB),
        "total_evidence": cur.execute("select count(*) from evidence").fetchone()[0],
        "stale": len(stale), "dangling": len(dangling), "relocatable_skipped": len(relocatable),
    }
    return stale, dangling, summary | {"relocatable_rows": relocatable}


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    no_backup = "--no-backup" in argv

    con = sqlite3.connect(DB)
    stale, dangling, summary = classify(con)

    print("=" * 62)
    print("孤儿证据隔离" + ("（APPLY 模式：将删库）" if apply else "（体检模式：不改库）"))
    print("=" * 62)
    print(f"evidence 总数            : {summary['total_evidence']}")
    print(f"STALE（属于另一份文本）  : {summary['stale']}")
    print(f"悬空外键（公告不存在）   : {summary['dangling']}")
    print(f"RELOCATABLE（仅漂移，跳过）: {summary['relocatable_skipped']}")

    if not (stale or dangling):
        print("\n✅ 无需处置。")
        con.close()
        return 0

    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = QUARANTINE_DIR / f"orphan_evidence_{stamp}.json"
    out.write_text(json.dumps({"summary": summary, "stale": stale, "dangling": dangling},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已导出隔离清单 → {out.relative_to(ROOT)}")

    if not apply:
        print("（体检模式，未修改数据库。加 --apply 执行删除。）")
        con.close()
        return 0

    if not no_backup:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        bak = BACKUP_DIR / f"bidagent.db.{stamp}.bak"
        shutil.copy2(DB, bak)
        print(f"已备份数据库 → {bak.relative_to(ROOT)}")

    cur = con.cursor()
    ids = [r["evidence_id"] for r in stale] + [r["evidence_id"] for r in dangling]
    cur.executemany("delete from evidence where id = ?", [(i,) for i in ids])
    con.commit()
    left = cur.execute("select count(*) from evidence").fetchone()[0]
    print(f"已删除 {len(ids)} 行；evidence 剩余 {left} 行。")
    con.close()
    print("\n复核：python scripts/check_evidence_offsets.py")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
