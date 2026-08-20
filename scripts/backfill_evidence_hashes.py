# -*- coding: utf-8 -*-
"""大件五（证据再加固·快照）：存量 tenders 补 SHA-256 存证哈希。

规则：
- content_sha256 = SHA-256(core_content)，raw_text_sha256 = SHA-256(source_raw_text)
- 空文本（None/""）不落哈希，保持 NULL（诚实：无内容不存证）
- 只填 NULL 行，已有值不覆盖（幂等可重跑）
- 时间戳复用 created_at（入库时刻），不改动

用法：python scripts/backfill_evidence_hashes.py [--db PATH] [--dry-run]
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


def backfill(db_path: Path, dry_run: bool = False) -> dict:
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cols = [r[1] for r in cur.execute("PRAGMA table_info(tenders)")]
    if "content_sha256" not in cols or "raw_text_sha256" not in cols:
        conn.close()
        raise SystemExit(
            "存证列不存在，请先运行 scripts/migrate_add_evidence_columns.py"
        )

    rows = cur.execute(
        "SELECT id, core_content, source_raw_text FROM tenders "
        "WHERE (core_content IS NOT NULL AND core_content != '' "
        "AND content_sha256 IS NULL) "
        "OR (source_raw_text IS NOT NULL AND source_raw_text != '' "
        "AND raw_text_sha256 IS NULL)"
    ).fetchall()

    filled_content = 0
    filled_raw = 0
    for rid, core, raw in rows:
        new_content = sha256_hex(core) if core else None
        new_raw = sha256_hex(raw) if raw else None
        if core:
            filled_content += 1
        if raw:
            filled_raw += 1
        if not dry_run:
            cur.execute(
                "UPDATE tenders SET content_sha256 = COALESCE(content_sha256, ?), "
                "raw_text_sha256 = COALESCE(raw_text_sha256, ?) WHERE id = ?",
                (new_content, new_raw, rid),
            )
    if not dry_run:
        conn.commit()
    conn.close()
    return {"rows_touched": len(rows), "filled_content": filled_content,
            "filled_raw": filled_raw, "dry_run": dry_run}


def main() -> None:
    ap = argparse.ArgumentParser(description="存量 tenders 补 SHA-256 存证哈希")
    ap.add_argument("--db", default=None, help="DB 路径（默认 data/bidagent.db）")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写入")
    args = ap.parse_args()
    db_path = Path(args.db) if args.db else DEFAULT_DB
    if not db_path.exists():
        print(f"DB 不存在: {db_path}", file=sys.stderr)
        sys.exit(1)
    stats = backfill(db_path, dry_run=args.dry_run)
    print(stats)


if __name__ == "__main__":
    main()
