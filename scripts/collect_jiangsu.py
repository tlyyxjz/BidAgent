# -*- coding: utf-8 -*-
"""江苏省政府采购网（ccgp-jiangsu.gov.cn）采集器（第三源）。

列表接口（D5 实机验证，GET，无验证码）：
    /jscms/js_lmapi/api/getArticeData/lists/z_jggg_2
    （首页 index.js createData() 逆向：gglb_area，z_jggg=结果公告、2=省级）
    返回 {"code":200,"result":[{title,id,ggCode,projId,summary,...}]}（每次 7 条）；
    列表条目的 ggCode/id 与详情页同构 → 直接走详情接口拿全文。

详情数据接口（实机验证，GET 可用）：
    /pss/jsp/relevantCgggListByProjId.jsp?gglb=<类型>&ggid=<公告id>&projId=
    返回 {"msg":"OK","cgxm":{projNumber/projName/buyerName/agentName...},
          "data":[{"summary":<公告全文>,"publishDate":...,"title":...,"type":...}]}

注：分页搜索接口 search_cggg.jsp 需要验证码，不绕，不用（合规红线）。

合规：robots.txt 无禁则（404）；同域限流 8s；403 即停。

用法：
    python scripts/collect_jiangsu.py --limit 5 [--dry-run]
    python scripts/collect_jiangsu.py --notice "gglb=gkzb&ggid=5a5424043b67413f9c63707d8e300470" --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

from app.core.rate_limiter import domain_rate_limiter  # noqa: E402
from app.core.robots_checker import robots_checker  # noqa: E402
from app.processors.ccgp_field_parser import parse_fields  # noqa: E402
from app.processors.simhash import compute_simhash  # noqa: E402
from verify_award import _extract_winners_from_text  # noqa: E402

BASE = "http://www.ccgp-jiangsu.gov.cn"
API = BASE + "/pss/jsp/relevantCgggListByProjId.jsp"
# D5：结果公告（中标类）列表接口，GET 无验证码（首页 createData 逆向，实机验证）；
# 后缀 2=省级/3/4 为首页区域 tab（各返约 7 条），1 无数据
LIST_API = BASE + "/jscms/js_lmapi/api/getArticeData/lists/z_jggg_{area}"
UA = "BidAgent/1.0 (+educational-research; compliant crawler)"

_LINK_RE = re.compile(
    r'href="(/jiangsu/js_cggg/details\.html\?gglb=(\w+)&ggid=([0-9a-f]{20,}))"'
)


class Collect403(Exception):
    """403 即停异常（合规红线）。"""


async def _fetch(client: httpx.AsyncClient, url: str) -> str:
    await domain_rate_limiter.wait(url)
    resp = await client.get(url)
    if resp.status_code == 403:
        raise Collect403(f"403 即停: {url}")
    if resp.status_code != 200:
        raise RuntimeError(f"fetch {resp.status_code}: {url}")
    return resp.text


def parse_homepage_links(html: str) -> list[dict]:
    """从首页提取公告链接。返回 [{gglb, ggid, url}]。"""
    seen = set()
    out = []
    for m in _LINK_RE.finditer(html):
        key = (m.group(2), m.group(3))
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "gglb": m.group(2),
            "ggid": m.group(3),
            "url": BASE + m.group(1),
        })
    return out


async def fetch_award_links(client: httpx.AsyncClient, limit: int,
                            areas: tuple[str, ...] = ("2",)) -> list[dict]:
    """D5：结果公告列表接口拿中标类链接（GET 无验证码，每区域约 7 条）。

    接口本身按公告分类返回（z_jggg=结果公告），条目即中标/成交类；
    仍按标题做一次防御性过滤（宁可少给、不可编造）。
    多个区域 tab（2=省级/3/4）可合并去重凑数，够 limit 即停。
    """
    seen: set[str] = set()
    links: list[dict] = []
    for area in areas:
        raw = await _fetch(client, LIST_API.format(area=area))
        payload = json.loads(raw)
        if payload.get("code") != 200:
            raise RuntimeError(f"list api code={payload.get('code')} area={area}")
        for it in payload.get("result") or []:
            gglb = it.get("ggCode") or "zbgg"
            ggid = it.get("id") or ""
            title = it.get("title") or ""
            if not ggid or ggid in seen:
                continue
            if not any(k in title for k in ("中标", "成交", "结果")):
                continue
            seen.add(ggid)
            links.append({
                "gglb": gglb,
                "ggid": ggid,
                "url": (f"{BASE}/jiangsu/js_cggg/details.html"
                        f"?gglb={gglb}&ggid={ggid}"),
            })
            if len(links) >= limit:
                return links
    return links


def parse_notice_payload(payload: dict) -> dict:
    """接口 JSON -> 入库字段（纯函数，可测）。"""
    cgxm = payload.get("cgxm") or {}
    items = payload.get("data") or []
    first = items[0] if items else {}
    title = (first.get("title") or cgxm.get("projName") or "").strip()
    summary = (first.get("summary") or "").strip()
    fields = parse_fields(title, summary)
    winners = _extract_winners_from_text(summary)
    # 发布时间：优先接口 publishDate 字段（summary 内换行易截断编号/时间）
    publish_time = fields.get("publish_time")
    if publish_time is None and first.get("publishDate"):
        from datetime import datetime
        try:
            publish_time = datetime.strptime(first["publishDate"][:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            publish_time = None
    return {
        "project_name": title,
        # 编号优先用接口权威字段（summary 中长编号可能被换行截断）
        "bid_number": (cgxm.get("projNumber") or "").strip() or fields.get("bid_number"),
        "win_amount": fields.get("win_amount"),
        "tender_org": fields.get("tender_org") or cgxm.get("buyerName"),
        "agency": cgxm.get("agentName"),
        "location": fields.get("location") or first.get("zoneName"),
        "publish_time": publish_time,
        "notice_type": fields.get("notice_type") or "tender",
        "win_company": "、".join(winners) if winners else None,
        "source_platform": "jiangsu",
        "source_url": first.get("url") or "",
        "core_content": summary[:2000],
        "source_raw_text": summary,
        "simhash": compute_simhash(summary) & 0x7FFFFFFFFFFFFFFF,
    }


async def _main_async(args) -> None:
    if not await robots_checker.is_allowed(BASE + "/", UA):
        raise SystemExit("robots 禁止采集，已停止")

    domain_rate_limiter.set_interval("www.ccgp-jiangsu.gov.cn", args.interval)
    async with httpx.AsyncClient(
        headers={"User-Agent": UA}, follow_redirects=True,
        # 禁用系统代理（httpx 默认读环境变量，本机代理会导致间歇性 ReadTimeout）
        trust_env=False,
        timeout=20.0,
    ) as client:
        if args.notice:
            links = [dict(gglb=args.notice.split("&")[0].split("=")[1],
                          ggid=args.notice.split("&")[1].split("=")[1],
                          url=BASE + "/jiangsu/js_cggg/details.html?" + args.notice)]
        elif args.all_types:
            home = await _fetch(client, BASE + "/")
            links = parse_homepage_links(home)[: args.limit]
        else:
            # D5：默认走结果公告列表接口（自带中标类分类，
            # 首页头部多为非中标类，旧扫描模式命中率太低）；
            # 区域 tab 2/3/4 合并去重凑数（各约 7 条）
            links = await fetch_award_links(client, args.limit,
                                            areas=("2", "3", "4"))
        print(f"待采公告 {len(links)} 条")

        results = []
        for link in links:
            api_url = f"{API}?gglb={link['gglb']}&ggid={link['ggid']}&projId="
            raw = await _fetch(client, api_url)
            payload = json.loads(raw.lstrip("\ufeff"))
            if payload.get("msg") != "OK":
                print(f"  接口异常，跳过 {link['ggid']}")
                continue
            p = parse_notice_payload(payload)
            p["source_url"] = link["url"]
            results.append(p)
            print(f"  [{p['notice_type']}] {p['project_name'][:36]}"
                  f" | 编号 {p['bid_number']} | 金额 {p['win_amount']}"
                  f" | 采购人 {p['tender_org']} | {p['publish_time']}")

    if args.dry_run:
        print("dry-run：未入库")
        return

    from app.models.database import AsyncSessionLocal
    from app.models.tender import Tender
    from sqlalchemy import select

    added = skipped = 0
    async with AsyncSessionLocal() as db:
        for p in results:
            exists = (
                await db.execute(
                    select(Tender.id).where(Tender.source_url == p["source_url"])
                )
            ).first()
            if exists:
                skipped += 1
                continue
            db.add(Tender(**p))
            added += 1
        await db.commit()
    print(f"入库完成：新增 {added} / 跳过(已存在) {skipped}")


def main() -> None:
    ap = argparse.ArgumentParser(description="江苏政府采购网采集")
    ap.add_argument("--limit", type=int, default=5, help="最多采集中标类条数（结果公告列表接口每次约 7 条）")
    ap.add_argument("--notice", default="", help="单条公告（gglb=xx&ggid=yy）")
    ap.add_argument("--interval", type=float, default=8.0, help="同域请求间隔秒数（合规默认 8）")
    ap.add_argument("--dry-run", action="store_true", help="只解析不入库")
    ap.add_argument("--all-types", action="store_true", help="回退模式：不过滤标题，采首页头部链接")
    args = ap.parse_args()
    try:
        asyncio.run(_main_async(args))
    except Collect403 as exc:
        print(f"[合规停止] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
