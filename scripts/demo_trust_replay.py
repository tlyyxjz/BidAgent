# -*- coding: utf-8 -*-
"""一键自动演示：输入 → 判定 → 证据 → 佐证 → 存证，五步单页报告。

演示动线（评委 90 秒看懂"数据可信"）：
  [1] 输入   从真实库选一条公告（默认自动选证据最充分的一条）
  [2] 判定   抽取字段清单（present/absent 如实呈现）
  [3] 证据   每条证据现场重放 core_content[start:end) ≡ 证据文本
             切片不符即判失败（宁可少给、不可编造）
  [4] 佐证   跨源交叉验证（app.services.cross_validation 纯函数）
             有跨源匹配→corroborated；否则如实标注 single_source 孤证
  [5] 存证   SHA-256 重算比对，证明入库后原文未被篡改

产出：
  - 控制台逐步叙事（带 [1/5]…[5/5] 步骤号与通过/失败标记）
  - 单页 HTML 报告 data/demo_auto_play_report.html（墨档 Ink Archive 风格）

零网络、零 LLM、纯本地可重放。退出码：0=全部通过，1=存在失败。

用法：
    python scripts/demo_trust_replay.py                 # 自动选公告
    python scripts/demo_trust_replay.py --id 114        # 指定公告
    python scripts/demo_trust_replay.py --out my.html   # 自定义报告路径

与根目录 demo_auto_play.py（Playwright 浏览器录屏演示）分工：
本脚本是数据层可信重放（零网络零 LLM），根目录脚本是页面动线录屏。
"""
from __future__ import annotations

import argparse
import hashlib
import html
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_DB = ROOT / "data" / "bidagent.db"
DEFAULT_OUT = ROOT / "data" / "demo_trust_replay_report.html"

# cross_validation 依赖链可能触达 app.config 强校验：独立运行给默认值
import os  # noqa: E402
os.environ.setdefault("SECRET_KEY", "a" * 64)
os.environ.setdefault("ADMIN_SECRET", "demo-admin-secret")
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{DEFAULT_DB}")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from app.services.cross_validation import (  # noqa: E402
    NoticeRef,
    corroborate_notice,
)

FIELD_LABELS = {
    "project_identifier": "项目编号",
    "project_name": "项目名称",
    "purchaser_name": "采购单位",
    "agency_name": "代理机构",
    "winner_name": "中标单位",
    "amount": "核心金额",
    "budget_amount": "预算金额",
    "publish_date": "发布日期",
    "bid_deadline": "投标截止",
    "notice_type": "公告类型",
}


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 数据读取
# ---------------------------------------------------------------------------

def load_tender(cur: sqlite3.Cursor, tender_id: int) -> dict | None:
    row = cur.execute(
        "SELECT id, project_name, bid_number, notice_type, source_platform, "
        "source_url, core_content, content_sha256, created_at "
        "FROM tenders WHERE id = ?", (tender_id,)).fetchone()
    if row is None:
        return None
    return dict(zip(
        ("id", "project_name", "bid_number", "notice_type", "source_platform",
         "source_url", "core_content", "content_sha256", "created_at"), row))


def load_fields(cur: sqlite3.Cursor, tender_id: int) -> list[dict]:
    rows = cur.execute(
        "SELECT field_name, field_status, raw_value, support_level "
        "FROM extracted_fields WHERE tender_id = ? AND is_current = 1 "
        "ORDER BY id", (tender_id,)).fetchall()
    return [dict(zip(("field_name", "field_status", "raw_value",
                      "support_level"), r)) for r in rows]


def load_evidences(cur: sqlite3.Cursor, tender_id: int) -> list[dict]:
    rows = cur.execute(
        "SELECT f.field_name, f.raw_value, l.evidence_role, e.evidence_text, "
        "e.raw_start, e.raw_end, e.match_method, e.confidence, e.verified "
        "FROM field_evidence_links l "
        "JOIN extracted_fields f ON l.field_id = f.id "
        "JOIN evidence e ON l.evidence_id = e.id "
        "WHERE f.tender_id = ? ORDER BY f.id, l.sequence",
        (tender_id,)).fetchall()
    return [dict(zip(("field_name", "raw_value", "role", "text", "start",
                      "end", "match_method", "confidence", "verified"), r))
            for r in rows]


def load_all_refs(cur: sqlite3.Cursor) -> list[NoticeRef]:
    rows = cur.execute(
        "SELECT id, source_platform, bid_number, project_name, simhash "
        "FROM tenders").fetchall()
    return [NoticeRef(tender_id=r[0], source_platform=r[1] or "",
                      bid_number=r[2], project_name=r[3], simhash=r[4])
            for r in rows]


def replay_pass_count(cur: sqlite3.Cursor, tender_id: int) -> tuple[int, int]:
    """快速预检：该公告证据可重放数 (ok, total)。"""
    core = cur.execute(
        "SELECT core_content FROM tenders WHERE id = ?",
        (tender_id,)).fetchone()
    if core is None or not core[0]:
        return (0, 0)
    text = core[0]
    rows = cur.execute(
        "SELECT e.evidence_text, e.raw_start, e.raw_end "
        "FROM field_evidence_links l "
        "JOIN extracted_fields f ON l.field_id = f.id "
        "JOIN evidence e ON l.evidence_id = e.id "
        "WHERE f.tender_id = ?", (tender_id,)).fetchall()
    ok = sum(1 for t, s, e in rows
             if s is not None and e is not None
             and 0 <= s < e <= len(text) and text[s:e] == t)
    return (ok, len(rows))


def pick_default_id(cur: sqlite3.Cursor, refs: list[NoticeRef]) -> int | None:
    """自动选公告：证据最多的前 12 条里，优先选“有跨源佐证且证据全部可重放”。

    降级链：佐证+可重放 → 仅可重放 → 证据最多（如实展示失败）。
    """
    rows = cur.execute(
        "SELECT f.tender_id, COUNT(*) AS c FROM field_evidence_links l "
        "JOIN extracted_fields f ON l.field_id = f.id "
        "GROUP BY f.tender_id ORDER BY c DESC, f.tender_id LIMIT 12").fetchall()
    if not rows:
        return None
    cand_ids = [r[0] for r in rows]
    replayable: list[int] = []
    for tid in cand_ids:
        ok, total = replay_pass_count(cur, tid)
        if total and ok == total:
            replayable.append(tid)
            target = next((r for r in refs if r.tender_id == tid), None)
            if target is not None and corroborate_notice(target, refs).matches:
                return tid
    if replayable:
        return replayable[0]
    return cand_ids[0]


# ---------------------------------------------------------------------------
# 五步核验
# ---------------------------------------------------------------------------

def run_demo(tender_id: int, db_path: Path) -> dict:
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    t0 = time.monotonic()

    tender = load_tender(cur, tender_id)
    if tender is None:
        conn.close()
        return {"ok": False, "error": f"公告 {tender_id} 不存在"}

    fields = load_fields(cur, tender_id)
    evidences = load_evidences(cur, tender_id)
    refs = load_all_refs(cur)
    conn.close()

    raw = tender["core_content"] or ""

    # [3] 证据重放：core_content[start:end) ≡ 证据文本
    ev_ok = 0
    for ev in evidences:
        start, end = ev["start"], ev["end"]
        bounds_ok = (start is not None and end is not None
                     and 0 <= start < end <= len(raw))
        slice_ok = bounds_ok and raw[start:end] == ev["text"]
        ev["replay_ok"] = bool(slice_ok)
        ev["slice"] = raw[start:end] if slice_ok else ""
        if not slice_ok:
            ev["fail_reason"] = ("偏移越界" if not bounds_ok
                                 else "切片与证据文本不一致（原文已清洗/历史证据失效）")
        if slice_ok:
            ev_ok += 1

    # [4] 跨源佐证
    target = next((r for r in refs if r.tender_id == tender_id), None)
    x_result = corroborate_notice(target, refs) if target else None

    # [5] 存证哈希
    stored_sha = tender["content_sha256"] or ""
    recomputed = sha256_hex(raw) if raw else ""
    hash_ok = bool(stored_sha) and stored_sha == recomputed

    all_ok = ev_ok == len(evidences) and hash_ok  # 0 证据不算失败（无证据可重放）
    return {
        "ok": all_ok,
        "tender": tender,
        "fields": fields,
        "evidences": evidences,
        "ev_ok": ev_ok,
        "x_result": x_result,
        "hash_ok": hash_ok,
        "stored_sha": stored_sha,
        "recomputed_sha": recomputed,
        "elapsed_ms": int((time.monotonic() - t0) * 1000),
    }


# ---------------------------------------------------------------------------
# 控制台叙事
# ---------------------------------------------------------------------------

def print_narrative(r: dict) -> None:
    if r.get("error"):
        print(f"[失败] {r['error']}")
        return
    t = r["tender"]
    print("=" * 64)
    print("标小智 · 一键自动演示（输入 → 判定 → 证据 → 佐证 → 存证）")
    print("=" * 64)
    print(f"[1/5 输入] 公告 #{t['id']} · {t['source_platform']}")
    print(f"         {t['project_name']}")
    print(f"         编号 {t['bid_number'] or '（无）'} · 入库 {t['created_at']}")

    present = [f for f in r["fields"] if f["field_status"] == "present"]
    absent = [f for f in r["fields"] if f["field_status"] != "present"]
    print(f"[2/5 判定] 抽取字段 {len(r['fields'])} 个：present {len(present)}，"
          f"absent {len(absent)}（absent 如实标注，不编造）")
    for f in present:
        label = FIELD_LABELS.get(f["field_name"], f["field_name"])
        print(f"         ✓ {label}: {f['raw_value'] or '—'}")
    for f in absent:
        label = FIELD_LABELS.get(f["field_name"], f["field_name"])
        print(f"         · {label}: （原文无此信息，标注 absent）")

    if not r["evidences"]:
        print("[3/5 证据] · 该公告库内无存证证据，无可重放项（如实展示）")
    else:
        mark = "✓" if r["ev_ok"] == len(r["evidences"]) else "✗"
        print(f"[3/5 证据] {mark} 证据重放 {r['ev_ok']}/{len(r['evidences'])} 通过"
              f"（core_content[start:end) ≡ 证据文本，切片不符即拒绝）")
    for ev in r["evidences"]:
        flag = "✓" if ev["replay_ok"] else "✗"
        label = FIELD_LABELS.get(ev["field_name"], ev["field_name"])
        print(f"         {flag} {label} ← 「{ev['text'][:36]}」"
              f"[{ev['start']}:{ev['end']}] {ev['match_method']}")

    x = r["x_result"]
    if x is None:
        print("[4/5 佐证] — 无候选集，跳过")
    elif x.matches:
        print(f"[4/5 佐证] ✓ corroborated：{len(x.matches)} 条跨源佐证")
        for m in x.matches[:3]:
            print(f"         ✓ #{m.tender_id} {m.source_platform} · {m.reason}")
    else:
        print("[4/5 佐证] · single_source：库内无跨源匹配，如实标注孤证")

    h = "✓" if r["hash_ok"] else "✗"
    print(f"[5/5 存证] {h} SHA-256 重算{'一致' if r['hash_ok'] else '不一致'}："
          f"{(r['recomputed_sha'] or '无')[:16]}…")

    print("-" * 64)
    verdict = "全部通过：判定有据、证据可重放、原文未篡改" if r["ok"] else "存在失败项，请查看报告"
    print(f"结论：{verdict}（{r['elapsed_ms']}ms，零网络零 LLM）")


# ---------------------------------------------------------------------------
# 单页 HTML 报告（墨档 Ink Archive 风格）
# ---------------------------------------------------------------------------

def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def render_context(raw: str, ev: dict) -> str:
    """证据上下文：前后各 24 字符，证据区间 <mark> 高亮。失败如实展示原因。"""
    if not ev["replay_ok"]:
        return f"<span class='fail'>（重放失败：{esc(ev.get('fail_reason', '切片不符'))}，拒绝展示）</span>"
    start, end = ev["start"], ev["end"]
    before = raw[max(0, start - 24):start]
    after = raw[end:end + 24]
    return esc(before) + "<mark>" + esc(raw[start:end]) + "</mark>" + esc(after)


def render_report(r: dict) -> str:
    t = r["tender"]
    x = r["x_result"]
    if x is None:
        x_html = "<div class='muted'>无候选集，跳过佐证判定</div>"
    elif x.matches:
        items = "".join(
            f"<li><b>#{m.tender_id}</b> · {esc(m.source_platform)} · "
            f"{esc(m.level)} · {esc(m.reason)}</li>"
            for m in x.matches[:5])
        x_html = (f"<div class='badge ok'>corroborated · {len(x.matches)} 条跨源佐证</div>"
                   f"<ul class='xv'>{items}</ul>")
    else:
        x_html = ("<div class='badge warn'>single_source · 孤证</div>"
                  "<div class='muted'>库内无跨源匹配，如实标注——宁可少给、不可编造。</div>")

    field_rows = "".join(
        f"<tr><td>{esc(FIELD_LABELS.get(f['field_name'], f['field_name']))}</td>"
        f"<td>{esc(f['raw_value']) or '<span class=muted>—</span>'}</td>"
        f"<td><span class='st {'st-ok' if f['field_status'] == 'present' else 'st-no'}'>"
        f"{esc(f['field_status'])}</span></td>"
        f"<td>{esc(f['support_level'] or '—')}</td></tr>"
        for f in r["fields"])

    ev_rows = "".join(
        f"<tr><td>{'✓' if ev['replay_ok'] else '✗'}</td>"
        f"<td>{esc(FIELD_LABELS.get(ev['field_name'], ev['field_name']))}</td>"
        f"<td class='ctx'>{render_context(t['core_content'] or '', ev)}</td>"
        f"<td class='mono'>[{ev['start']}:{ev['end']}]</td>"
        f"<td>{esc(ev['match_method'])}</td></tr>"
        for ev in r["evidences"])

    if r["evidences"]:
        ev_badge = (f"<span class='badge {'ok' if r['ev_ok'] == len(r['evidences']) else 'warn'}'>"
                    f"{r['ev_ok']}/{len(r['evidences'])} 通过</span>")
        ev_table = ("<table><thead><tr><th></th><th>字段</th><th>原文切片（证据高亮）</th>"
                    "<th>偏移</th><th>匹配</th></tr></thead>"
                    f"<tbody>{ev_rows}</tbody></table>"
                    "<div class='muted' style='margin-top:8px'>切片不符即拒绝展示——宁可少给、不可编造。</div>")
    else:
        ev_badge = "<span class='badge warn'>库内无存证证据</span>"
        ev_table = "<div class='muted'>该公告未入库证据，无可重放项——如实展示，不编造。</div>"

    hcls = "ok" if r["hash_ok"] else "bad"
    verdict = ("全部通过：判定有据 · 证据可重放 · 原文未篡改" if r["ok"]
               else "存在失败项（证据切片或存证哈希不一致）")
    vcls = "ok" if r["ok"] else "bad"

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>标小智 · 一键自动演示报告 #{t['id']}</title>
<style>
:root {{--primary:#2E4B7A; --seal:#B23E28; --bg:#F5F3EC; --card:#FFFDF8;
 --line:#E3DECF; --ink:#2B2A26; --t2:#6B675C; --t3:#9A9484; --green:#3E7A4E;}}
* {{box-sizing:border-box; margin:0; padding:0;}}
body {{background:var(--bg); color:var(--ink);
 font-family:"Source Han Serif SC","Noto Serif CJK SC",Georgia,serif;
 padding:32px 16px; line-height:1.6;}}
.wrap {{max-width:960px; margin:0 auto;}}
header {{border-bottom:3px double var(--primary); padding-bottom:14px; margin-bottom:22px;}}
h1 {{font-size:24px; color:var(--primary); letter-spacing:1px;}}
.sub {{font-size:13px; color:var(--t2); margin-top:6px;}}
.seal {{display:inline-block; color:var(--seal); border:2px solid var(--seal);
 padding:2px 10px; font-size:13px; margin-left:10px; letter-spacing:2px;}}
section {{background:var(--card); border:1px solid var(--line); border-radius:10px;
 padding:16px 20px; margin-bottom:16px;}}
h2 {{font-size:16px; color:var(--primary); margin-bottom:10px;}}
h2 .step {{color:var(--seal); margin-right:8px;}}
table {{width:100%; border-collapse:collapse; font-size:13px;}}
th,td {{text-align:left; padding:6px 8px; border-bottom:1px solid var(--line);}}
th {{color:var(--t3); font-weight:600; font-size:12px;}}
mark {{background:#F6E3A8; padding:0 2px; border-radius:2px;}}
.ctx {{font-family:"SFMono-Regular",Consolas,monospace; font-size:12px; color:var(--t2);}}
.mono {{font-family:Consolas,monospace; font-size:12px; color:var(--t3);}}
.muted {{color:var(--t3); font-size:13px;}}
.badge {{display:inline-block; padding:3px 12px; border-radius:20px; font-size:13px;}}
.badge.ok {{background:#E8F1EA; color:var(--green); border:1px solid #B9D4C0;}}
.badge.warn {{background:#F7EDE4; color:var(--seal); border:1px solid #E0C4B2;}}
.st-ok {{color:var(--green);}} .st-no {{color:var(--t3);}}
.fail {{color:var(--seal);}}
ul.xv {{margin:8px 0 0 20px; font-size:13px; color:var(--t2);}}
.verdict {{text-align:center; font-size:15px; padding:14px; border-radius:10px;}}
.verdict.ok {{background:#E8F1EA; color:var(--green); border:1px solid #B9D4C0;}}
.verdict.bad {{background:#F7E4E0; color:var(--seal); border:1px solid #E0B2A8;}}
footer {{text-align:center; font-size:12px; color:var(--t3); margin-top:20px;}}
.sha {{font-family:Consolas,monospace; font-size:12px; word-break:break-all;}}
</style></head><body><div class="wrap">
<header>
  <h1>标小智 · 一键自动演示报告<span class="seal">可重放</span></h1>
  <div class="sub">公告 #{t['id']} · {esc(t['source_platform'])} · {esc(t['notice_type'] or '')} ·
    编号 {esc(t['bid_number'] or '（无）')} · 入库 {esc(t['created_at'])}</div>
  <div class="sub" style="margin-top:2px"><b>{esc(t['project_name'])}</b></div>
</header>

<section>
  <h2><span class="step">[2]</span>判定 · 抽取字段（absent 如实标注）</h2>
  <table><thead><tr><th>字段</th><th>值</th><th>状态</th><th>支撑等级</th></tr></thead>
  <tbody>{field_rows}</tbody></table>
</section>

<section>
  <h2><span class="step">[3]</span>证据 · 现场重放（core_content[start:end) ≡ 证据文本）
    {ev_badge}</h2>
  {ev_table}
</section>

<section>
  <h2><span class="step">[4]</span>佐证 · 跨源交叉验证（编号严格一致 / 标题相似度 ≥ 0.80）</h2>
  {x_html}
</section>

<section>
  <h2><span class="step">[5]</span>存证 · SHA-256 完整性</h2>
  <div class="badge {'ok' if r['hash_ok'] else 'warn'}">
    {'✓ 重算一致，入库后原文未被篡改' if r['hash_ok'] else '✗ 哈希不一致或缺存证'}</div>
  <div class="sha" style="margin-top:8px">存证 {esc(r['stored_sha'] or '—')}<br>
    重算 <span class="{hcls}">{esc(r['recomputed_sha'] or '—')}</span></div>
</section>

<div class="verdict {vcls}">{verdict} · 耗时 {r['elapsed_ms']}ms · 零网络 · 零 LLM</div>
<footer>标小智 v4.1 · GOAI 2026 复赛 · 金标 620 篇十源 · python scripts/demo_trust_replay.py</footer>
</div></body></html>"""


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="一键自动演示（输入→判定→证据→佐证→存证）")
    ap.add_argument("--id", type=int, default=None, help="tenders.id（默认自动选）")
    ap.add_argument("--db", default=None, help="DB 路径（默认 data/bidagent.db）")
    ap.add_argument("--out", default=None, help="报告输出路径")
    args = ap.parse_args()

    db_path = Path(args.db) if args.db else DEFAULT_DB
    if not db_path.exists():
        print(f"DB 不存在: {db_path}", file=sys.stderr)
        sys.exit(2)

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    refs = load_all_refs(cur)
    tender_id = args.id or pick_default_id(cur, refs)
    conn.close()
    if tender_id is None:
        print("库内无证据数据，无法演示（请先跑抽取流水线）", file=sys.stderr)
        sys.exit(2)

    result = run_demo(tender_id, db_path)
    print_narrative(result)
    if result.get("error"):
        sys.exit(2)

    out_path = Path(args.out) if args.out else DEFAULT_OUT
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_report(result), encoding="utf-8")
    print(f"报告已生成：{out_path}")
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
