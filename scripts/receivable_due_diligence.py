# -*- coding: utf-8 -*-
"""中标应收账款放款前核验——一页尽调单（大件三）。

金融场景：供应商以"中标应收账款"向银行/保理申请融资，提交中标通知书。
放款前须回答三问：
1. 存在性：这个项目在官方公告库里真的存在吗？
2. 一致性：通知书上的中标人/金额/采购人与官方公告一致吗？
3. 证据链：每个结论能否回溯到公告原文与来源链接？

本引擎复用 scripts/verify_award.py 的确定性核验内核（零 LLM），
在其之上叠加金融风控视角：
- 无编号时按中标人反查中标公告（通知书常不带编号）
- 金额比对升级为融资视角：通知书金额 > 公告金额 = 超额融资风险
- 输出一页尽调单（结论/逐项核对/证据链/风险提示/口径免责声明）

用法：
    python scripts/receivable_due_diligence.py --winner "XX公司" \
        --amount "22.532万元" [--purchaser ...] [--bid-number ...] \
        [--finance-amount "20万元"] [--json] [--save 路径.txt]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# 兼容两种调用：pytest（项目根在 sys.path）与直接 `python scripts/xxx.py`
try:
    from scripts.verify_award import (
        DEFAULT_DB,
        _norm_bid_number,
        _norm_name,
        _to_yuan,
        verify_award,
    )
except ModuleNotFoundError:  # 直接运行时回退到同级目录导入
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from verify_award import (  # noqa: E402
        DEFAULT_DB,
        _norm_bid_number,
        _norm_name,
        _to_yuan,
        verify_award,
    )

_RISK_CRITICAL = "critical"
_RISK_WARNING = "warning"
_RISK_INFO = "info"


def _find_awards_by_winner(db_path, winner: str, limit: int = 10) -> list[dict]:
    """无编号兜底：按中标人检索 award 公告（win_company 精确/包含匹配）。"""
    norm = _norm_name(winner)
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT id, project_name, bid_number, win_company, win_amount, "
            "tender_org, source_url, publish_time FROM tenders "
            "WHERE notice_type = 'award' AND win_company IS NOT NULL "
            "AND win_company != ''"
        ).fetchall()
    finally:
        conn.close()
    hits = []
    for r in rows:
        db_name = _norm_name(r[3])
        if not db_name:
            continue
        if db_name == norm or (
            len(norm) >= 4 and (norm in db_name or db_name in norm)
        ):
            hits.append({
                "id": r[0], "project_name": r[1], "bid_number": r[2],
                "win_company": r[3], "win_amount": r[4], "tender_org": r[5],
                "source_url": r[6], "publish_time": r[7],
            })
        if len(hits) >= limit:
            break
    return hits


def run_due_diligence(
    winner: str,
    amount: str = "",
    purchaser: str = "",
    bid_number: str = "",
    finance_amount: str = "",
    db_path: str | os.PathLike | None = None,
) -> dict:
    """执行一页尽调核验，返回结构化结果（含 risks/checks/evidence/report_text）。"""
    if db_path is None:
        db_path = os.environ.get("BIDAGENT_DB") or DEFAULT_DB
    risks: list[dict] = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    if not winner and not bid_number:
        return _finish({
            "verdict": "cannot_verify",
            "reason": "未提供中标人与项目编号，无法启动核验",
            "risks": [{"level": _RISK_CRITICAL, "text": "尽调材料缺失：通知书无中标人且无项目编号"}],
        }, winner, amount, now, db_path)

    # 1) 存在性 + 一致性：复用核验器内核
    if bid_number:
        core = verify_award(bid_number, amount, winner, purchaser, db_path)
    else:
        # 无编号：按中标人反查，用第一条命中公告的编号走核验器
        candidates = _find_awards_by_winner(db_path, winner)
        if not candidates:
            return _finish({
                "verdict": "not_found",
                "reason": f"中标人『{winner}』在官方公告库中无任何中标记录",
                "risks": [{
                    "level": _RISK_CRITICAL,
                    "text": f"存在性不成立：库内无『{winner}』的中标公告——通知书对应项目可能不存在或不在库覆盖范围",
                }],
                "candidates": [],
            }, winner, amount, now, db_path)
        core = verify_award(
            candidates[0]["bid_number"] or "", amount, winner, purchaser, db_path
        )
        core["fallback_candidates"] = candidates

    verdict = core["verdict"]

    # 2) 金融风控增强
    if verdict == "fake":
        risks.append({
            "level": _RISK_CRITICAL,
            "text": "存在性不成立：项目编号在官方公告库中不存在——疑似伪造通知书，禁止放款",
        })
    elif verdict == "suspicious":
        risks.append({
            "level": _RISK_CRITICAL,
            "text": "字段不一致：通知书信息与官方公告存在不符项——需人工复核后方可放款",
        })

    # 超额融资检查：融资金额 > 公告中标金额
    fin_yuan = _to_yuan(finance_amount) if finance_amount else None
    notice_yuan = _to_yuan(amount) if amount else None
    db_amount = None
    for r in core.get("rows", []):
        v = _to_yuan(r.get("win_amount"))
        if v is not None:
            db_amount = v
            break
    if fin_yuan is not None and db_amount is not None and fin_yuan > db_amount:
        risks.append({
            "level": _RISK_CRITICAL,
            "text": (
                f"超额融资：申请融资 {fin_yuan:,.0f} 元 > 官方公告中标金额 "
                f"{db_amount:,.0f} 元，应收账款债权上限不支持该融资规模"
            ),
        })
    elif notice_yuan is not None and db_amount is not None and notice_yuan > db_amount:
        risks.append({
            "level": _RISK_WARNING,
            "text": f"通知书金额 {notice_yuan:,.0f} 元高于公告金额 {db_amount:,.0f} 元",
        })

    if verdict == "verified":
        risks.append({
            "level": _RISK_INFO,
            "text": "核验通过仅代表库内公告与通知书一致；库覆盖范围有限，建议辅以采购平台官网二次确认",
        })

    core["risks"] = risks
    return _finish(core, winner, amount, now, db_path)


def _finish(core: dict, winner: str, amount: str, now: str, db_path) -> dict:
    """补口径信息 + 渲染一页尽调单文本。"""
    conn = sqlite3.connect(str(db_path))
    try:
        n_award = conn.execute(
            "SELECT COUNT(*) FROM tenders WHERE notice_type='award'"
        ).fetchone()[0]
        n_number = conn.execute(
            "SELECT COUNT(DISTINCT bid_number) FROM tenders "
            "WHERE bid_number IS NOT NULL AND bid_number != ''"
        ).fetchone()[0]
        # 大件五：证据链升级为存证版——命中公告的 SHA-256 存证+入库时间+重放命令
        cert = None
        rows = core.get("rows") or []
        if rows and rows[0].get("bid_number"):
            cols = [r[1] for r in conn.execute("PRAGMA table_info(tenders)")]
            if "content_sha256" in cols:
                hit = conn.execute(
                    "SELECT id, content_sha256, created_at FROM tenders "
                    "WHERE bid_number = ? LIMIT 1",
                    (rows[0]["bid_number"],),
                ).fetchone()
                if hit:
                    cert = {
                        "row_id": hit[0],
                        "content_sha256": hit[1],
                        "ingested_at": hit[2],
                        "replay_cmd": f"python scripts/replay_evidence.py --id {hit[0]}",
                    }
    finally:
        conn.close()
    core["evidence_cert"] = cert
    core["caliber"] = {
        "generated_at": now,
        "db_award_count": n_award,
        "db_bid_number_count": n_number,
        "disclaimer": (
            "本尽调单基于公开招投标公告库比对，只陈述可观察事实，不构成信用评分；"
            "库未覆盖的项目无法证伪，需人工补充核查。"
        ),
    }
    core["report_text"] = _render_report(core, winner, amount)
    return core


def _render_report(core: dict, winner: str, amount: str) -> str:
    v_map = {
        "verified": "通过（与官方公告一致）",
        "suspicious": "存疑（字段不一致，需人工复核）",
        "fake": "不通过（存在性不成立）",
        "not_found": "不通过（库内无该中标人记录）",
        "cannot_verify": "无法核验（材料缺失）",
    }
    lines = [
        "=" * 62,
        "中标应收账款放款前核验 · 一页尽调单",
        "=" * 62,
        f"核验对象：中标人={winner or '（未提供）'}　通知书金额={amount or '（未提供）'}",
        f"核验结论：{v_map.get(core['verdict'], core['verdict'])}",
        f"理由：{core.get('reason', '')}",
        "",
        "── 逐项核对 ──",
    ]
    rows = core.get("rows", [])
    if rows:
        for r in rows[:3]:
            lines.append(f"公告：{r['project_name']}（编号 {r['bid_number']}）")
            for c in r.get("checks", []):
                tag = "一致" if c["match"] else "不符"
                lines.append(f"  [{tag}] {c['field']}: 通知书={c['input']!r} vs 官方={c['db']!r}")
    else:
        lines.append("（库内无可比对公告）")
    ev = core.get("evidence") or {}
    if ev.get("source_url"):
        lines += [
            "",
            "── 证据链 ──",
            f"来源链接：{ev['source_url']}",
            f"发布时间：{ev.get('publish_time', '未知')}",
        ]
        if ev.get("winner_line"):
            lines.append(f"原文证据：{ev['winner_line']}")
    cert = core.get("evidence_cert")
    if cert:
        if cert.get("content_sha256"):
            lines.append(f"存证 SHA-256：{cert['content_sha256']}")
        else:
            lines.append("存证 SHA-256：（该公告无正文，未存证）")
        lines.append(f"入库时间：{cert.get('ingested_at', '未知')}")
        lines.append(f"重放命令：{cert['replay_cmd']}")
    lines += ["", "── 风险提示 ──"]
    for rk in core.get("risks", []):
        lines.append(f"  [{rk['level'].upper()}] {rk['text']}")
    cal = core.get("caliber", {})
    lines += [
        "",
        "── 口径声明 ──",
        f"生成时间：{cal.get('generated_at', '')}",
        f"数据口径：官方中标公告库 {cal.get('db_award_count', 0)} 篇"
        f"（含编号 {cal.get('db_bid_number_count', 0)} 个）",
        cal.get("disclaimer", ""),
        "=" * 62,
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="中标应收账款放款前核验（一页尽调单）")
    ap.add_argument("--winner", default="", help="通知书上的中标人")
    ap.add_argument("--amount", default="", help="通知书上的中标金额（如 22.532万元）")
    ap.add_argument("--purchaser", default="", help="通知书上的采购人")
    ap.add_argument("--bid-number", default="", help="通知书上的项目编号（可选）")
    ap.add_argument("--finance-amount", default="", help="申请融资金额（用于超额融资检查）")
    ap.add_argument("--db", default=None, help="DB 路径")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--save", default=None, help="尽调单文本保存路径")
    args = ap.parse_args()

    result = run_due_diligence(
        winner=args.winner, amount=args.amount, purchaser=args.purchaser,
        bid_number=_norm_bid_number(args.bid_number),
        finance_amount=args.finance_amount, db_path=args.db,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(result["report_text"])
    if args.save:
        Path(args.save).write_text(result["report_text"], encoding="utf-8")


if __name__ == "__main__":
    main()
