# -*- coding: utf-8 -*-
"""广东省政府采购网（gdgpo.czt.gd.gov.cn）结果公告采集器（省级批量·浏览器通道）。

站点结构说明（2026-08 实机逆向核对）：
- 纯 JS SPA 壳页（2.2KB），数据全走 gpcms REST 接口：
  列表 GET /gpcms/rest/web/v2/info/selectInfoMoreChannel
  （siteId/channel 固定，noticeType=00102=结果公告，regionCode=440001 省级）；
  详情 GET /gpcms/rest/web/v2/info/getInfoById?id=<uuid>。
- WAF 对非浏览器 UA 黑名单（自报爬虫 UA 返 403）→ 与浙江同归
  "浏览器通道"模式：Playwright 页面上下文 page.request 调接口
  （正常浏览器标识 = 真人正常浏览，非指纹伪装）。
- 详情结构化字段 bidCompany/successfulMoney 恒为空 → 中标人/金额必须从
  content 的"三、采购结果"表抽取（表头特征：供应商名称+中标（成交）金额），
  金额为各合同包金额之和（与公告预算口径一致）。

合规（全部由确定性机制保证）：
- robots.txt 先行检查（站方 WAF 对其返 403 → RFC 9309 惯例视为全允许）；
- 限流：所有请求走 domain_rate_limiter（默认 8s/域）；
- 403 即停：任何 403 抛 Collect403 终止；不碰验证码/代理。

用法：
    python scripts/collect_guangdong.py --limit 3
    python scripts/collect_guangdong.py --limit 3 --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import html as html_mod
import json
import re
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from app.core.rate_limiter import domain_rate_limiter  # noqa: E402
from app.core.robots_checker import robots_checker  # noqa: E402
from app.processors.ccgp_field_parser import parse_notice_type  # noqa: E402
from app.processors.simhash import compute_simhash  # noqa: E402

SITE_URL = "https://gdgpo.czt.gd.gov.cn"
DOMAIN = "gdgpo.czt.gd.gov.cn"
UA = "BidAgent/1.0 (+educational-research; compliant crawler)"
SITE_ID = "cd64e06a-21a7-4620-aebc-0576bab7e07a"
CHANNEL = "fca71be5-fc0c-45db-96af-f513e9abda9d"
NOTICE_TYPE = "00102"        # 结果公告（含中标/成交各子类型）
_EXCLUDE_KEYS = ("废标", "流标", "终止", "更正", "澄清", "答疑")
_INCLUDE_KEYS = ("中标", "成交", "结果")


class Collect403(Exception):
    """403 即停异常（合规红线）。"""


# ---------------------------------------------------------------- 纯函数层

def list_url(page_size: int, curr_page: int = 1) -> str:
    return (f"{SITE_URL}/gpcms/rest/web/v2/info/selectInfoMoreChannel"
            f"?siteId={SITE_ID}&channel={CHANNEL}&currPage={curr_page}"
            f"&pageSize={page_size}&noticeType={NOTICE_TYPE}"
            "&regionCode=440001&cityOrArea=&subChannel=false&purchaseManner=")


def detail_url(info_id: str) -> str:
    """详情接口规范 URL（全站 SPA 无稳定详情路由，以接口 URL 为存证锚点）。"""
    return f"{SITE_URL}/gpcms/rest/web/v2/info/getInfoById?id={info_id}"


def parse_list(list_json: dict) -> list[dict]:
    """解析 selectInfoMoreChannel 响应；按 id 去重，跳过无 id/无标题脏块。"""
    items, seen = [], set()
    try:
        rows = list_json["data"]["rows"]
    except (KeyError, TypeError):
        return []
    for it in rows or []:
        rid = it.get("id")
        title = (it.get("title") or "").strip()
        if not rid or not title or rid in seen:
            continue
        seen.add(rid)
        items.append({
            "id": rid,
            "title": re.sub(r"\s+", " ", title),
            "notice_time": it.get("noticeTime"),
            "budget": it.get("budget"),
            "purchaser": it.get("purchaser"),
            "agency": it.get("agency"),
            "bid_code": it.get("openTenderCode"),
            "region": it.get("regionName"),
            "manner": it.get("purchaseMannerName") or it.get("purchaseManner"),
        })
    return items


def filter_result_items(items: list[dict], all_types: bool = False) -> list[dict]:
    """剔除废标/终止等非结果类，仅保留中标/成交结果；all_types 透传。"""
    if all_types:
        return items
    out = []
    for it in items:
        t = it["title"]
        if any(k in t for k in _EXCLUDE_KEYS):
            continue
        if any(k in t for k in _INCLUDE_KEYS):
            out.append(it)
    return out


_STYLE_RE = re.compile(r"<(style|script)[\s\S]*?</\1>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_TABLE_RE = re.compile(r"<table[\s\S]*?</table>", re.I)
_TR_RE = re.compile(r"<tr[^>]*>([\s\S]*?)</tr>", re.I)
_TD_RE = re.compile(r"<td[^>]*>([\s\S]*?)</td>", re.I)
_NUM_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)")
_PROJ_RE = re.compile(r"项目名称[：:]\s*([^<]{4,150}?)[<\r\n]")


def html_to_text(content: str) -> str:
    """模板 HTML → 纯文本（剥 style/script/标签，解实体，压缩空白）。"""
    t = _STYLE_RE.sub(" ", content or "")
    t = _TAG_RE.sub(" ", t)
    t = html_mod.unescape(t)
    t = re.sub(r"[ \t\u00a0]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n", t)
    return t.strip()


def _cell_text(cell: str) -> str:
    t = html_mod.unescape(_TAG_RE.sub(" ", cell or ""))
    return re.sub(r"\s+", " ", t).strip()


def parse_amount_cell(cell: str) -> Decimal | None:
    """'540,000.00元' → Decimal('540000.00')；解析失败 None。"""
    m = _NUM_RE.search(cell or "")
    if not m:
        return None
    try:
        return Decimal(m.group(1).replace(",", ""))
    except InvalidOperation:
        return None


def parse_result_pairs(content: str) -> list[tuple[str, Decimal | None]]:
    """抽"采购结果"表 (中标供应商, 金额) 对；金额 None 表示该包无有效金额
    （如"服务费率 X%"）；只认表头同时含 供应商名称 与 中标（成交）金额 的表。"""
    pairs = []
    for tbl in _TABLE_RE.findall(content or ""):
        if "供应商名称" not in tbl or "中标（成交）金额" not in tbl:
            continue
        for tr in _TR_RE.findall(tbl):
            tds = [_cell_text(c) for c in _TD_RE.findall(tr)]
            if len(tds) < 2:
                continue
            cell = tds[-1]
            # 诚实原则：金额列必须带"元"单位；"服务费率 X%"类非金额
            # 单元格不入金额（宁可缺不可编），但供应商仍然采信
            if "元" not in cell or "%" in cell or "率" in cell:
                pairs.append((tds[0], None))
                continue
            amt = parse_amount_cell(cell)
            if tds[0] and amt is not None:
                pairs.append((tds[0], amt))
    return pairs


def parse_detail(detail_json: dict | None) -> dict:
    """getInfoById → publish_time/win_company/win_amount/bid_number/
    project_name/plain_text；缺失一律 None（宁可缺不可编）。
    金额取各合同包之和（多包项目逐包列金额，求和与预算口径一致）。"""
    out = {"publish_time": None, "win_company": None, "win_amount": None,
           "bid_number": None, "project_name": None, "plain_text": None}
    if not isinstance(detail_json, dict):
        return out
    d = detail_json.get("data")
    if not isinstance(d, dict):
        return out
    nt = d.get("noticeTime")
    if isinstance(nt, str):
        try:
            out["publish_time"] = datetime.strptime(nt, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    out["bid_number"] = d.get("openTenderCode") or None
    content = d.get("content") or ""
    m = _PROJ_RE.search(content)
    if m:
        out["project_name"] = re.sub(r"\s+", " ", m.group(1)).strip()
    pairs = parse_result_pairs(content)
    if pairs:
        out["win_company"] = "、".join(dict.fromkeys(s for s, _ in pairs))[:300]
        amounts = [a for _, a in pairs if a is not None]
        # 只有全部合同包都抽到金额才求和入库，否则置 None（宁可缺不可编）
        if amounts and len(amounts) == len(pairs):
            out["win_amount"] = sum(amounts, Decimal(0))
    out["plain_text"] = html_to_text(content) or None
    return out


def compose_text(rec: dict, detail: dict) -> str:
    """列表+详情如实拼记录文本（证据锚定用原文）。"""
    lines = [f"公告标题：{rec['title']}"]
    if detail.get("project_name"):
        lines.append(f"项目名称：{detail['project_name']}")
    if detail.get("bid_number") or rec.get("bid_code"):
        lines.append(f"项目编号：{detail.get('bid_number') or rec['bid_code']}")
    if rec.get("purchaser"):
        lines.append(f"采购单位：{rec['purchaser']}")
    if detail.get("win_company"):
        lines.append(f"中标（成交）供应商：{detail['win_company']}")
    if detail.get("win_amount") is not None:
        lines.append(f"中标（成交）金额：{detail['win_amount']}元")
    if rec.get("manner"):
        lines.append(f"采购方式：{rec['manner']}")
    if rec.get("region"):
        lines.append(f"行政区划：{rec['region']}")
    if detail.get("publish_time"):
        lines.append("发布时间："
                     + detail["publish_time"].strftime("%Y-%m-%d %H:%M"))
    lines.append("来源：广东省政府采购网（结果公告 selectInfoMoreChannel）")
    if detail.get("plain_text"):
        lines.append("正文摘录：" + detail["plain_text"][:600])
    return "\n".join(lines)


def _to_signed_simhash(h: int) -> int:
    """simhash 转 64 位有符号（与 PostgreSQL BIGINT 对齐）。"""
    h &= (1 << 64) - 1
    return h - (1 << 64) if h >= (1 << 63) else h


def build_payload(rec: dict, detail: dict | None = None) -> dict:
    """构建入库字段（纯函数）。存证三列自算（collect 直入路径）。"""
    detail = detail or {}
    text = compose_text(rec, detail)
    _core = text[:2000]
    return {
        "project_name": detail.get("project_name") or rec["title"],
        "bid_number": detail.get("bid_number") or rec.get("bid_code"),
        "win_amount": detail.get("win_amount"),
        "tender_org": rec.get("purchaser"),
        "publish_time": detail.get("publish_time"),
        "notice_type": parse_notice_type(rec["title"], text) or "award",
        "win_company": detail.get("win_company"),
        "source_platform": "guangdong",
        "source_url": detail_url(rec["id"]),
        "core_content": _core,
        "source_raw_text": text,
        "content_sha256": hashlib.sha256(_core.encode("utf-8")).hexdigest(),
        "raw_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "simhash": _to_signed_simhash(compute_simhash(text)),
    }


# ---------------------------------------------------------------- 传输层

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0.0.0 Safari/537.36")
WAF_MARKS = ("403 Forbidden", "<title>403")


async def _fetch(req, url: str) -> str:
    """浏览器通道内请求：限流先行；403 即停；非 200 抛 RuntimeError。"""
    await domain_rate_limiter.wait(url)
    resp = await req.get(url)
    if resp.status == 403:
        raise Collect403(f"403 即停: {url}")
    if resp.status != 200:
        raise RuntimeError(f"HTTP {resp.status}: {url[:100]}")
    text = await resp.text()
    if any(m in text[:300] for m in WAF_MARKS):
        raise Collect403(f"WAF 拦截页，即停: {url}")
    return text


async def collect_payloads(limit: int = 3, all_types: bool = False) -> list[dict]:
    """浏览器通道：开首页建会话 → 列表 → 过滤 → 逐条详情（单条失败降级）。"""
    from playwright.async_api import async_playwright

    results: list[dict] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(user_agent=BROWSER_UA,
                                        ignore_https_errors=True)
        page = await ctx.new_page()
        try:
            try:
                await page.goto(SITE_URL + "/", timeout=60000,
                                wait_until="domcontentloaded")
            except Exception:  # noqa: BLE001 SPA 长连接埋点致 idle 超时不致命
                pass
            req = ctx.request
            raw = await _fetch(req, list_url(page_size=max(limit * 3, 12)))
            items = filter_result_items(parse_list(json.loads(raw)), all_types)
            print(f"列表 {len(items)} 条候选（结果公告·中标/成交类）")
            for rec in items:
                if len(results) >= limit:
                    break
                try:
                    dj = json.loads(await _fetch(req, detail_url(rec["id"])))
                except (RuntimeError, json.JSONDecodeError) as exc:
                    print(f"  [降级] 详情失败 {rec['id'][:13]}: {str(exc)[:60]}")
                    continue
                detail = parse_detail(dj)
                payload = build_payload(rec, detail)
                results.append(payload)
                print(f"  [{payload['notice_type']}] "
                      f"{payload['project_name'][:36]}"
                      f" | 中标人={payload['win_company']}"
                      f" | 金额={payload['win_amount']}"
                      f" | {payload['publish_time']}")
        finally:
            await browser.close()
    return results


async def persist_results(results: list[dict]) -> tuple[int, int]:
    """按 source_url 去重入库，返回 (新增, 跳过)。"""
    from app.models.database import AsyncSessionLocal
    from app.models.tender import Tender
    from sqlalchemy import select

    added = skipped = 0
    async with AsyncSessionLocal() as db:
        for p in results:
            exists = (await db.execute(
                select(Tender.id).where(Tender.source_url == p["source_url"])
            )).first()
            if exists:
                skipped += 1
                continue
            db.add(Tender(**p))
            added += 1
        await db.commit()
    return added, skipped


async def _main_async(args) -> None:
    if not await robots_checker.is_allowed(SITE_URL + "/", UA):
        raise SystemExit("robots 禁止采集，已停止")
    domain_rate_limiter.set_interval(DOMAIN, args.interval)
    payloads = await collect_payloads(limit=args.limit,
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
    parser = argparse.ArgumentParser(description="广东省政府采购网结果公告采集")
    parser.add_argument("--limit", type=int, default=3, help="最多采集条数")
    parser.add_argument("--interval", type=float, default=8.0,
                        help="请求间隔秒数（合规默认 8）")
    parser.add_argument("--dry-run", action="store_true", help="只解析不入库")
    parser.add_argument("--all-types", action="store_true",
                        help="不过滤，采集结果栏目全部类型（含废标）")
    args = parser.parse_args()
    try:
        asyncio.run(_main_async(args))
    except Collect403 as exc:
        print(f"[合规停止] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
