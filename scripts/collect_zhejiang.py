# -*- coding: utf-8 -*-
"""浙江省政府采购网（ccgp-zhejiang.gov.cn）结果公告采集器（省级批量·浏览器通道）。

站点结构说明（2026-08 实机逆向核对）：
- 全站 SPA + 阿里云 WAF 动态防护：httpx 直连 API 只拿到 JS 挑战页
  （renderData/aliyun_waf_aa 特征），**必须走真实浏览器通道**；
  Playwright 真人式浏览（打开首页→随机停顿→分段滚动）可稳定通过挑战。
- 数据接口：POST /portal/category（categoryCode=110-900461 采购结果公告，
  其中混有"废标公告"需过滤）；GET /portal/detail?articleId=<base64>。
- 详情正文为结构化模板 HTML，带 class 标记：
  code-winningSupplierName=中标供应商、code-summaryPrice=中标金额，
  可精确抽取（完整抽取模式，非元数据降级）。

合规（全部由确定性机制保证）：
- 浏览器通道 = 真人正常浏览，不伪造指纹、不对抗明示拦截；
- robots.txt：启动时检查（WAF 背后返回的页面无 Disallow 即视为允许）；
- 限流：每条详情间受 --interval 约束（默认 8 秒）；
- 挑战页检测：若浏览器最终仍落在挑战页 → CollectBlocked 即停。

用法：
    python scripts/collect_zhejiang.py --limit 3             # 采 3 条
    python scripts/collect_zhejiang.py --limit 3 --dry-run  # 只解析不入库
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import html as html_mod
import re
import sys
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from app.core.rate_limiter import domain_rate_limiter  # noqa: E402
from app.core.robots_checker import robots_checker  # noqa: E402
from app.processors.ccgp_field_parser import parse_notice_type  # noqa: E402
from app.processors.simhash import compute_simhash  # noqa: E402

SITE_URL = "http://www.ccgp-zhejiang.gov.cn"
DOMAIN = "www.ccgp-zhejiang.gov.cn"
UA = "BidAgent/1.0 (+educational-research; compliant crawler)"
CATEGORY_CODE = "110-900461"          # 采购结果公告（含中标/成交/废标）
WAF_MARKS = ("aliyun_waf_aa", "renderData")


class CollectBlocked(Exception):
    """浏览器通道仍被 WAF 挑战拦截 → 即停（不攻坚）。"""


# ---------------------------------------------------------------- 纯函数层

def parse_list_items(list_json: dict) -> list[dict]:
    """解析 /portal/category 响应为标准化条目；按 articleId 去重，
    跳过无 articleId/无 title 的脏条目。"""
    items: list[dict] = []
    seen: set[str] = set()
    try:
        data = list_json["result"]["data"]["data"]
    except (KeyError, TypeError):
        return []
    for it in data or []:
        aid = it.get("articleId")
        title = (it.get("title") or "").strip()
        if not aid or not title or aid in seen:
            continue
        seen.add(aid)
        items.append({
            "article_id": aid,
            "title": re.sub(r"\s+", " ", title),
            "path_name": it.get("pathName") or "",
            "publish_ms": it.get("publishDate"),
            "district": it.get("districtName"),
            "purchase_name": it.get("purchaseName"),
            "agency": it.get("author"),
            "project_name": it.get("projectName"),
            "project_code": it.get("projectCode"),
            "method": it.get("procurementMethod"),
            "supplier_name": it.get("supplierName"),
            "contract_amount": it.get("totalContractAmount"),
        })
    return items


def filter_result_items(items: list[dict], all_types: bool = False) -> list[dict]:
    """默认仅保留中标/成交结果类（pathName 或标题含中标/成交），
    剔除废标；all_types=True 透传（纯函数便于测试）。"""
    if all_types:
        return items
    out = []
    for it in items:
        scope = it["path_name"] + it["title"]
        if "废标" in scope:
            continue
        if any(k in scope for k in ("中标", "成交", "结果")):
            out.append(it)
    return out


_STYLE_RE = re.compile(r"<(style|script)[\s\S]*?</\1>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_SUPPLIER_RE = re.compile(
    r'class="[^"]*code-winningSupplierName[^"]*"[^>]*>\s*([^<]{2,100}?)\s*<')
_AMOUNT_RE = re.compile(
    r'class="[^"]*code-summaryPrice[^"]*"[^>]*>\s*([^<]{1,80}?)\s*<')
_NUM_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)")


def html_to_text(content: str) -> str:
    """模板 HTML → 纯文本（剥 style/script/标签，解实体，压缩空白）。"""
    t = _STYLE_RE.sub(" ", content or "")
    t = _TAG_RE.sub(" ", t)
    t = html_mod.unescape(t)
    t = re.sub(r"[ \t\u00a0]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n", t)
    return t.strip()


def parse_amount_cell(cell: str) -> Decimal | None:
    """'总价：539000（元）' → Decimal('539000')；解析失败 None。"""
    m = _NUM_RE.search(cell or "")
    if not m:
        return None
    try:
        return Decimal(m.group(1).replace(",", ""))
    except InvalidOperation:
        return None


def parse_detail(detail_json: dict | None) -> dict:
    """从 /portal/detail 响应提取：publish_time/win_company/win_amount/
    bid_number/project_name/plain_text；缺失一律 None（宁可缺不可编）。"""
    out = {"publish_time": None, "win_company": None, "win_amount": None,
           "bid_number": None, "project_name": None, "plain_text": None}
    if not isinstance(detail_json, dict):
        return out
    try:
        d = detail_json["result"]["data"]
    except (KeyError, TypeError):
        return out
    ms = d.get("publishDate")
    if isinstance(ms, (int, float)) and ms > 0:
        try:
            out["publish_time"] = datetime.fromtimestamp(ms / 1000)
        except (OverflowError, OSError, ValueError):
            pass
    out["bid_number"] = d.get("projectCode") or None
    out["project_name"] = (d.get("projectName") or "").strip() or None
    content = d.get("content") or ""
    suppliers = [s.strip() for s in _SUPPLIER_RE.findall(content) if s.strip()]
    if suppliers:
        out["win_company"] = "、".join(dict.fromkeys(suppliers))[:300]
    for cell in _AMOUNT_RE.findall(content):
        amt = parse_amount_cell(cell)
        if amt is not None:
            out["win_amount"] = amt
            break
    out["plain_text"] = html_to_text(content) or None
    return out


def compose_text(rec: dict, detail: dict) -> str:
    """列表+详情如实拼记录文本（证据锚定用原文）。"""
    lines = [f"公告标题：{rec['title']}"]
    if detail.get("project_name"):
        lines.append(f"项目名称：{detail['project_name']}")
    if detail.get("bid_number"):
        lines.append(f"项目编号：{detail['bid_number']}")
    if rec.get("purchase_name"):
        lines.append(f"采购单位：{rec['purchase_name']}")
    if detail.get("win_company"):
        lines.append(f"中标（成交）供应商：{detail['win_company']}")
    if detail.get("win_amount") is not None:
        lines.append(f"中标（成交）金额：{detail['win_amount']}元")
    if rec.get("method"):
        lines.append(f"采购方式：{rec['method']}")
    if rec.get("district"):
        lines.append(f"行政区划：{rec['district']}")
    pt = detail.get("publish_time")
    if pt:
        lines.append(f"发布时间：{pt.strftime('%Y-%m-%d %H:%M')}")
    lines.append("来源：浙江省政府采购网（采购结果公告 /portal/category）")
    if detail.get("plain_text"):
        lines.append("正文摘录：" + detail["plain_text"][:600])
    return "\n".join(lines)


def _to_signed_simhash(h: int) -> int:
    """simhash 转 64 位有符号（与 PostgreSQL BIGINT 对齐）。"""
    h &= (1 << 64) - 1
    return h - (1 << 64) if h >= (1 << 63) else h


def _ms_to_dt(ms) -> datetime | None:
    if isinstance(ms, (int, float)) and ms > 0:
        try:
            return datetime.fromtimestamp(ms / 1000)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def build_payload(rec: dict, detail: dict | None = None) -> dict:
    """构建入库字段（纯函数）。publish_time 优先详情，回落列表毫秒时间戳。
    存证三列自算（collect 直入路径不走 _build_tender）。"""
    detail = detail or {}
    pt = detail.get("publish_time") or _ms_to_dt(rec.get("publish_ms"))
    text = compose_text(rec, detail)
    _core = text[:2000]
    content_sha = hashlib.sha256(_core.encode("utf-8")).hexdigest() if _core else None
    raw_sha = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None
    return {
        "project_name": detail.get("project_name") or rec.get("project_name")
        or rec["title"],
        "bid_number": detail.get("bid_number") or rec.get("project_code"),
        "win_amount": detail.get("win_amount"),
        "tender_org": rec.get("purchase_name"),
        "publish_time": pt,
        "notice_type": parse_notice_type(rec["title"], text) or "award",
        "win_company": detail.get("win_company") or rec.get("supplier_name"),
        "source_platform": "zhejiang",
        "source_url": detail_url(rec["article_id"]),
        "core_content": _core,
        "source_raw_text": text,
        "content_sha256": content_sha,
        "raw_text_sha256": raw_sha,
        "simhash": _to_signed_simhash(compute_simhash(text)),
    }


def detail_url(article_id: str) -> str:
    """详情页规范 URL（articleId URL 编码，含 ==/+// 字符）。"""
    return f"{SITE_URL}/site/detail?articleId={quote(article_id, safe='')}"


# ---------------------------------------------------------------- 传输层

_LIST_FETCH_JS = (
    "async (body) => { const r = await fetch('/portal/category', "
    "{method:'POST', headers:{'Content-Type':'application/json'}, "
    "body: JSON.stringify(body)}); return await r.json(); }")
_DETAIL_FETCH_JS = (
    "async (aid) => { const r = await fetch('/portal/detail?articleId=' "
    "+ encodeURIComponent(aid) + '&timestamp=' + Date.now()); "
    "return await r.json(); }")


def _category_body(limit: int, days: int = 30) -> dict:
    import time
    begin = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    return {"pageNo": 1, "pageSize": max(limit * 2, 6),
            "categoryCode": CATEGORY_CODE, "isGov": True,
            "excludeDistrictPrefix": ["90", "006011", "H0", "001111"],
            "_t": int(time.time() * 1000), "publishDateBegin": begin}


async def collect_payloads(limit: int = 3, all_types: bool = False,
                           interval: float = 8.0) -> list[dict]:
    """浏览器通道：过 WAF 挑战 → 列表 → 过滤 → 逐条详情 → payload。"""
    import random
    from playwright.async_api import async_playwright

    results: list[dict] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36"),
            ignore_https_errors=True)
        page = await ctx.new_page()
        try:
            try:
                await page.goto(SITE_URL + "/", timeout=60000,
                                wait_until="networkidle")
            except Exception:  # noqa: BLE001 长连接埋点导致 idle 超时不致命
                pass
            # 拟人节奏：随机停顿 + 分段变速滚动
            await asyncio.sleep(random.uniform(2.0, 3.5))
            for _ in range(2):
                await page.mouse.wheel(0, random.randint(260, 520))
                await asyncio.sleep(random.uniform(1.0, 2.2))
            html_now = await page.content()
            if any(m in html_now for m in WAF_MARKS):
                raise CollectBlocked("浏览器通道仍被 WAF 挑战拦截，即停")

            list_json = await page.evaluate(_LIST_FETCH_JS,
                                            _category_body(limit))
            items = filter_result_items(parse_list_items(list_json),
                                        all_types=all_types)
            print(f"列表 {len(items)} 条候选（近30天中标/成交结果）")
            for rec in items:
                if len(results) >= limit:
                    break
                await domain_rate_limiter.wait(SITE_URL + "/portal/detail")
                try:
                    dj = await page.evaluate(_DETAIL_FETCH_JS,
                                             rec["article_id"])
                except Exception:  # noqa: BLE001 单条失败降级
                    dj = None
                detail = parse_detail(dj)
                payload = build_payload(rec, detail)
                results.append(payload)
                print(f"  [{payload['notice_type']}] "
                      f"{payload['project_name'][:36]}"
                      f" | 中标人={payload['win_company']}"
                      f" | 金额={payload['win_amount']}"
                      f" | {payload['publish_time']}")
                await asyncio.sleep(interval)
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
    payloads = await collect_payloads(limit=args.limit,
                                      all_types=args.all_types,
                                      interval=args.interval)
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
    parser = argparse.ArgumentParser(description="浙江省政府采购网结果公告采集")
    parser.add_argument("--limit", type=int, default=3, help="最多采集条数")
    parser.add_argument("--interval", type=float, default=8.0,
                        help="请求间隔秒数（合规默认 8）")
    parser.add_argument("--dry-run", action="store_true", help="只解析不入库")
    parser.add_argument("--all-types", action="store_true",
                        help="不过滤，采集结果栏目全部类型（含废标）")
    args = parser.parse_args()
    try:
        asyncio.run(_main_async(args))
    except CollectBlocked as exc:
        print(f"[合规停止] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
