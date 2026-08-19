"""山东省政府采购网（ccgp-shandong.gov.cn）中标/成交公告采集器（第四源）。

站点为 Vue SPA：页面无服务端渲染内容，数据全部来自 JSON API（已逆向契约，D6 实机验证）。

契约（D6 实机核对：主站 :443 对 API 路径返 405，API 已搬到独立端口）：
- API 主机：https://www.ccgp-shandong.gov.cn:8087/api（bundle 内 axios baseURL 实锤）；
  :8087 证书对 www 域不可验证，verify=False（仅影响本采集通道）。
- 列表：POST {API}/website/site/getListByCode
  body {"colCode":"0302","area":"","currentPage":1,"pageSize":20,"homePage":1}
  → 外层 {"status":200,"message":"OK","data":{code:100,message,data:{records:[...]}}}，
  前端拦截器取 resp.data.data → {code,message,data:{records}}；本脚本解两层。
  records: [{id,title,date,areaName,userName,colCode,...}]；
  colCode=0302 为结果公告栏目（D6 码表 chunk 实锤：采购信息→结果公告；
  旧推断的 29 实为网站通知公告，已纠正）。
- 详情：GET {API}/website/site/getDetail?id=<id>&colCode=<colCode>
  实机响应：外层包装内 {title,date,userName,body,files,...}，
  **body 为 base64 编码的 HTML**（前端 SiteBody 组件 base64 解码实锤，
  实机解出 26KB HTML）；getNoticeBody 对结果公告返“公告正文不存在”，故不用。
  接口偶发 code=999“请稍后重试”（限流抖动）→ 采集器跳过该条不崩溃。
  source_url 存可浏览详情页 {SITE}/detail?id=<id>&colCode=<code>。

合规（全部由确定性机制保证）：
- robots.txt：启动时检查，禁止即停（山东站 robots.txt 返回 SPA 壳 HTML，
  无任何 Disallow 规则，按 RFC 9309 惯例视为全允许）；
- 限流：每请求受 domain_rate_limiter 约束（默认 8 秒同域间隔）；
- 403 即停：任何 403 抛 Collect403 并终止；
- 不绕登录墙/验证码；API 契约失效（非 JSON/无 records）即停不重试。

用法：
    python scripts/collect_shandong.py --limit 5            # 采 5 条（默认 8s 间隔）
    python scripts/collect_shandong.py --limit 3 --dry-run  # 只解析不入库
"""
from __future__ import annotations

import argparse
import asyncio
import base64
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

SITE_URL = "https://www.ccgp-shandong.gov.cn"  # 站点壳（robots/展示 URL）
API_BASE = "https://www.ccgp-shandong.gov.cn:8087/api"  # D6 实机确认的 API 主机
LIST_API = "/website/site/getListByCode"
DETAIL_API = "/website/site/getDetail?id={id}&colCode={code}"  # D6 实机：getNoticeBody 对结果公告无正文
PAGE_URL = "/detail?id={id}&colCode={code}"  # 可浏览详情页（source_url 用）
COL_CODE = "0302"  # 结果公告栏目（中标/成交/结果类；D6 码表实锤）
UA = "BidAgent/1.0 (+educational-research; compliant crawler)"


class Collect403(Exception):
    """403 即停异常（合规红线）。"""


_TITLE_RE = re.compile(r"<title>([\s\S]*?)</title>")
_DATE_RES = (r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?", r"\d{4}/\d{2}/\d{2}")
_B64_RE = re.compile(r"^[A-Za-z0-9+/=\s]+$")
# D6 实机（probe16 实锤）：正文为 GBK 且无 charset meta 声明，utf-8 解码直接失败；
# 策略：有声明按声明 → 无声明先 utf-8 严格试 → 失败用 gb18030（超集含 GBK）
_CHARSET_RE = re.compile(rb"charset=[\"']?([A-Za-z0-9_\-]+)", re.I)

# D6 实机（probe17 样本）：山东结果公告为表格展平格式，共享解析器的冒号型正则不覆盖，
# 补充山东专用 fallback（仅在共享解析器未命中时启用）：
# 「供应商名称 供应商地址 中标（成交）金额 评审总得分 泰安市威新医用制品有限公司 … 1,992,000.00元」
_SD_WINNER_RES = (
    re.compile(r"供应商名称\s+供应商地址\s+[\s\S]{0,40}?((?:[\u4e00-\u9fa5A-Za-z0-9（）()·]{2,40}?)(?:有限公司|公司|集团))"),
    re.compile(r"(?:中标|成交)供应商(?:名称)?\s+((?:[^\s]{2,40}?)(?:有限公司|公司|集团|中心|研究院|医院))"),
)
_SD_AMOUNT_RE = re.compile(
    r"(?:中标[（(]?成交[）)]?金额|采购结果)[\s\S]{0,250}?([\d,]+(?:\.\d+)?)\s*(万元|亿元|元|万|亿)"
)


def strip_tags(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", text).strip()


def _parse_date(raw: str) -> datetime | None:
    for pat in _DATE_RES:
        m = re.search(pat, raw or "")
        if not m:
            continue
        s = m.group(0).replace("/", "-").replace("T", " ")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
    return None


def _api_body(payload) -> dict:
    """getListByCode 外层解包：{status,message,data:{code,message,data:{records}}}。

    兼容外层直接是业务体（{code,data:{records}}）的形态。
    """
    if not isinstance(payload, dict):
        return {}
    body = payload.get("data")
    if not isinstance(body, dict):
        body = payload
    inner = body.get("data")
    if isinstance(inner, dict):
        return inner
    return body


def parse_list_json(payload) -> list[dict]:
    """解析 getListByCode 响应。返回 [{id, col_code, title, date, publish_dt}]。

    契约（D6 实机）：records 位于两层包装内，见 _api_body；
    无 records 或非预期形态返回 []（由调用方决定是否报错）。
    """
    body = _api_body(payload)
    rows = body.get("records") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return []
    items: list[dict] = []
    for o in rows:
        if not isinstance(o, dict):
            continue
        nid = str(o.get("id") or "").strip()
        title = re.sub(r"\s+", " ", str(o.get("title") or "")).strip()
        if not nid or not title:
            continue
        items.append({
            "id": nid,
            "col_code": str(o.get("colCode") or COL_CODE),
            "title": title,
            "date": str(o.get("date") or "").strip(),
            "publish_dt": _parse_date(str(o.get("date") or "")),
        })
    return items


def _maybe_b64_decode(v: str) -> str:
    """山东详情 body 为 base64（D6 前端实锤）；解出含标签才采用，否则原样返回。

    编码（D6 实机 probe16）：正文为 GBK 且无 charset meta 声明，utf-8 解码失败。
    策略：有 meta 声明按声明解；否则先 utf-8 严格试（新数据兼容），失败回退 gb18030。
    """
    if not v or "<" in v:
        return v
    s = v.strip()
    if len(s) < 16 or not _B64_RE.match(s):
        return v
    try:
        raw = base64.b64decode(s, validate=True)
    except Exception:
        return v
    m = _CHARSET_RE.search(raw[:512])
    if m:
        encs = [m.group(1).decode("ascii", "ignore")]
    else:
        encs = ["utf-8", "gb18030"]
    dec = None
    for enc in encs:
        try:
            dec = raw.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if dec is None:
        dec = raw.decode("gb18030", "replace")
    return dec if "<" in dec else v


def parse_detail_html(text: str) -> str:
    """从详情响应提取 HTML 正文。

    D6 实机契约：外层 {status,...,data:{code,message,data:{title,date,userName,body}}}，
    body 为 base64 编码的 HTML。兼容形态：
    1. 双层包装 + base64 body（实机）；2. {"noticeBody":html} / {"data":{...}} 旧契约；
    3. 裸 HTML。无正文时返回空串（由调用方决定跳过）。
    """
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text or ""
    if not isinstance(payload, dict):
        return text or ""
    # 逐层解包候选节点（外层 status 包装 → data → data.data）
    nodes = [payload]
    d1 = payload.get("data")
    if isinstance(d1, dict):
        nodes.append(d1)
        d2 = d1.get("data")
        if isinstance(d2, dict):
            nodes.append(d2)
    for extra in (payload.get("content"), payload.get("result")):
        if isinstance(extra, dict):
            nodes.append(extra)
    for node in nodes:
        for key in ("body", "noticeBody", "html", "content"):
            v = node.get(key)
            if not isinstance(v, str) or not v:
                continue
            dec = _maybe_b64_decode(v)
            if "<" in dec:
                return dec
    return ""


def parse_detail_title(html: str) -> str | None:
    m = _TITLE_RE.search(html or "")
    if not m:
        return None
    t = re.sub(r"<[^>]+>", " ", m.group(1))
    t = re.sub(r"\s+", " ", t).strip()
    return t or None


def _to_signed_simhash(h: int) -> int:
    """与 tender_ingestor 一致：uint64 simhash 归一为 int64 有符号存储。"""
    h &= 0xFFFFFFFFFFFFFFFF
    return h - 0x10000000000000000 if h >= 0x8000000000000000 else h


def sd_extract_winners(text: str) -> list[str]:
    """山东表格展平格式中标人 fallback（共享解析器未命中时启用，probe17 样本验证）。"""
    found: list[str] = []
    seen: set[str] = set()
    for pat in _SD_WINNER_RES:
        for m in pat.finditer(text or ""):
            name = m.group(1).strip()
            key = re.sub(r"[\s\u3000]+", "", name)
            if key and key not in seen and len(key) >= 5:
                seen.add(key)
                found.append(name)
    return found


def sd_extract_win_amount(text: str) -> Decimal | None:
    """山东表格展平格式中标金额 fallback：表头/采购结果段后首个金额（probe17 验证）。"""
    m = _SD_AMOUNT_RE.search(text or "")
    if not m:
        return None
    try:
        num = Decimal(m.group(1).replace(",", ""))
    except Exception:
        return None
    unit = m.group(2)
    if unit in ("万元", "万"):
        num *= 10000
    elif unit in ("亿元", "亿"):
        num *= 100000000
    return num


def build_payload(item: dict, detail_html: str) -> dict:
    """由列表项 + 详情 HTML 构建入库字段（纯函数，便于测试）。"""
    text = strip_tags(detail_html)
    winners = _extract_winners_from_text(text) or sd_extract_winners(text)
    win_amount = parse_win_amount(text) or sd_extract_win_amount(text)
    publish_time = parse_publish_time(text) or item.get("publish_dt")
    return {
        "project_name": item["title"],
        "bid_number": parse_bid_number(text),
        "win_amount": Decimal(str(win_amount)) if win_amount is not None else None,
        "tender_org": parse_tender_org(text),
        "publish_time": publish_time,
        "notice_type": parse_notice_type(item["title"], text) or "award",
        "win_company": "、".join(winners) if winners else None,
        "source_platform": "shandong",
        "source_url": SITE_URL + PAGE_URL.format(id=item["id"], code=item["col_code"]),
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


async def _fetch_list(client: httpx.AsyncClient, page: int = 1,
                      page_size: int = 20) -> list[dict]:
    """抓结果公告列表（colCode=0302）。带 Origin/Referer/XHR 头（SPA 站点要求）。"""
    await domain_rate_limiter.wait(API_BASE)
    resp = await client.post(
        API_BASE + LIST_API,
        json={"colCode": COL_CODE, "area": "", "currentPage": page,
              "pageSize": page_size, "homePage": 1},
        headers={"Referer": SITE_URL + "/", "Origin": SITE_URL,
                 "X-Requested-With": "XMLHttpRequest"},
    )
    _raise_if_403(resp, LIST_API)
    if resp.status_code != 200:
        raise RuntimeError(f"list failed: status={resp.status_code}")
    try:
        payload = json.loads(resp.text)
    except json.JSONDecodeError:
        return []
    return parse_list_json(payload)


async def _collect_pages(fetch_page, pages: int, limit: int) -> list[dict]:
    """逐页拉取列表并按 id 去重，凑满 limit 或页拉空即停（纯函数便于测试）。"""
    items: list[dict] = []
    seen_ids: set[str] = set()
    for pg in range(1, max(pages, 1) + 1):
        got = await fetch_page(pg)
        if not got:
            break
        for it in got:
            if it["id"] not in seen_ids:
                seen_ids.add(it["id"])
                items.append(it)
        if len(items) >= limit:
            break
    return items


async def _main_async(args) -> None:
    if not await robots_checker.is_allowed(SITE_URL, UA):
        raise SystemExit("robots 禁止采集，已停止")

    domain_rate_limiter.set_interval("www.ccgp-shandong.gov.cn", args.interval)
    async with httpx.AsyncClient(
        headers={"User-Agent": UA}, follow_redirects=True, timeout=25.0,
        trust_env=False, verify=False,  # 不走本机代理；:8087 证书链对 www 域不可验证
    ) as client:
        items = await _collect_pages(
            lambda pg: _fetch_list(client, page=pg, page_size=max(args.limit, 20)),
            pages=args.pages, limit=args.limit)
        print(f"列表解析 {len(items)} 条")
        if not items:
            print("[警告] 列表为空：契约响应形态可能变化（待实机核对），已停止")
            return
        if not args.all_types:
            kept = [it for it in items
                    if any(k in it["title"] for k in ("中标", "成交", "结果"))]
            print(f"过滤中标/成交/结果后 {len(kept)} 条（跳过 {len(items) - len(kept)} 条）")
            items = kept
        items = items[: args.limit]

        results = []
        for it in items:
            url = API_BASE + DETAIL_API.format(id=it["id"], code=it["col_code"])
            try:
                detail_html = parse_detail_html(await _fetch(client, url))
            except httpx.TransportError as exc:
                print(f"  [跳过] {it['title'][:40]} 抓取失败: {exc}")
                continue
            except RuntimeError as exc:
                # 接口偶发 code=999/5xx（限流抖动）：跳过该条，不阻断整体
                print(f"  [跳过] {it['title'][:40]} 详情接口异常: {exc}")
                continue
            if "<" not in detail_html:
                print(f"  [跳过] {it['title'][:40]} 无正文（接口抖动或形态变化）")
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
    parser = argparse.ArgumentParser(description="山东政府采购网中标/成交公告采集")
    parser.add_argument("--limit", type=int, default=5, help="最多采集条数")
    parser.add_argument("--pages", type=int, default=1, help="列表拉取页数（单页约 15 条）")
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
