# -*- coding: utf-8 -*-
"""大件五（证据再加固）：存证哈希重放核验。

给定 tenders.id，重新读取库内 core_content / source_raw_text，现场重算
SHA-256 并与入库时存证的哈希比对——一致即证明"入库后原文未被篡改"。

退出码：0=全部一致；1=存在不一致；2=记录不存在或无存证。

用法：python scripts/replay_evidence.py --id 1 [--db PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "bidagent.db"


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def replay(row_id: int, db_path: Path) -> dict:
    """重放一条记录的存证核验，返回结构化结果。"""
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cols = [r[1] for r in cur.execute("PRAGMA table_info(tenders)")]
    if "content_sha256" not in cols:
        conn.close()
        return {"id": row_id, "status": "no_evidence",
                "reason": "存证列不存在（未迁移）", "checks": []}
    row = cur.execute(
        "SELECT core_content, source_raw_text, content_sha256, raw_text_sha256, "
        "created_at, source_url FROM tenders WHERE id = ?",
        (row_id,),
    ).fetchone()
    conn.close()
    if row is None:
        return {"id": row_id, "status": "not_found", "reason": "记录不存在",
                "checks": []}
    core, raw, stored_content, stored_raw, created_at, source_url = row
    if not stored_content and not stored_raw:
        return {"id": row_id, "status": "no_evidence",
                "reason": "该记录无存证哈希", "checks": []}

    checks = []
    if stored_content:
        recomputed = sha256_hex(core) if core else None
        checks.append({
            "field": "core_content",
            "stored": stored_content,
            "recomputed": recomputed,
            "match": recomputed == stored_content,
        })
    if stored_raw:
        recomputed = sha256_hex(raw) if raw else None
        checks.append({
            "field": "source_raw_text",
            "stored": stored_raw,
            "recomputed": recomputed,
            "match": recomputed == stored_raw,
        })

    all_match = all(c["match"] for c in checks)
    return {
        "id": row_id,
        "status": "verified" if all_match else "tampered",
        "reason": "重算哈希与存证一致，入库后原文未被篡改" if all_match
        else "重算哈希与存证不一致，原文疑似被修改",
        "ingested_at": created_at,
        "source_url": source_url,
        "checks": checks,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="存证哈希重放核验")
    ap.add_argument("--id", type=int, required=True, help="tenders.id")
    ap.add_argument("--db", default=None, help="DB 路径（默认 data/bidagent.db）")
    args = ap.parse_args()
    db_path = Path(args.db) if args.db else DEFAULT_DB
    if not db_path.exists():
        print(f"DB 不存在: {db_path}", file=sys.stderr)
        sys.exit(2)
    result = replay(args.id, db_path)
    print(f"记录 #{result['id']}：{result['status']}")
    print(f"说明：{result['reason']}")
    for c in result.get("checks", []):
        flag = "一致" if c["match"] else "不一致"
        print(f"  [{flag}] {c['field']}: 存证={c['stored'][:16]}… "
              f"重算={(c['recomputed'] or '')[:16]}…")
    if result.get("ingested_at"):
        print(f"入库时间：{result['ingested_at']}")
    if result.get("source_url"):
        print(f"来源链接：{result['source_url']}")
    if result["status"] == "verified":
        sys.exit(0)
    elif result["status"] == "tampered":
        sys.exit(1)
    else:
        sys.exit(2)


if __name__ == "__main__":
    main()
