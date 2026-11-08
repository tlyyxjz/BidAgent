"""中标真实性核验器（复赛主场景）。

输入一份"中标通知书"字段（编号/金额/中标人/采购人），去官方公告库比对，
输出 真/存疑/伪造 并附原文证据。全确定性程序，无 LLM 参与判断。

用法：
    python scripts/verify_award.py --bid-number GHHX2026000062 \
        --amount "22.532万元" --winner "青岛盛信合创电子技术有限公司" \
        --purchaser "中国电子口岸数据中心青岛分中心" [--json]

DB 路径优先级：--db 参数 > 环境变量 BIDAGENT_DB > 仓库默认 data/bidagent.db
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "bidagent.db"

# 中标人抽取：只认两种确定性模式，不引入 LLM
_WINNER_PATTERNS = [
    re.compile(r"供应商名称[：:]\s*([^\s，,；;。、]+)"),
    re.compile(r"(?:中标|成交)供应商[：:]\s*([^\s，,；;。、]+)"),
]
# 明显不是公司名的抽取结果（纯数字/日期/百分比）
_JUNK_VALUE = re.compile(r"^[\d\.\,，年月日%¥￥\-/]+$")


def _norm_bid_number(s: str) -> str:
    """项目编号归一化：去首尾空白与内部空格，统一大写。"""
    return (s or "").strip().replace(" ", "").upper()


def _to_yuan(value: str) -> float | None:
    """金额字符串 -> 元（float）。支持 225320 / 22.532万 / 22.5320000（万元）/ ￥22.532万。"""
    if value is None:
        return None
    s = str(value).strip().replace("，", ",").replace(",", "")
    s = s.replace("（", "").replace("）", "").replace("(", "").replace(")", "")
    s = re.sub(r"[¥￥\s]", "", s)
    if not s:
        return None
    m = re.match(r"^([\d.]+)(万元|亿元|万|亿|元)?$", s)
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2) or ""
    if unit in ("万元", "万"):
        num *= 10000
    elif unit in ("亿元", "亿"):
        num *= 100000000
    return round(num, 2)


def _norm_name(s: str) -> str:
    """名称归一化：去全角/半角空格与常见标点。"""
    return re.sub(r"[\s\u3000]+", "", str(s or ""))


def _extract_winners_from_text(text: str) -> list[str]:
    """从公告原文用确定性正则抽中标人，返回去重列表（保持出现顺序）。"""
    found: list[str] = []
    seen: set[str] = set()
    for pat in _WINNER_PATTERNS:
        for m in pat.finditer(text or ""):
            name = m.group(1).strip()
            if not name or _JUNK_VALUE.fullmatch(name):
                continue
            key = _norm_name(name)
            if key and key not in seen:
                seen.add(key)
                found.append(name)
    return found


def _name_matches(input_name: str, db_names: list[str]) -> bool:
    """输入名称 vs 库名称：双向包含比对，被包含方至少 4 字防泛词误命中。"""
    a = _norm_name(input_name)
    if not a:
        return False
    for db in db_names:
        b = _norm_name(db)
        if not b:
            continue
        if b == a:
            return True
        if len(a) >= 4 and (a in b or b in a):
            return True
    return False


def _query_tenders(conn: sqlite3.Connection, bid_number: str) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        "SELECT id, project_name, bid_number, win_amount, tender_org, "
        "source_url, publish_time, source_raw_text "
        "FROM tenders WHERE bid_number = ?",
        (bid_number,),
    )
    return [
        {
            "id": r[0],
            "project_name": r[1],
            "bid_number": r[2],
            "win_amount": r[3],
            "tender_org": r[4],
            "source_url": r[5],
            "publish_time": r[6],
            "source_raw_text": r[7] or "",
        }
        for r in cur.fetchall()
    ]


def _check_row(row: dict, amount: str, winner: str, purchaser: str) -> tuple[list[dict], bool]:
    """对单条库记录做字段比对，返回 (checks, all_matched)。"""
    checks: list[dict] = []
    raw_winners = _extract_winners_from_text(row["source_raw_text"])

    if amount:
        input_yuan = _to_yuan(amount)
        db_yuan = _to_yuan(row["win_amount"])
        checks.append({
            "field": "中标金额",
            "input": amount,
            "db": row["win_amount"],
            "match": input_yuan is not None and db_yuan is not None and input_yuan == db_yuan,
        })
    if winner:
        checks.append({
            "field": "中标人",
            "input": winner.strip(),
            "db": "、".join(raw_winners) if raw_winners else None,
            "match": _name_matches(winner, raw_winners),
        })
    if purchaser:
        db_pur = _norm_name(row["tender_org"])
        checks.append({
            "field": "采购人",
            "input": purchaser.strip(),
            "db": row["tender_org"],
            "match": bool(db_pur) and db_pur == _norm_name(purchaser),
        })

    all_matched = bool(checks) and all(c["match"] for c in checks)
    return checks, all_matched


def verify_award(
    bid_number: str,
    amount: str = "",
    winner: str = "",
    purchaser: str = "",
    db_path: str | os.PathLike | None = None,
) -> dict:
    """核验一份中标通知书，返回 {"verdict","reason","rows","evidence"}。"""
    number = _norm_bid_number(bid_number)
    if len(number) < 4:
        return {
            "verdict": "fake",
            "reason": f"项目编号格式异常（'{bid_number}'），无法核验",
            "rows": [],
            "evidence": {},
        }
    if not amount and not winner and not purchaser:
        return {
            "verdict": "suspicious",
            "reason": "只提供了编号，没有任何可比对字段，只能确认编号存在性，无法判断真伪",
            "rows": [],
            "evidence": {},
        }

    if db_path is None:
        db_path = os.environ.get("BIDAGENT_DB") or DEFAULT_DB
    conn = sqlite3.connect(str(db_path))
    try:
        rows = _query_tenders(conn, number)
    finally:
        conn.close()

    if not rows:
        return {
            "verdict": "fake",
            "reason": f"项目编号 {bid_number.strip()} 在官方公告库中不存在（库覆盖 {_all_bid_numbers(db_path)} 个公告编号）",
            "rows": [],
            "evidence": {},
        }

    best_row = None
    best_checks = None
    any_verified = False
    for row in rows:
        checks, all_matched = _check_row(row, amount, winner, purchaser)
        if all_matched:
            any_verified = True
            best_row, best_checks = row, checks
            break
        if best_checks is None or len(checks) > len(best_checks):
            best_row, best_checks = row, checks

    if any_verified:
        verdict = "verified"
        reason = "编号存在，且所有提供字段均与官方公告一致"
    else:
        verdict = "suspicious"
        bad = [c["field"] for c in (best_checks or []) if not c["match"]]
        reason = (
            f"编号存在（{len(rows)} 条相关公告），但提供的字段与官方公告不一致："
            f"{'、'.join(bad) if bad else '全部字段'}"
        )

    return {
        "verdict": verdict,
        "reason": reason,
        "rows": [
            {
                "project_name": r["project_name"],
                "bid_number": r["bid_number"],
                "win_amount": r["win_amount"],
                "tender_org": r["tender_org"],
                "source_url": r["source_url"],
                "publish_time": r["publish_time"],
                "checks": _check_row(r, amount, winner, purchaser)[0],
            }
            for r in rows
        ],
        "evidence": {
            "project_name": best_row["project_name"],
            "source_url": best_row["source_url"],
            "publish_time": best_row["publish_time"],
            "winner_line": _find_winner_line(best_row["source_raw_text"]),
        },
    }


def _all_bid_numbers(db_path) -> int:
    """库里有多少个非空编号（用于 fake 理由里的覆盖度说明）。"""
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT COUNT(DISTINCT bid_number) FROM tenders "
            "WHERE bid_number IS NOT NULL AND bid_number != ''"
        ).fetchone()[0]
    except Exception:
        return 0
    finally:
        conn.close()


def _find_winner_line(raw_text: str) -> str | None:
    """返回原文中第一条'供应商名称：'所在行的前后文（证据回溯）。"""
    if not raw_text:
        return None
    m = re.search(r"供应商名称[：:][^\n]{0,40}", raw_text)
    return m.group(0) if m else None


def main() -> None:
    parser = argparse.ArgumentParser(description="中标真实性核验")
    parser.add_argument("--bid-number", required=True, help="通知书上的项目编号")
    parser.add_argument("--amount", default="", help="通知书上的中标金额（如 22.532万元）")
    parser.add_argument("--winner", default="", help="通知书上的中标人")
    parser.add_argument("--purchaser", default="", help="通知书上的采购人")
    parser.add_argument("--db", default=None, help="DB 路径（默认 data/bidagent.db，可用环境变量 BIDAGENT_DB 覆盖）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    result = verify_award(
        args.bid_number, args.amount, args.winner, args.purchaser, args.db,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return

    mark = {"verified": "真", "suspicious": "存疑", "fake": "伪造"}[result["verdict"]]
    print("=" * 60)
    print(f"核验结论：{mark}（{result['verdict']}）")
    print(f"理由：{result['reason']}")
    if result["evidence"].get("project_name"):
        print(f"匹配公告：{result['evidence']['project_name']}")
        print(f"来源：{result['evidence']['source_url']}")
        if result["evidence"].get("winner_line"):
            print(f"原文证据：{result['evidence']['winner_line']}")
    for r in result["rows"]:
        print("-" * 60)
        print(f"公告：{r['project_name']}（编号 {r['bid_number']}）")
        for c in r["checks"]:
            tag = "一致" if c["match"] else "不符"
            print(f"  [{tag}] {c['field']}: 通知书={c['input']!r} vs 官方={c['db']!r}")
    print("=" * 60)


if __name__ == "__main__":
    main()
