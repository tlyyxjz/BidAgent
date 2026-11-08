# -*- coding: utf-8 -*-
"""批量核验：CSV 一摞中标通知书 → 逐条判定 → 结果 CSV（复赛"省人力"场景）。

输入 CSV 列名要求（支持中英文两种表头）：
    项目编号/bid_number、中标金额/amount、中标人/winner、采购人/purchaser
输出 = 原列 + 结论(真/存疑/伪造) + 理由 + 匹配公告 + 来源URL。

用法：
    python scripts/verify_batch.py --csv 通知书.csv --out 核验结果.csv
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_award import verify_award  # noqa: E402

DEFAULT_DB = REPO / "data" / "bidagent.db"

# 表头别名映射（大小写不敏感、兼容常见写法）
ALIASES = {
    "bid_number": ("bid_number", "项目编号", "编号"),
    "amount": ("amount", "中标金额", "金额"),
    "winner": ("winner", "中标人", "供应商", "成交供应商"),
    "purchaser": ("purchaser", "采购人", "招标人", "采购单位"),
}

VERDICT_CN = {"verified": "真", "suspicious": "存疑", "fake": "伪造"}


def map_header(header: str) -> str | None:
    """把任意表头映射到内部字段名；不认识的返回 None。"""
    h = header.strip().lower()
    for field, names in ALIASES.items():
        if h in names or header.strip() in names:
            return field
    return None


def process_rows(rows: list[dict], db_path) -> list[dict]:
    """逐条核验（纯逻辑层，便于测试）。rows 每项至少含 bid_number。"""
    out = []
    for row in rows:
        r = verify_award(
            row.get("bid_number") or "",
            amount=row.get("amount") or "",
            winner=row.get("winner") or "",
            purchaser=row.get("purchaser") or "",
            db_path=db_path,
        )
        evidence = r.get("evidence") or {}
        out.append({
            **row,
            "结论": VERDICT_CN.get(r["verdict"], r["verdict"]),
            "理由": r.get("reason", ""),
            "匹配公告": evidence.get("project_name") or "",
            "来源URL": evidence.get("source_url") or "",
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="批量核验中标通知书")
    ap.add_argument("--csv", required=True, help="输入 CSV 路径")
    ap.add_argument("--out", default="核验结果.csv", help="输出 CSV 路径")
    ap.add_argument("--db", default=None, help="DB 路径（默认 data/bidagent.db，可用环境变量 BIDAGENT_DB 覆盖）")
    args = ap.parse_args()

    db = args.db or os.environ.get("BIDAGENT_DB") or DEFAULT_DB

    with open(args.csv, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        mapped = {h: map_header(h) for h in headers}
        required = [h for h in headers if mapped[h] == "bid_number"]
        if not required:
            print(f"表头找不到项目编号列（支持：{ALIASES['bid_number']}）")
            sys.exit(1)
        rows = []
        for raw in reader:
            row = {}
            for h in headers:
                field = mapped.get(h)
                if field:
                    row[field] = (raw.get(h) or "").strip()
            rows.append(row)

    results = process_rows(rows, db)
    with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    counts = {}
    for r in results:
        counts[r["结论"]] = counts.get(r["结论"], 0) + 1
    print(f"核验完成 {len(results)} 条 → {args.out}")
    print(f"结论分布：{counts}")


if __name__ == "__main__":
    main()
