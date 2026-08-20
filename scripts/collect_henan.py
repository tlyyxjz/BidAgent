# -*- coding: utf-8 -*-
"""河南省政府采购网（ccgp-henan.gov.cn）结果公告采集器（大件二省级批量·第一站）。

流程：robots 检查 → GET 首页 SSR（公告区块直出）→ 过滤中标/成交/结果类标题
→ 逐条 GET 详情页核验发布时间 → 列表元数据如实构建 payload → 去重入库 tenders。

站点结构说明（2026-08 实机逆向核对）：
- 首页 SSR 直出公告列表：`<li><span class="Right Gray">日期</span>
  <span class="danwei">单位</span><a href="/henan/content?infoId=N&channelCode=..."
  title="完整标题">`；
- 详情页正文为 PDF 附件渠道（zbwj.pdf 等），HTML 无中标人/金额字段
  → 本采集器为**列表元数据+详情核验模式**：win_amount/win_company/tender_org
  置 None（宁可缺、不可编），core_content 如实注明口径；
- /henan/ggcx 表单查询需会话 token 且 POST 空条件回表单页，未采用。

合规（全部由确定性机制保证）：
- robots.txt：启动时检查（探查实测 404，视为全允许）；
- 限流：每请求受 domain_rate_limiter 约束（默认 8 秒同域间隔）；
- 403 即停：任何 403 抛 Collect403 并终止；
- 不绕验证码/登录墙：正文是 PDF 附件渠道就只采元数据，不攻坚。

用法：
    python scripts/collect_henan.py --limit 3             # 采 3 条（默认 8s 间隔）
    python scripts/collect_henan.py --limit 3 --dry-run  # 只解析不入库
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

from app.core.rate_limiter import domain_rate_limiter  # noqa: E402
from app.core.robots_checker import robots_checker  # noqa: E402
from app.processors.ccgp_field_parser import (  # noqa: E402
    parse_notice_type,
)
from app.processors.simhash import compute_simhash  # noqa: E402

SITE_URL = "http://www.ccgp-henan.gov.cn"
HOME_PATH = "/"
DOMAIN = "www.ccgp-henan.gov.cn"
UA = "BidAgent/1.0 (+educational-research; compliant crawler)"


class Collect403(Exception):
    """403 即停异常（合规红线）。"""


# <li> 块：日期 + 单位（可选）+ 详情链接（title 为完整标题）
_LI_BLOCK_RE = re.compile(r"<li[\s\S]*?</li>", re.I)
_DATE_RE = re.compile(r'(20\d{2}-\d{2}-\d{2})')
_LINK_RE = re.compile(
    r'<a\s+href="(/henan/content\?[^"]*infoId=\d+[^"]*)"[^>]*'
    r'title="([^"]{6,120})"', re.I)
_UNIT_RE = re.compile(r'<span[^>]*class="[^"]*danwei[^"]*"[^>]*>([^<]*)</span>',
                      re.I)
_DETAIL_TIME_RE = re.compile(
    r'发布日期[\s\S]{0,120}?(20\d{2}-\d{2}-\d{2})[ T](\d{2}:\d{2})')
_DETAIL_ORG_RE = re.compile(
    r'发布机构[\s\S]{0,120}?>\s*([^<]{2,60}?)\s*<')


def parse_list(html: str) -> list[dict]:
    """解析首页 SSR 公告区块为 [{info_id, title, url, date, unit}]；
    按 infoId 去重，跳过无标题/无链接脏块。"""
    items: list[dict] = []
    seen: set[str] = set()
    for block in _LI_BLOCK_RE.findall(html or ""):
        m = _LINK_RE.search(block)
        if not m:
            continue
        href, title = m.group(1), re.sub(r"\s+", " ", m.group(2)).strip()
        info_m = re.search(r"infoId=(\d+)", href)
        if not info_m or not title:
            continue
        info_id = info_m.group(1)
        if info_id in seen:
            continue
        seen.add(info_id)
        d = _DATE_RE.search(block)
        u = _UNIT_RE.search(block)
        items.append({
            "info_id": info_id,
            "title": title,
            "url": SITE_URL + href,
            "date": d.group(1) if d else None,
            "unit": re.sub(r"\s+", " ", u.group(1)).strip() if u else None,
        })
    return items


def filter_result_items(items: list[dict], all_types: bool = False) -> list[dict]:
    """默认仅保留中标/成交/结果类标题；all_types=True 时不过滤（纯函数便于测试）。"""
    if all_types:
        return items
    return [it for it in items
            if any(k in it["title"] for k in ("中标", "成交", "结果"))]


def parse_detail(html: str) -> dict:
    """从详情页提取 publish_time(datetime|None)/publish_org；无则 None。"""
    m = _DETAIL_TIME_RE.search(html or "")
    pt = None
    if m:
        try:
            pt = datetime.strptime(f"{m.group(1)} {m.group(2)}",
                                   "%Y-%m-%d %H:%M")
        except ValueError:
            pt = None
    org = None
    mo = _DETAIL_ORG_RE.search(html or "")
    if mo:
        org = re.sub(r"\s+", " ", mo.group(1)).strip() or None
    return {"publish_time": pt, "publish_org": org}


def compose_text(rec: dict, detail: dict | None) -> str:
    """列表元数据+详情核验信息如实拼成记录文本（正文为 PDF 附件渠道，不虚构）。"""
    detail = detail or {}
    pt = detail.get("publish_time")
    publish = pt.strftime("%Y-%m-%d %H:%M") if pt else (rec.get("date") or "")
    lines = [f"公告标题：{rec['title']}"]
    if rec.get("unit"):
        lines.append(f"发布单位：{rec['unit']}")
    if detail.get("publish_org") and detail["publish_org"] != rec.get("unit"):
        lines.append(f"发布机构：{detail['publish_org']}")
    if publish:
        lines.append(f"发布时间：{publish}")
    lines.append("来源：河南省政府采购网（首页结果公告区块 SSR 直出）")
    lines.append("口径说明：本站公告正文为 PDF 附件渠道，公开页面仅提供元数据；"
                 "本记录按元数据如实入库，中标人/金额等字段以原文附件为准。")
    return "\n".join(lines)


def _to_signed_simhash(h: int) -> int:
    """simhash 转 64 位有符号（与 PostgreSQL BIGINT 对齐，同其他采集器）。"""
    h &= (1 << 64) - 1
    return h - (1 << 64) if h >= (1 << 63) else h


def build_payload(rec: dict, detail: dict | None = None) -> dict:
    """由列表记录+详情核验构建入库字段（纯函数，便于测试）。

    列表元数据模式：win_amount/win_company/tender_org 公开页面不提供，
    置 None（宁可缺、不可编）；publish_time 优先详情页发布日期，
    回落列表日期（0 点）。大件五存证：collect 直入路径不走 _build_tender，
    必须在此落 SHA-256。
    """
    detail = detail or {}
    pt = detail.get("publish_time")
    if pt is None and rec.get("date"):
        try:
            pt = datetime.strptime(rec["date"], "%Y-%m-%d")
        except ValueError:
            pt = None
    text = compose_text(rec, detail)
    _core = text[:2000]
    content_sha = hashlib.sha256(_core.encode("utf-8")).hexdigest() if _core else None
    raw_sha = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None
    return {
        "project_name": rec["title"],
        "bid_number": None,
        "win_amount": None,
        "tender_org": None,
        "publish_time": pt,
        "notice_type": parse_notice_type(rec["title"], text) or "award",
        "win_company": None,
        "source_platform": "henan",
        "source_url": rec["url"],
        "core_content": _core,
        "source_raw_text": text,
        "content_sha256": content_sha,
        "raw_text_sha256": raw_sha,
        "simhash": _to_signed_simhash(compute_simhash(text)),
    }


def _raise_if_403(resp, url: str) -> None:
    if resp.status_code == 403:
        raise Collect403(f"403 即停: {url}")


async def fetch_home(client: httpx.AsyncClient) -> str:
    """GET 首页；403 即停，非 200 抛 RuntimeError。"""
    url = SITE_URL + HOME_PATH
    await domain_rate_limiter.wait(url)
    resp = await client.get(url)
    _raise_if_403(resp, url)
    if resp.status_code != 200:
        raise RuntimeError(f"fetch failed: {url} status={resp.status_code}")
    return resp.text


async def fetch_detail(client: httpx.AsyncClient, url: str) -> str | None:
    """GET 详情页；403 即停；其他异常返回 None（单条跳过不炸整源）。"""
    await domain_rate_limiter.wait(url)
    try:
        resp = await client.get(url)
    except httpx.HTTPError:
        return None
    _raise_if_403(resp, url)
    if resp.status_code != 200:
        return None
    return resp.text


async def collect_payloads(client: httpx.AsyncClient, limit: int = 3,
                           all_types: bool = False) -> list[dict]:
    """首页一次 → 过滤 → 逐条详情核验 → payload。"""
    html = await fetch_home(client)
    items = filter_result_items(parse_list(html), all_types=all_types)
    results: list[dict] = []
    for rec in items:
        if len(results) >= limit:
            break
        detail_html = await fetch_detail(client, rec["url"])
        detail = parse_detail(detail_html) if detail_html else None
        p = build_payload(rec, detail)
        results.append(p)
        print(f"  [{p['notice_type']}] {p['project_name'][:40]}"
              f" | {p['publish_time']}")
    return results


async def persist_results(results: list[dict]) -> tuple[int, int]:
    """按 source_url 去重入库，返回 (新增, 跳过)。"""
    from app.models.database import AsyncSessionLocal
    from app.models.tender import Tender
    from sqlalchemy import select

    added = skipped = 0
    async with AsyncSessionLocal() as db:
        for p in results:
            exists = (
                await db.execute(
                    select(Tender.id).where(Tender.source_url == p["source_url"]))
            ).first()
            if exists:
                skipped += 1
                continue
            db.add(Tender(**p))
            added += 1
        await db.commit()
    return added, skipped


async def _main_async(args) -> None:
    if not await robots_checker.is_allowed(SITE_URL, UA):
        raise SystemExit("robots 禁止采集，已停止")

    domain_rate_limiter.set_interval(DOMAIN, args.interval)
    async with httpx.AsyncClient(
        headers={"User-Agent": UA}, follow_redirects=True, timeout=25.0,
        trust_env=False,  # 不走本机代理（间歇性 ReadTimeout）
    ) as client:
        payloads = await collect_payloads(client, limit=args.limit,
                                          all_types=args.all_types)
        print(f"构建 payload {len(payloads)} 条")
        if args.dry_run:
            print("[dry-run] 不入库")
            return
        if not payloads:
            print("无可入库记录")
            return
        added, skipped = await persist_results(payloads)
        print(f"入库完成：新增 {added}，去重跳过 {skipped}")


def main() -> None:
    parser = argparse.ArgumentParser(description="河南省政府采购网结果公告采集")
    parser.add_argument("--limit", type=int, default=3, help="最多采集条数")
    parser.add_argument("--interval", type=float, default=8.0,
                        help="同域请求间隔秒数（合规默认 8）")
    parser.add_argument("--dry-run", action="store_true", help="只解析不入库")
    parser.add_argument("--all-types", action="store_true",
                        help="不过滤标题，采集列表全部类型")
    args = parser.parse_args()
    try:
        asyncio.run(_main_async(args))
    except Collect403 as exc:
        print(f"[合规停止] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
