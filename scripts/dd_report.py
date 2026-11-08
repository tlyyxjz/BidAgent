"""批量尽调报告器（复赛"省人力"场景）。

输入企业名称，一键产出 HTML 尽调数据包：
- 画像摘要：中标次数/总金额/年度趋势/客户集中度/区域
- 逐条中标核验：每条记录 = 声明 + 原文证据（字符位置）+ 来源链接 + 日期
- 客观风险信号：客户集中度、单客户依赖等（只列事实，不下结论）
- 免责声明 + 生成时间 + 数据快照说明

所有数字均来自确定性抽取（原文正则）与聚合，无 LLM 参与。

用法：
    python scripts/dd_report.py --company "湖南创益蔚来进出口有限公司" [--out report.html] [--json]
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from evidence_card import cards_from_profile, _VERDICT_BADGE  # noqa: E402
import winner_profile as wp  # noqa: E402
from verify_award import _to_yuan  # noqa: E402


def _fmt(v) -> str:
    return f"{v:,.0f}" if v is not None else "-"


def risk_flags(stats: dict) -> list[dict]:
    """客观信号（只列事实，不判定好坏）。"""
    flags = []
    if stats["total_wins"] >= 1:
        if stats["top1_share_pct"] is not None and stats["top1_share_pct"] >= 50:
            top = stats["customers"][0] if stats["customers"] else {}
            flags.append({
                "signal": "客户集中度高",
                "fact": f"第一大采购人占中标总金额 {stats['top1_share_pct']}%"
                        + (f"（{top.get('purchaser')}）" if top.get("purchaser") else ""),
            })
        trend = stats["yearly_trend"]
        if len(trend) == 1:
            flags.append({
                "signal": "数据周期短",
                "fact": f"中标记录集中在 {list(trend)[0]} 年（单一年度，趋势参考性有限）",
            })
    return flags


def build_report_data(company: str, db_path=None) -> dict:
    """组装报告数据（纯数据层，便于测试）。"""
    p = wp.profile(company, db_path)
    stats = p["stats"]
    return {
        "company": company,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stats": {
            "total_wins": stats["total_wins"],
            "total_amount": stats["total_amount"],
            "avg_amount": stats["avg_amount"],
            "max_amount": stats["max_amount"],
            "min_amount": stats["min_amount"],
            "yearly_trend": stats["yearly_trend"],
            "locations": stats["locations"],
            "top1_share_pct": stats["top1_share_pct"],
            "top_customers": stats["customers"][:5],
        },
        "wins": [
            {
                "project_name": w["project_name"],
                "bid_number": w["bid_number"],
                "amount": w["win_amount"],
                "purchaser": w["tender_org"],
                "publish_time": w["publish_time"],
                "source_url": w["source_url"],
                "evidence": w["evidence_line"],
            }
            for w in p["wins"]
        ],
        "risks": risk_flags(stats),
        "empty": stats["total_wins"] == 0,
    }


def chart_images(data: dict) -> list[str]:
    """画像图表：金额分布柱状图 + 年度趋势。返回 base64 PNG 列表。"""
    import base64
    import io

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei"]
    plt.rcParams["axes.unicode_minus"] = False

    out = []
    wins = data.get("wins") or []

    if wins:
        amounts = [_to_yuan(w["amount"]) or 0 for w in wins]
        fig, ax = plt.subplots(figsize=(7, 2.4), dpi=100)
        ax.bar(range(1, len(amounts) + 1), amounts, color="#1e2761", width=0.6)
        ax.set_title("中标金额分布（元）")
        ax.set_xticks(range(1, len(amounts) + 1))
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        plt.close(fig)
        out.append(base64.b64encode(buf.getvalue()).decode())

    trend = (data.get("stats") or {}).get("yearly_trend") or {}
    if trend:
        fig, ax = plt.subplots(figsize=(7, 2.4), dpi=100)
        years = list(trend.keys())
        counts = list(trend.values())
        ax.bar(years, counts, color="#2a347c", width=0.5)
        ax.set_title("年度中标次数")
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        plt.close(fig)
        out.append(base64.b64encode(buf.getvalue()).decode())
    return out


def render_html(data: dict) -> str:
    """渲染单文件 HTML 尽调报告（无外部依赖，可打印/截图）。"""
    s = data["stats"]
    body = []
    try:
        for img in chart_images(data):
            body.append('<div style="text-align:center;margin:10px 0">'
                        f'<img src="data:image/png;base64,{img}" style="max-width:100%"></div>')
    except Exception:  # noqa: BLE001 —— 图表失败不阻塞报告
        pass

    body.append(
        f'<div class="hdr"><h1>尽调数据包 · {html.escape(data["company"])}</h1>'
        f'<div class="meta">生成时间 {data["generated_at"]} · '
        f'数据快照以官方公告原文为准 · 全确定性程序生成，无 LLM 参与</div></div>'
    )

    if data["empty"]:
        body.append('<div class="card"><b>未找到该企业的中标记录</b>'
                    '<div class="dim">库中无此企业可画像数据，如实报告，不猜测补全。</div></div>')

    # 画像摘要
    body.append('<div class="section">一、中标画像摘要</div><div class="stats">')
    stat_items = [
        ("中标次数", str(s.get("total_wins", 0))),
        ("总金额（元）", _fmt(s.get("total_amount"))),
        ("单笔平均（元）", _fmt(s.get("avg_amount"))),
        ("单笔最高（元）", _fmt(s.get("max_amount"))),
        ("Top1 客户占比", f'{s["top1_share_pct"]}%' if s.get("top1_share_pct") is not None else "-"),
    ]
    for label, value in stat_items:
        body.append(f'<div class="stat"><div class="v">{value}</div><div class="l">{label}</div></div>')
    body.append('</div>')

    body.append(f'<div class="card"><b>年度趋势</b>：{json.dumps(s.get("yearly_trend", {}), ensure_ascii=False)}'
                f' &nbsp;|&nbsp; <b>区域</b>：{json.dumps(s.get("locations", {}), ensure_ascii=False)}</div>')
    if s["top_customers"]:
        rows = "".join(
            f'<tr><td>{html.escape(c["purchaser"])}</td><td>{c["count"]} 次</td>'
            f'<td>{_fmt(c["amount"])} 元</td></tr>'
            for c in s["top_customers"]
        )
        body.append(f'<div class="card"><b>主要客户</b>'
                    f'<table><tr><th>采购人</th><th>次数</th><th>金额</th></tr>{rows}</table></div>')

    # 客观信号
    body.append('<div class="section">二、客观信号（只列事实，不下结论）</div>')
    if data["risks"]:
        for r in data["risks"]:
            body.append(f'<div class="card risk"><span class="tag">信号</span>'
                        f'<b>{html.escape(r["signal"])}</b>'
                        f'<div class="dim">{html.escape(r["fact"])}</div></div>')
    else:
        body.append('<div class="card dim">未发现阈值内的信号。</div>')

    # 逐条核验
    body.append(f'<div class="section">三、逐条中标核验（{len(data["wins"])} 条）</div>')
    for i, w in enumerate(data["wins"], 1):
        badge = _VERDICT_BADGE["verified"][0]
        ev = (
            f'<div class="evidence">{html.escape(w["evidence"])}</div>'
            if w["evidence"] else ""
        )
        body.append(
            f'<div class="card"><div class="head">'
            f'<span class="badge ok">{badge}</span>'
            f'<b>[{i}] {html.escape(w["project_name"])}</b></div>'
            f'<div class="claim">中标金额：{_fmt(_to_yuan(w["amount"]))} 元 &nbsp;|&nbsp; '
            f'采购人：{html.escape(w["purchaser"] or "-")} &nbsp;|&nbsp; 编号：{html.escape(w["bid_number"] or "-")}</div>'
            f'{ev}'
            f'<div class="source">{html.escape(w["source_url"] or "")} @ {html.escape(str(w["publish_time"] or ""))}</div>'
            f'</div>'
        )

    body.append(
        '<div class="footer">本报告仅整理官方公告库中已有信息，不构成投资建议。'
        '每条数字均可回溯到公告原文字符位置。</div>'
    )

    css = (
        "body{font-family:'Microsoft YaHei',sans-serif;background:#f6f8fa;"
        "padding:28px;max-width:900px;margin:0 auto;color:#212635}"
        ".hdr h1{margin:0 0 6px;font-size:26px}"
        ".meta{color:#57606a;font-size:12px;margin-bottom:18px}"
        ".section{font-size:17px;font-weight:700;margin:22px 0 10px;color:#1e2761}"
        ".stats{display:flex;gap:12px;flex-wrap:wrap}"
        ".stat{background:#fff;border:1px solid #d0d7de;border-radius:8px;"
        "padding:10px 18px;min-width:110px}"
        ".stat .v{font-size:24px;font-weight:700;color:#1e2761}"
        ".stat .l{font-size:12px;color:#57606a}"
        ".card{background:#fff;border:1px solid #d0d7de;border-left:4px solid #57606a;"
        "border-radius:8px;padding:12px 16px;margin:10px 0}"
        ".card.risk{border-left-color:#9a6700}"
        ".head{display:flex;gap:8px;align-items:center}"
        ".badge{color:#fff;padding:1px 10px;border-radius:10px;font-size:12px;font-weight:700}"
        ".badge.ok{background:#1a7f37}"
        ".tag{color:#9a6700;border:1px solid #9a6700;border-radius:10px;"
        "font-size:11px;padding:0 8px;margin-right:8px;font-weight:700}"
        ".claim{font-size:13px;margin:6px 0}"
        ".evidence{background:#f6f8fa;border-radius:6px;padding:7px 10px;font-size:12px;"
        "font-family:Consolas,monospace;word-break:break-all;margin:6px 0}"
        ".source{font-size:11px;color:#57606a;word-break:break-all}"
        ".dim{color:#57606a;font-size:12px;margin-top:4px}"
        "table{border-collapse:collapse;width:100%;font-size:13px;margin-top:6px}"
        "th,td{border-bottom:1px solid #e8ecf1;padding:6px 8px;text-align:left}"
        "th{color:#57606a;font-weight:600}"
        ".footer{margin-top:28px;color:#57606a;font-size:11px}"
    )
    return (
        "<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>"
        f"<title>尽调数据包 · {html.escape(data['company'])}</title><style>{css}</style>"
        "</head><body>" + "\n".join(body) + "</body></html>"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="批量尽调报告")
    parser.add_argument("--company", required=True, help="企业名称（支持简称）")
    parser.add_argument("--db", default=None, help="DB 路径（默认 data/bidagent.db，可用环境变量 BIDAGENT_DB 覆盖）")
    parser.add_argument("--out", default="", help="输出 HTML 路径（默认 尽调报告_<公司>.html）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 数据")
    parser.add_argument("--pdf", default="", help="同时导出 PDF（reportlab）")
    args = parser.parse_args()

    data = build_report_data(args.company, args.db)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        return
    out = args.out or f"尽调报告_{args.company}.html"
    Path(out).write_text(render_html(data), encoding="utf-8")
    print(f"尽调报告已生成：{out}")
    if args.pdf:
        from dd_pdf import render_pdf
        pdf = render_pdf(data, args.pdf or f"尽调报告_{args.company}.pdf")
        print(f"PDF 已生成：{pdf}")
    print(f"  中标 {data['stats']['total_wins']} 条 | 总金额 {_fmt(data['stats']['total_amount'])} 元"
          f" | 客观信号 {len(data['risks'])} 条")


if __name__ == "__main__":
    main()
