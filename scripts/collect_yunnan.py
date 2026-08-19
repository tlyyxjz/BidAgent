"""云南省政府采购网（ccgp-yunnan.gov.cn）中标/成交公告采集器（第三源）。

流程：robots 检查 → POST 首页公告接口(gghtlist, TYPE=2 结果公告/bxlx007) →
过滤标题 → 逐条 GET 详情片段 /ggInfo.html?bulletin_id=X → 去标签 →
ccgp_field_parser 抽字段 → 中标人正则抽取 → 入库 tenders。

站点结构说明：
- 列表：/api/firstpage/firstpage.gghtlist.svc（POST LEVEL=1&TYPE=2），
  返回 JSON 数组，字段 BULLETIN_ID/BULLETINTITLE/FINISHDAY/BULLETINCLASS；
  分页搜索接口 Procurement.searchForMainList.svc 带滑块验证码，不使用。
- 详情：/ggInfo.html?bulletin_id=<BULLETIN_ID>（服务端渲染 HTML 片段，
  含 showBulletinInfoDiv，无验证码）。

合规（全部由确定性机制保证）：
- robots.txt：启动时检查，禁止即停（云南站 User-agent:* 仅禁 /04285c08de.html）；
- 限流：每请求受 domain_rate_limiter 约束（默认 8 秒同域间隔）；
- 403 即停：任何 403 抛 Collect403 并终止；
- 不绕验证码：带验证码的搜索接口一律不用；不绕登录墙。

用法：
    python scripts/collect_yunnan.py --limit 5            # 采 5 条（默认 8s 间隔）
    python scripts/collect_yunnan.py --limit 3 --dry-run  # 只解析不入库
"""
from __future__ import annotations

import argparse
import asyncio
import json
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

BASE_URL = "http://www.ccgp-yunnan.gov.cn/"
LIST_API = "/api/firstpage/firstpage.gghtlist.svc"
DETAIL_PATH = "/ggInfo.html?bulletin_id={bid}"
RESULT_TYPE = "2"  # 首页公告接口 TYPE=2 = 结果公告（bxlx007）
UA = "BidAgent/1.0 (+educational-research; compliant crawler)"


class Collect403(Exception):
    """403 即停异常（合规红线）。"""


_TITLE_RE = re.compile(r"<title>([\s\S]*?)</title>")
# 脏记录特征：非 ID 形态（如"云南省采购成交纪录"占位行 id=sddfucggg）
_VALID_ID_RE = re.compile(r"^[A-Za-z0-9._\-]+$")


def strip_tags(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", text).strip()


def parse_list_json(payload) -> list[dict]:
    """解析 gghtlist 响应。兼容裸数组与 {"code":..,"data":[..]} 两种形态。

    返回 [{bulletin_id, title, date}]；跳过无效 ID/无效日期占位记录
    （如 id=sddfucggg、FINISHDAY="/" 的脏记录）。
    """
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    items: list[dict] = []
    for o in rows:
        if not isinstance(o, dict):
            continue
        bid = str(o.get("BULLETIN_ID") or "").strip()
        title = re.sub(r"\s+", " ", str(o.get("BULLETINTITLE") or "")).strip()
        date = str(o.get("FINISHDAY") or "").strip()
        if not bid or not _VALID_ID_RE.fullmatch(bid):
            continue  # 占位/脏记录
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date or ""):
            continue  # 无有效日期的占位记录（如 FINISHDAY="/"）
        if title:
            items.append({"bulletin_id": bid, "title": title, "date": date})
    return items


def parse_detail_title(html: str) -> str | None:
    m = _TITLE_RE.search(html or "")
    if not m:
        return None
    t = re.sub(r"<[^>]+>", " ", m.group(1))
    t = re.sub(r"\s+", " ", t).strip()
    return t or None


def _cell_text(cell: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", cell)).strip()


def _accept_winner(name: str, seen: set[str], winners: list[str]) -> None:
    name = name.rstrip(";； ").strip()
    if len(name) >= 4 and name not in seen and not re.fullmatch(r"[\d\.\-/]+", name):
        seen.add(name)
        winners.append(name)


_KV_KEY_RE = re.compile(r"(?:中标|成交)（?供应商|供应商名称")


def extract_winners_from_table(html: str) -> list[str]:
    """从云南结果公告的表格抽中标人（确定性表格解析，非推断）。

    覆盖两种真实形态：
    1. 键值行：<td class="title">中标供应商</td><td>昆明道实科技有限公司;</td>；
    2. 结果表格：表头行含"供应商名称"，取后续数据行该列的值。
    strip_tags 后键名与值间无冒号，verify_award 的文本正则无法覆盖，故单独解析。
    """
    winners: list[str] = []
    seen: set[str] = set()
    for table_m in re.finditer(r"<table[\s\S]*?</table>", html or "", flags=re.I):
        rows = re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", table_m.group(0), flags=re.I)
        col = -1
        for row in rows:
            cells = [_cell_text(c) for c in
                     re.findall(r"<(?:td|th)[^>]*>([\s\S]*?)</(?:td|th)>", row, flags=re.I)]
            if not cells:
                continue
            # 形态 1：键值行（键单元格 + 值单元格）
            if len(cells) >= 2 and re.fullmatch(_KV_KEY_RE, cells[0]):
                _accept_winner(cells[1], seen, winners)
                continue
            # 形态 2：表头定位"供应商名称"列
            if col < 0:
                for i, c in enumerate(cells):
                    if "供应商名称" in c:
                        col = i
                        break
                continue
            if 0 <= col < len(cells):
                _accept_winner(cells[col], seen, winners)
    return winners


def _to_signed_simhash(h: int) -> int:
    """与 tender_ingestor 一致：uint64 simhash 归一为 int64 有符号存储。"""
    h &= 0xFFFFFFFFFFFFFFFF
    return h - 0x10000000000000000 if h >= 0x8000000000000000 else h


def build_payload(item: dict, detail_html: str) -> dict:
    """由列表项 + 详情页 HTML 构建入库字段（纯函数，便于测试）。"""
    text = strip_tags(detail_html)
    winners = _extract_winners_from_text(text) or extract_winners_from_table(detail_html)
    win_amount = parse_win_amount(text)
    publish_time = parse_publish_time(text)
    if publish_time is None and item.get("date"):
        try:
            publish_time = datetime.strptime(item["date"], "%Y-%m-%d")
        except ValueError:
            publish_time = None
    return {
        "project_name": item["title"],
        "bid_number": parse_bid_number(text),
        "win_amount": Decimal(str(win_amount)) if win_amount is not None else None,
        "tender_org": parse_tender_org(text),
        "publish_time": publish_time,
        "notice_type": parse_notice_type(item["title"], text) or "award",
        "win_company": "、".join(winners) if winners else None,
        "source_platform": "yunnan",
        "source_url": BASE_URL.rstrip("/") + DETAIL_PATH.format(bid=item["bulletin_id"]),
        "core_content": text[:2000],
        "source_raw_text": text,
        "simhash": _to_signed_simhash(compute_simhash(text)),
    }


def _raise_if_403(resp, url: str) -> None:
    if resp.status_code == 403:
        raise Collect403(f"403 即停: {url}")


async def _fetch(client: httpx.AsyncClient, url: str) -> str:
    await domain_rate_limiter.wait(url)
    resp = await client.get(url)
    _raise_if_403(resp, url)
    if resp.status_code != 200:
        raise RuntimeError(f"fetch failed: {url} status={resp.status_code}")
    return resp.text


async def _fetch_list(client: httpx.AsyncClient, level: str = "1") -> list[dict]:
    """抓首页结果公告列表（TYPE=2）。带 Referer 头（站点要求）。"""
    await domain_rate_limiter.wait(BASE_URL)
    resp = await client.post(
        BASE_URL.rstrip("/") + LIST_API,
        data={"LEVEL": level, "TYPE": RESULT_TYPE},
        headers={"Referer": BASE_URL, "X-Requested-With": "XMLHttpRequest"},
    )
    _raise_if_403(resp, LIST_API)
    if resp.status_code != 200:
        raise RuntimeError(f"list failed: status={resp.status_code}")
    try:
        payload = json.loads(resp.text)
    except json.JSONDecodeError:
        return []
    return parse_list_json(payload)


async def _main_async(args) -> None:
    if not await robots_checker.is_allowed(BASE_URL, UA):
        raise SystemExit("robots 禁止采集，已停止")

    domain_rate_limiter.set_interval("www.ccgp-yunnan.gov.cn", args.interval)
    async with httpx.AsyncClient(
        headers={"User-Agent": UA}, follow_redirects=True,
        # 禁用系统代理（httpx 默认读环境变量，本机代理会导致间歇性 ReadTimeout）
        trust_env=False,
        timeout=25.0,
    ) as client:
        try:
            items = await _fetch_list(client)
        except httpx.TransportError as exc:
            # D5 真机：站点/链路偶发 ReadTimeout，隔几秒重试一次
            #（限流器保证间隔合规）；再失败才报错
            print(f"列表首抓失败（{type(exc).__name__}），重试一次...")
            items = await _fetch_list(client)
        if not items:
            # 接口偶发 HTTP 200 + 体内 status=500“系统异常”（解析为空列表），
            # 隔几秒重试即恢复；最多重试一次，限流器保证间隔合规（同实时适配器口径）
            print("列表为空，重试一次...")
            items = await _fetch_list(client)
        if not args.all_types:
            kept = [it for it in items
                    if any(k in it["title"] for k in ("中标", "成交", "结果"))]
            print(f"列表解析 {len(items)} 条，过滤中标/成交/结果后 {len(kept)} 条"
                  f"（跳过 {len(items) - len(kept)} 条非结果类）")
            items = kept
        else:
            print(f"列表解析 {len(items)} 条")
        items = items[: args.limit]

        results = []
        for it in items:
            url = BASE_URL.rstrip("/") + DETAIL_PATH.format(bid=it["bulletin_id"])
            try:
                detail_html = await _fetch(client, url)
            except httpx.TransportError as exc:
                print(f"  [跳过] {it['title'][:40]} 抓取失败: {exc}")
                continue
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
    parser = argparse.ArgumentParser(description="云南政府采购网中标/成交公告采集")
    parser.add_argument("--limit", type=int, default=5, help="最多采集条数")
    parser.add_argument("--interval", type=float, default=8.0, help="同域请求间隔秒数（合规默认 8）")
    parser.add_argument("--dry-run", action="store_true", help="只解析不入库")
    parser.add_argument("--all-types", action="store_true", help="不过滤标题，采集列表全部类型")
    args = parser.parse_args()
    try:
        asyncio.run(_main_async(args))
    except Collect403 as exc:
        print(f"[合规停止] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
