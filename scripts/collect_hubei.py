"""湖北省政府采购网（ccgp-hubei.gov.cn）中标公告采集器（第二源）。

流程：robots 检查 → GET 首页 → 解析 #area-1-4 中标(成交) tab → 逐条 GET 详情 →
去标签 → ccgp_field_parser 抽字段 → 中标人正则抽取 → 入库 tenders。

合规（全部由确定性机制保证）：
- robots.txt：启动时检查，禁止即停（湖北站无 robots.txt = 无禁则）；
- 限流：每请求受 domain_rate_limiter 约束（默认 8 秒同域间隔）；
- 403 即停：任何 403 抛 Collect403 并终止；
- 不绕登录墙：本源无需登录。

用法：
    python scripts/collect_hubei.py --limit 5          # 采 5 条（默认 8s 间隔）
    python scripts/collect_hubei.py --limit 3 --dry-run  # 只解析不入库
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

from app.core.rate_limiter import domain_rate_limiter  # noqa: E402
from app.core.robots_checker import robots_checker  # noqa: E402
from app.processors.ccgp_field_parser import (  # noqa: E402
    parse_bid_number,
    parse_notice_type,
    parse_publish_time,
    parse_tender_org,
    parse_win_amount,
)
from app.processors.simhash import compute_simhash  # noqa: E402
from verify_award import _extract_winners_from_text  # noqa: E402

BASE_URLS = ["https://www.ccgp-hubei.gov.cn/", "http://www.ccgp-hubei.gov.cn/"]
UA = "BidAgent/1.0 (+educational-research; compliant crawler)"


class Collect403(Exception):
    """403 即停异常（合规红线）。"""


_LIST_RE = re.compile(
    r'<li>\s*<a href="(/notice/\d{6}/notice_[0-9a-f]{32}\.html)"[^>]*>([\s\S]*?)</a>'
    r"\s*<span>(\d{4}-\d{2}-\d{2})</span>"
)
_H2_TITLE_RE = re.compile(r"<h2[^>]*>\s*<span[^>]*>([\s\S]*?)</span>")


def strip_tags(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", text).strip()


def parse_list(html: str) -> list[dict]:
    """解析首页中标 tab 列表。返回 [{path, title, date}]。"""
    items: list[dict] = []
    for m in _LIST_RE.finditer(html):
        raw_title = m.group(2)
        title = re.sub(r"\[[\s\S]*?\]", "", raw_title)  # 去掉 [竞争性磋商] 前缀
        title = re.sub(r"\s+", " ", title).strip()
        if title:
            items.append({"path": m.group(1), "title": title, "date": m.group(3)})
    return items


def parse_detail_title(html: str) -> str | None:
    m = _H2_TITLE_RE.search(html)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else None


def _to_signed_simhash(h: int) -> int:
    """与 tender_ingestor 一致：uint64 simhash 归一为 int64 有符号存储。"""
    h &= 0xFFFFFFFFFFFFFFFF
    return h - 0x10000000000000000 if h >= 0x8000000000000000 else h


def build_payload(item: dict, detail_html: str) -> dict:
    """由列表项 + 详情页 HTML 构建入库字段（纯函数，便于测试）。"""
    title = parse_detail_title(detail_html) or item["title"]
    text = strip_tags(detail_html)
    winners = _extract_winners_from_text(text)
    win_amount = parse_win_amount(text)
    publish_time = parse_publish_time(text)
    if publish_time is None and item.get("date"):
        try:
            publish_time = datetime.strptime(item["date"], "%Y-%m-%d")
        except ValueError:
            publish_time = None
    return {
        "project_name": title,
        "bid_number": parse_bid_number(text),
        "win_amount": Decimal(str(win_amount)) if win_amount is not None else None,
        "tender_org": parse_tender_org(text),
        "publish_time": publish_time,
        "notice_type": parse_notice_type(title, text) or "award",
        "win_company": "、".join(winners) if winners else None,
        "source_platform": "hubei",
        "source_url": item["url"],
        "core_content": text[:2000],
        "source_raw_text": text,
        "simhash": _to_signed_simhash(compute_simhash(text)),
    }


async def _fetch(client: httpx.AsyncClient, url: str) -> str:
    await domain_rate_limiter.wait(url)
    for base in (url, url.replace("https://", "http://", 1) if url.startswith("https://") else url):
        try:
            resp = await client.get(base)
        except httpx.TransportError:
            if base == url:
                continue
            raise
        if resp.status_code == 403:
            raise Collect403(f"403 即停: {base}")
        if resp.status_code == 200:
            return resp.text
        if base == url:
            continue
    raise RuntimeError(f"fetch failed: {url}")


async def _main_async(args) -> None:
    if not await robots_checker.is_allowed(BASE_URLS[0], UA):
        raise SystemExit("robots 禁止采集，已停止")

    domain_rate_limiter.set_interval("www.ccgp-hubei.gov.cn", args.interval)
    async with httpx.AsyncClient(
        headers={"User-Agent": UA}, follow_redirects=True, timeout=20.0,
    ) as client:
        home_html = await _fetch(client, BASE_URLS[0])
        items = parse_list(home_html)
        if not args.all_types:
            kept = [it for it in items if ("中标" in it["title"] or "成交" in it["title"])]
            print(f"列表解析 {len(items)} 条，过滤中标/成交后 {len(kept)} 条（跳过 {len(items) - len(kept)} 条非中标类）")
            items = kept
        else:
            print(f"列表解析 {len(items)} 条")
        items = items[: args.limit]

        results = []
        for it in items:
            it["url"] = BASE_URLS[0].rstrip("/") + it["path"]
            detail_html = await _fetch(client, it["url"])
            payload = build_payload(it, detail_html)
            results.append(payload)
            print(
                f"  [{payload['notice_type']}] {payload['project_name'][:40]}"
                f" | 编号 {payload['bid_number']} | 金额 {payload['win_amount']}"
                f" | 中标人 {payload['win_company']} | {payload['publish_time']}"
            )

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
                await db.execute(select(Tender.id).where(Tender.source_url == p["source_url"]))
            ).first()
            if exists:
                skipped += 1
                continue
            db.add(Tender(**p))
            added += 1
        await db.commit()
    print(f"入库完成：新增 {added} / 跳过(已存在) {skipped}")


def main() -> None:
    parser = argparse.ArgumentParser(description="湖北政府采购网采集")
    parser.add_argument("--limit", type=int, default=5, help="最多采集条数")
    parser.add_argument("--interval", type=float, default=8.0, help="同域请求间隔秒数（合规默认 8）")
    parser.add_argument("--dry-run", action="store_true", help="只解析不入库")
    parser.add_argument("--all-types", action="store_true", help="不过滤标题，采集所有类型")
    args = parser.parse_args()
    try:
        asyncio.run(_main_async(args))
    except Collect403 as exc:
        print(f"[合规停止] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
