# -*- coding: utf-8 -*-
"""天津市政府采购网（ccgp-tianjin.gov.cn）中标/成交公告采集器（15城·首城试点）。

站点为 SSR 传统门户：首页即含公告直链列表（2026-08 实机验证）。

契约（实机核对）：
- 列表：GET https://www.ccgp-tianjin.gov.cn/ 首页 HTML；
  公告链接形态 <a href="/viewer.do?id=<数字>&ver=2" title="<完整标题>">，
  同 <li> 内 <span class="times">Thu Aug 20 16:19:39 CST 2026</span>
  （Java Date.toString 格式，本地解析，不依赖 locale）；
  首页混有工作动态/政策类链接 → 按标题关键词过滤仅留中标/成交/结果类。
- 详情：GET {SITE}/viewer.do?id=<id>&ver=2 → SSR HTML；
  正文为表格展平格式（供应商名称/供应商地址/中标（成交）金额），
  共享解析器冒号型正则不全覆盖 → 本地 fallback 正则补位。
- source_url 存可浏览详情页 viewer.do?id=...。

合规（全部由确定性机制保证）：
- robots.txt：启动时检查，禁止即停（实机 robots.txt 返回空体，
  无任何 Disallow 规则，按 RFC 9309 惯例视为全允许）；
- 限流：每请求受 domain_rate_limiter 约束（默认 8 秒同域间隔）；
- 403 即停：任何 403 抛 Collect403 并终止；
- 不绕登录墙/验证码；单条详情失败降级跳过不阻断。

用法：
    python scripts/collect_tianjin.py --limit 5            # 采 5 条（默认 8s 间隔）
    python scripts/collect_tianjin.py --limit 3 --dry-run  # 只解析不入库
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import html as _html
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

SITE_URL = "https://www.ccgp-tianjin.gov.cn"
HOME_PATH = "/"
DETAIL_PATH = "/viewer.do?id={vid}&ver=2"
DOMAIN = "www.ccgp-tianjin.gov.cn"
UA = "BidAgent/1.0 (+educational-research; compliant crawler)"


class Collect403(Exception):
    """403 即停异常（合规红线）。"""


_TAG_RE = re.compile(r"<[^>]+>")
_CELL_RE = re.compile(r"<t[dh][^>]*>([\s\S]*?)</t[dh]>", re.I)
# 首页公告链接 + 标题（title 属性含完整标题，比锚文本可靠）
_LINK_RE = re.compile(
    r'href="(/viewer\.do\?id=(\d+)&ver=2)"[^>]*title="([^"]+)"')
# 同 li 内日期 span（Java Date.toString）
_DATE_SPAN_RE = re.compile(
    r'href="/viewer\.do\?id=(\d+)&ver=2"[^>]*title="([^"]+)"'
    r'[\s\S]{0,300}?<span class="times">([^<]+)</span>')
# Java Date 月份/星期映射（不依赖 locale）
_MONTHS = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
           "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
_JAVA_DATE_RE = re.compile(
    r"[A-Z][a-z]{2} ([A-Z][a-z]{2}) (\d{1,2}) (\d{2}):(\d{2}):(\d{2}) "
    r"[A-Z]{2,5} (\d{4})")

# 天津详情表格展平格式 fallback（仅在共享解析器未命中时启用；
# 与山东同形态：「供应商名称 供应商地址 中标（成交）金额 … XX有限公司 …」）
_TJ_WINNER_RES = (
    re.compile(r"供应商名称\s+供应商地址\s+[\s\S]{0,60}?"
               r"((?:[\u4e00-\u9fa5A-Za-z0-9（）()·]{2,40}?)"
               r"(?:有限公司|公司|集团|中心|研究院|医院|大学|学校))"),
    re.compile(r"(?:中标|成交)供应商(?:名称)?\s+"
               r"((?:[^\s]{2,40}?)(?:有限公司|公司|集团|中心|研究院|医院))"),
)
_TJ_AMOUNT_RE = re.compile(
    r"(?:中标|成交)[（(]?中标[)）]?\s*金额[：:]?\s*"
    r"([\d,]+(?:\.\d+)?)\s*万?元")


def strip_tags(html: str) -> str:
    """去标签并压空白（纯函数）。先解 HTML 实体——实机发现天津表头用
    &#20013;&#26631;金额 形式编码“中标金额”，不解码会导致金额正则失配。"""
    text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = _TAG_RE.sub(" ", text)
    text = _html.unescape(text)
    text = re.sub(r"&nbsp;", " ", text)
    return re.sub(r"[ \t]+", " ", text)


def parse_cells(html: str) -> list[str]:
    """把详情 HTML 的所有表格单元格展平成有序列表（纯函数）。

    天津中标公告为表格形态：表头行（供应商名称/供应商地址/
    中标金额(万元)/评审得分）后紧跟数据行。跨行展平文本的正则
    会误匹配，故先解实体再按单元格抽取，用表头定位数据。
    """
    cells: list[str] = []
    for m in _CELL_RE.finditer(html):
        inner = re.sub(r"<[^>]+>", " ", m.group(1))
        inner = _html.unescape(inner)
        inner = re.sub(r"\s+", " ", inner).strip()
        cells.append(inner)
    return cells


def tj_extract_from_cells(cells: list[str]) -> tuple[list[str], Decimal | None]:
    """从单元格列表定位中标供应商与中标金额（诚实：找不到就空）。

    策略：找含“供应商名称”的表头行，其后第一个含公司后缀的
    单元格即中标人；金额取同数据行的万元数值（表头含“金额”），
    找不到同行则取中标人后的第一个万元数值。
    """
    winners: list[str] = []
    amount: Decimal | None = None
    company_rx = re.compile(
        r"^[\u4e00-\u9fa5A-Za-z0-9（）()·]{2,40}?"
        r"(?:有限公司|公司|集团|中心|研究院|医院|事务所)$")
    header_idx = [i for i, c in enumerate(cells) if c == "供应商名称"]
    for hi in header_idx:
        # 该表头组的列数：到下一个“供应商名称”或行结束（启发式取 8 列窗口）
        nxt = next((j for j in header_idx if j > hi), len(cells))
        window = cells[hi:min(nxt, hi + 12)]
        comp = next((c for c in window if company_rx.match(c)), None)
        if comp and comp not in winners:
            winners.append(comp)
            if amount is None:
                # 金额：窗口内第一个形如 123.45 的纯数值单元格
                # （排除统一社会信用代码与电话：信用代码 18 位、电话带 -）
                for c in window:
                    if re.fullmatch(r"\d{1,3}(?:,\d{3})*\.\d{1,6}", c) or \
                            re.fullmatch(r"\d+\.\d{1,6}", c):
                        try:
                            amount = Decimal(c.replace(",", ""))
                            break
                        except Exception:  # noqa: BLE001
                            continue
    # 兜底：无表头形态时，取全文第一个公司名 + 其后万元数值
    if not winners:
        comp = next((c for c in cells if company_rx.match(c)), None)
        if comp:
            winners.append(comp)
    return winners, amount


def parse_java_date(s: str) -> datetime | None:
    """解析 Java Date.toString 格式（Thu Aug 20 16:19:39 CST 2026）。"""
    m = _JAVA_DATE_RE.search(s or "")
    if not m:
        return None
    mon = _MONTHS.get(m.group(1))
    if mon is None:
        return None
    try:
        return datetime(int(m.group(6)), mon, int(m.group(2)),
                        int(m.group(3)), int(m.group(4)), int(m.group(5)))
    except ValueError:  # pragma: no cover - 防御
        return None


def parse_list(html: str) -> list[dict]:
    """从首页 HTML 解析公告列表（纯函数，便于测试）。

    返回 [{id, title, url, publish_dt}]，按 id 去重保持出现顺序。
    """
    items: list[dict] = []
    seen: set[str] = set()
    for m in _DATE_SPAN_RE.finditer(html):
        vid, title, date_str = m.group(1), m.group(2), m.group(3)
        if vid in seen:
            continue
        seen.add(vid)
        items.append({
            "id": vid,
            "title": title.strip(),
            "url": SITE_URL + DETAIL_PATH.format(vid=vid),
            "publish_dt": parse_java_date(date_str),
        })
    # 兜底：无日期 span 的链接也纳入（日期留空）
    for m in _LINK_RE.finditer(html):
        vid, title = m.group(2), m.group(3)
        if vid in seen:
            continue
        seen.add(vid)
        items.append({
            "id": vid,
            "title": title.strip(),
            "url": SITE_URL + DETAIL_PATH.format(vid=vid),
            "publish_dt": None,
        })
    return items


def tj_extract_winners(text: str) -> list[str]:
    """天津表格展平格式中标人 fallback（共享解析器未命中时启用）。"""
    for rx in _TJ_WINNER_RES:
        m = rx.search(text)
        if m:
            return [m.group(1).strip()]
    return []


def tj_extract_amount(text: str) -> Decimal | None:
    """天津表格金额 fallback。"""
    m = _TJ_AMOUNT_RE.search(text)
    if not m:
        return None
    try:
        return Decimal(m.group(1).replace(",", ""))
    except Exception:  # noqa: BLE001 - 防御
        return None


def build_payload(item: dict, detail_html: str) -> dict:
    """由列表项 + 详情 HTML 构建入库字段（纯函数，便于测试）。

    抽取优先级：共享解析器（冒号型）→ 表格单元格定位（天津主形态）
    → 展平文本 fallback。金额单位统一为万元口径（天津表头即万元，
    存储原值不做换算，与既有库一致：金额字段存公告原文数值）。
    """
    text = strip_tags(detail_html)
    cell_winners, cell_amount = tj_extract_from_cells(parse_cells(detail_html))
    winners = (_extract_winners_from_text(text)
               or cell_winners or tj_extract_winners(text))
    win_amount = parse_win_amount(text) or cell_amount or tj_extract_amount(text)
    publish_time = parse_publish_time(text) or item.get("publish_dt")
    # 大件五存证：入库即存证（collect 直入路径不走 _build_tender，
    # 必须在此处同样落 SHA-256；空文本不落证）
    _core = text[:2000]
    content_sha = hashlib.sha256(_core.encode("utf-8")).hexdigest() if _core else None
    raw_sha = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None
    return {
        "project_name": item["title"],
        "bid_number": parse_bid_number(text),
        "win_amount": Decimal(str(win_amount)) if win_amount is not None else None,
        "tender_org": parse_tender_org(text),
        "publish_time": publish_time,
        "notice_type": parse_notice_type(item["title"], text) or "award",
        "win_company": "、".join(winners) if winners else None,
        "source_platform": "tianjin",
        "source_url": item["url"],
        "core_content": _core,
        "source_raw_text": text,
        "content_sha256": content_sha,
        "raw_text_sha256": raw_sha,
        "simhash": _to_signed_simhash(compute_simhash(text)),
    }


def _to_signed_simhash(h: int) -> int:
    """simhash 转 64 位有符号（与 PostgreSQL BIGINT 对齐，同其他采集器）。"""
    h &= (1 << 64) - 1
    return h - (1 << 64) if h >= (1 << 63) else h


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


def filter_result_items(items: list[dict], all_types: bool = False) -> list[dict]:
    """默认仅保留中标/成交/结果类公告；all_types=True 时不过滤（纯函数便于测试）。"""
    if all_types:
        return items
    return [it for it in items
            if any(k in it["title"] for k in ("中标", "成交", "结果"))]


async def collect_detail_payloads(client, items: list[dict]) -> list[dict]:
    """逐条抓详情并构建入库 payload；抓取失败的条目跳过不阻断。"""
    results: list[dict] = []
    for it in items:
        try:
            detail_html = await _fetch(client, it["url"])
        except httpx.TransportError as exc:
            print(f"  [跳过] {it['title'][:40]} 抓取失败: {exc}")
            continue
        except RuntimeError as exc:
            print(f"  [跳过] {it['title'][:40]} 详情异常: {exc}")
            continue
        payload = build_payload(it, detail_html)
        if not payload["bid_number"] and not payload["win_company"]:
            # 诚实原则：编号与中标人都抽不到的详情不入中标库
            print(f"  [跳过] {it['title'][:40]} 无有效字段")
            continue
        results.append(payload)
        print(
            f"  [{payload['notice_type']}] {payload['project_name'][:40]}"
            f" | 编号 {payload['bid_number']} | 金额 {payload['win_amount']}"
            f" | 中标人 {payload['win_company']} | {payload['publish_time']}"
        )
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
        home_html = await _fetch(client, SITE_URL + HOME_PATH)
        items = parse_list(home_html)
        print(f"列表解析 {len(items)} 条")
        if not items:
            print("[警告] 列表为空：页面结构可能变化（待实机核对），已停止")
            return
        items = filter_result_items(items, all_types=args.all_types)
        print(f"标题过滤后 {len(items)} 条")
        items = items[: args.limit]
        results = await collect_detail_payloads(client, items)

    if args.dry_run:
        print("dry-run：未入库")
        return

    added, skipped = await persist_results(results)
    print(f"入库完成：新增 {added} / 跳过(已存在) {skipped}")


def main() -> None:
    parser = argparse.ArgumentParser(description="天津市政府采购网中标/成交公告采集")
    parser.add_argument("--limit", type=int, default=5, help="最多采集条数")
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
