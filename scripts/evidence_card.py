"""证据回溯卡片（复赛"可回溯"卖点载体）。

把核验器/画像的输出统一渲染成"证据卡片"：
每张卡 = 声明(claim) + 原文证据(含字符位置定位) + 来源URL + 抓取时间 + 判定章。

证据一律来自公告原文 + 确定性正则定位，无 LLM、无编造。

用法：
    python scripts/evidence_card.py --verify --bid-number GHHX2026000062 \
        --amount "22.532万元" --winner "青岛盛信合创电子技术有限公司" \
        --purchaser "中国电子口岸数据中心青岛分中心" --out cards.html
    python scripts/evidence_card.py --company "湖南创益蔚来进出口有限公司" --out cards.html
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_award import (  # noqa: E402
    _norm_bid_number,
    _query_tenders,
    _to_yuan,
    verify_award,
)
import winner_profile as wp  # noqa: E402

DEFAULT_DB = REPO / "data" / "bidagent.db"

# 证据定位模式（金额/编号/采购人各自独立的确定性正则）
_PAT_AMOUNT = re.compile(r"中标（成交）金额[：:][^\n]{0,60}")
_PAT_BIDNO = re.compile(r"项目编号[：:][^\n]{0,60}")
_PAT_WINNER = re.compile(r"供应商名称[：:][^\n]{0,60}")


def _locate(text: str, snippet: str) -> tuple[int, int] | None:
    """返回 snippet 在原文中的字符位置 [start, end)，找不到返回 None。"""
    i = text.find(snippet)
    if i < 0:
        return None
    return (i, i + len(snippet))


def _field_evidence(raw: str, field: str) -> tuple[str | None, tuple[int, int] | None]:
    """按字段在原文中定位证据行及位置。"""
    pattern = {
        "中标金额": _PAT_AMOUNT,
        "中标人": _PAT_WINNER,
        "项目编号": _PAT_BIDNO,
    }.get(field)
    if not pattern:
        return None, None
    m = pattern.search(raw or "")
    if not m:
        return None, None
    return m.group(0), (m.start(), m.end())


def cards_from_verification(result: dict, raw_texts: dict[str, str]) -> list[dict]:
    """把 verify_award 的结果转成证据卡片列表。

    raw_texts: {bid_number: source_raw_text}，由调用方从 DB 取（便于单测注入）。
    """
    cards: list[dict] = []
    cards.append({
        "kind": "verdict",
        "verdict": result["verdict"],
        "reason": result["reason"],
        "claim": result["reason"],
    })
    for row in result.get("rows", []):
        raw = raw_texts.get(row["bid_number"], "") or ""
        for check in row["checks"]:
            evidence, pos = _field_evidence(raw, check["field"])
            cards.append({
                "kind": "check",
                "verdict": "verified" if check["match"] else "suspicious",
                "title": f"{check['field']}核验",
                "claim": f"{check['field']} = {check['input']}",
                "db_value": f"官方记录 = {check['db']}",
                "match": check["match"],
                "evidence_text": evidence,
                "evidence_pos": pos,
                "source_url": row["source_url"],
                "publish_time": row["publish_time"],
                "project_name": row["project_name"],
            })
    return cards


def cards_from_profile(result: dict) -> list[dict]:
    """把 winner_profile.profile 的结果转成证据卡片列表。"""
    s = result["stats"]
    cards: list[dict] = [{
        "kind": "summary",
        "verdict": "verified",
        "title": f"画像摘要：{result['company']}",
        "claim": (
            f"中标 {s['total_wins']} 次，总金额 {s['total_amount']:,.0f} 元；"
            f"Top1 客户占 {s['top1_share_pct']}%"
            if s["top1_share_pct"] is not None
            else f"中标 {s['total_wins']} 次"
        ),
        "db_value": f"年度趋势 {s['yearly_trend']}",
        "match": True,
        "evidence_text": None,
        "evidence_pos": None,
        "source_url": None,
        "publish_time": None,
        "project_name": None,
    }]
    for w in result["wins"]:
        cards.append({
            "kind": "check",
            "verdict": "verified",
            "title": "中标记录",
            "claim": f"{w['winner']} 中标 {w['project_name']}",
            "db_value": f"金额 {_to_yuan(w['win_amount']) or '-'} 元 | 采购人 {w['tender_org']}",
            "match": True,
            "evidence_text": w["evidence_line"],
            "evidence_pos": None,
            "source_url": w["source_url"],
            "publish_time": w["publish_time"],
            "project_name": w["project_name"],
        })
    return cards


_VERDICT_BADGE = {
    "verified": ("真", "#1a7f37"),
    "suspicious": ("存疑", "#9a6700"),
    "fake": ("伪造", "#cf222e"),
}


def render_text(cards: list[dict]) -> str:
    lines = ["=" * 64]
    for c in cards:
        if c["kind"] == "verdict":
            badge = _VERDICT_BADGE.get(c["verdict"], (c["verdict"], ""))[0]
            lines.append(f"【判定】{badge} — {c['reason']}")
            continue
        tag = "一致" if c.get("match") else "不符"
        lines.append(f"[{tag}] {c['title']}")
        lines.append(f"    声明: {c['claim']}")
        lines.append(f"    对照: {c['db_value']}")
        if c.get("evidence_text"):
            pos = c.get("evidence_pos")
            pos_s = f"（原文第 {pos[0]}-{pos[1]} 字符）" if pos else ""
            lines.append(f"    证据: {c['evidence_text']}{pos_s}")
        if c.get("source_url"):
            lines.append(f"    来源: {c['source_url']} @ {c['publish_time']}")
        lines.append("-" * 64)
    return "\n".join(lines)


def render_html(cards: list[dict]) -> str:
    """极简单文件 HTML，无外部依赖，可直接截图进 PPT。"""
    body = []
    for c in cards:
        if c["kind"] == "verdict":
            badge, color = _VERDICT_BADGE.get(c["verdict"], (c["verdict"], "#666"))
            body.append(
                f'<div class="card verdict" style="border-left-color:{color}">'
                f'<span class="badge" style="background:{color}">{html.escape(badge)}</span>'
                f'<div class="claim">{html.escape(c["reason"])}</div></div>'
            )
            continue
        tag, tcolor = ("一致", "#1a7f37") if c.get("match") else ("不符", "#cf222e")
        pos = c.get("evidence_pos")
        pos_s = f'<span class="pos">原文第 {pos[0]}-{pos[1]} 字符</span>' if pos else ""
        ev = (
            f'<div class="evidence">{html.escape(c["evidence_text"])} {pos_s}</div>'
            if c.get("evidence_text") else ""
        )
        src = (
            f'<div class="source">来源：{html.escape(c["source_url"] or "")} '
            f'@ {html.escape(str(c["publish_time"] or ""))}</div>'
            if c.get("source_url") else ""
        )
        body.append(
            f'<div class="card"><div class="head">'
            f'<span class="tag" style="color:{tcolor};border-color:{tcolor}">{tag}</span>'
            f'<b>{html.escape(c["title"])}</b></div>'
            f'<div class="claim">{html.escape(c["claim"])}</div>'
            f'<div class="db">{html.escape(c["db_value"])}</div>'
            f'{ev}{src}</div>'
        )
    return (
        "<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>"
        "<title>证据回溯卡片</title><style>"
        "body{font-family:'Microsoft YaHei',sans-serif;background:#f6f8fa;padding:24px;max-width:860px;margin:0 auto}"
        ".card{background:#fff;border:1px solid #d0d7de;border-left:4px solid #57606a;"
        "border-radius:8px;padding:14px 18px;margin:12px 0}"
        ".verdict{font-size:17px;font-weight:600}"
        ".badge{color:#fff;padding:2px 10px;border-radius:12px;font-size:13px;margin-right:10px}"
        ".head{display:flex;gap:10px;align-items:center;margin-bottom:6px}"
        ".tag{font-size:12px;border:1px solid;border-radius:10px;padding:0 8px;font-weight:600}"
        ".claim{font-size:15px;font-weight:600;margin:2px 0}"
        ".db{color:#57606a;font-size:13px;margin:2px 0}"
        ".evidence{background:#f6f8fa;border-radius:6px;padding:8px 10px;font-size:13px;"
        "font-family:Consolas,monospace;margin:6px 0;word-break:break-all}"
        ".pos{color:#0969da;font-weight:600}"
        ".source{font-size:12px;color:#57606a;word-break:break-all}"
        "</style></head><body>" + "\n".join(body) + "</body></html>"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="证据回溯卡片")
    parser.add_argument("--verify", action="store_true", help="核验模式")
    parser.add_argument("--company", default="", help="画像模式：企业名称")
    parser.add_argument("--bid-number", default="", help="核验：项目编号")
    parser.add_argument("--amount", default="", help="核验：中标金额")
    parser.add_argument("--winner", default="", help="核验：中标人")
    parser.add_argument("--purchaser", default="", help="核验：采购人")
    parser.add_argument("--db", default=None, help="DB 路径（默认 data/bidagent.db，可用环境变量 BIDAGENT_DB 覆盖）")
    parser.add_argument("--out", default="", help="输出 HTML 文件路径（缺省打印文本）")
    args = parser.parse_args()

    db = args.db or os.environ.get("BIDAGENT_DB") or DEFAULT_DB

    if args.verify:
        result = verify_award(
            args.bid_number, args.amount, args.winner, args.purchaser, db,
        )
        raw_texts = {}
        if result.get("rows"):
            import sqlite3
            conn = sqlite3.connect(str(db))
            try:
                rows = _query_tenders(conn, _norm_bid_number(args.bid_number))
                raw_texts = {r["bid_number"]: r["source_raw_text"] for r in rows}
            finally:
                conn.close()
        cards = cards_from_verification(result, raw_texts)
    elif args.company:
        cards = cards_from_profile(wp.profile(args.company, db))
    else:
        parser.error("必须指定 --verify 或 --company")

    if args.out:
        Path(args.out).write_text(render_html(cards), encoding="utf-8")
        print(f"已生成 {len(cards)} 张证据卡片 -> {args.out}")
    else:
        print(render_text(cards))


if __name__ == "__main__":
    main()
