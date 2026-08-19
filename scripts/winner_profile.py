"""企业中标画像（复赛辅助场景）。

输入企业名称，聚合其全部中标记录，输出：
- 中标次数 / 中标总金额 / 单笔最大最小 / 时间趋势
- 客户集中度（采购人分布 + Top1 占比）
- 区域分布
- 每条中标记录的证据回溯（编号/项目/金额/采购人/日期/来源URL/原文行）

数据原则（与核验器一致，全确定性）：
中标人从 source_raw_text 用确定性正则抽取，绝不使用 win_company 列
（全空）或直接采信实体表（有页面混流错误）。

用法：
    python scripts/winner_profile.py --company "青岛盛信合创电子技术有限公司"
    python scripts/winner_profile.py --all              # 列出库中全部可画像企业
    python scripts/winner_profile.py --company xxx --json
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_award import (  # noqa: E402
    _extract_winners_from_text,
    _find_winner_line,
    _name_matches,
    _to_yuan,
)

DEFAULT_DB = REPO / "data" / "bidagent.db"


def iter_winner_tenders(db_path) -> list[dict]:
    """扫描全库，返回 [(tender, winner_name), ...]，中标人一律来自原文正则。"""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT bid_number, project_name, win_amount, tender_org, location, "
            "publish_time, source_url, source_raw_text "
            "FROM tenders WHERE source_raw_text IS NOT NULL AND source_raw_text != ''"
        ).fetchall()
    finally:
        conn.close()
    out = []
    for bid_number, project_name, win_amount, tender_org, location, publish_time, source_url, raw in rows:
        for name in _extract_winners_from_text(raw):
            out.append({
                "winner": name,
                "bid_number": bid_number,
                "project_name": project_name,
                "win_amount": win_amount,
                "tender_org": tender_org,
                "location": location,
                "publish_time": publish_time,
                "source_url": source_url,
                "evidence_line": _find_winner_line(raw),
            })
    return out


def _year_of(publish_time: str | None) -> str:
    if not publish_time:
        return "未知"
    return str(publish_time)[:4]


def aggregate(wins: list[dict]) -> dict:
    """纯函数：对某企业的中标记录做统计聚合。wins 每项含 win_amount/publish_time/tender_org。"""
    amounts = [_to_yuan(w["win_amount"]) for w in wins]
    amounts = [a for a in amounts if a is not None]
    years = Counter(_year_of(w.get("publish_time")) for w in wins)
    customers: Counter[str] = Counter()
    customer_amount: dict[str, float] = defaultdict(float)
    for w in wins:
        org = (w.get("tender_org") or "未标注").strip()
        customers[org] += 1
        a = _to_yuan(w.get("win_amount"))
        if a is not None:
            customer_amount[org] += a
    top_customers = sorted(
        customers.items(), key=lambda kv: (-kv[1], -customer_amount.get(kv[0], 0.0))
    )
    total_amount = round(sum(amounts), 2)
    top1_share = None
    if total_amount > 0 and customer_amount:
        top_org = max(customer_amount, key=lambda k: customer_amount[k])
        top1_share = round(customer_amount[top_org] / total_amount * 100, 1)
    return {
        "total_wins": len(wins),
        "total_amount": total_amount,
        "avg_amount": round(total_amount / len(amounts), 2) if amounts else None,
        "max_amount": max(amounts) if amounts else None,
        "min_amount": min(amounts) if amounts else None,
        "yearly_trend": dict(sorted(years.items())),
        "customers": [
            {
                "purchaser": org,
                "count": cnt,
                "amount": round(customer_amount.get(org, 0.0), 2),
            }
            for org, cnt in top_customers
        ],
        "top1_share_pct": top1_share,
        "locations": dict(Counter((w.get("location") or "未知").strip() for w in wins)),
    }


def profile(company: str, db_path=None) -> dict:
    """企业中标画像。返回 {company, wins, stats}。"""
    if db_path is None:
        db_path = os.environ.get("BIDAGENT_DB") or DEFAULT_DB
    all_rows = iter_winner_tenders(db_path)
    wins = [r for r in all_rows if _name_matches(company, [r["winner"]])]
    wins.sort(key=lambda w: str(w.get("publish_time") or ""))
    return {"company": company, "wins": wins, "stats": aggregate(wins)}


def list_all_companies(db_path=None) -> dict[str, int]:
    """库中全部可画像企业及其中标次数（按次数降序）。"""
    if db_path is None:
        db_path = os.environ.get("BIDAGENT_DB") or DEFAULT_DB
    counter: Counter[str] = Counter()
    for row in iter_winner_tenders(db_path):
        counter[row["winner"]] += 1
    return dict(counter.most_common())


def _fmt_amount(v) -> str:
    if v is None:
        return "-"
    return f"{v:,.2f} 元" if v >= 10000 else f"{v} 元"


def main() -> None:
    parser = argparse.ArgumentParser(description="企业中标画像")
    parser.add_argument("--company", default="", help="企业名称（支持简称/全称）")
    parser.add_argument("--all", action="store_true", help="列出库中全部可画像企业")
    parser.add_argument("--db", default=None, help="DB 路径（默认 data/bidagent.db，可用环境变量 BIDAGENT_DB 覆盖）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    if args.all:
        companies = list_all_companies(args.db)
        if args.json:
            print(json.dumps(companies, ensure_ascii=False, indent=2))
            return
        print(f"库中可画像企业 {len(companies)} 家：")
        for name, cnt in companies.items():
            print(f"  {cnt:>3} 次  {name}")
        return

    if not args.company:
        parser.error("必须提供 --company 或 --all")

    result = profile(args.company, args.db)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return

    s = result["stats"]
    print("=" * 64)
    print(f"企业中标画像：{result['company']}")
    print(f"中标次数 {s['total_wins']} | 中标总金额 {_fmt_amount(s['total_amount'])}")
    print(f"单笔：平均 {_fmt_amount(s['avg_amount'])} / 最高 {_fmt_amount(s['max_amount'])} / 最低 {_fmt_amount(s['min_amount'])}")
    print(f"年度趋势：{s['yearly_trend']}")
    print(f"区域分布：{s['locations']}")
    print(f"客户集中度：Top1 采购人占金额 {s['top1_share_pct']}%" if s["top1_share_pct"] is not None else "客户集中度：无金额数据")
    for c in s["customers"][:5]:
        print(f"  - {c['purchaser']}: {c['count']} 次 / {_fmt_amount(c['amount'])}")
    print("-" * 64)
    for i, w in enumerate(result["wins"], 1):
        print(f"[{i}] {w['project_name']}")
        print(f"    编号 {w['bid_number']} | 金额 {_fmt_amount(_to_yuan(w['win_amount']))} | 采购人 {w['tender_org']}")
        print(f"    日期 {w['publish_time']} | {w['source_url']}")
        if w["evidence_line"]:
            print(f"    原文证据：{w['evidence_line']}")
    print("=" * 64)


if __name__ == "__main__":
    main()
