# -*- coding: utf-8 -*-
"""青岛市政府采购网（ccgp-qingdao.gov.cn）结果公告采集器（大件二批次二·第二城）。

流程：robots 检查 → POST 列表 API（site-info/page，colCode=0304 结果公告）
→ 过滤中标/成交/结果类标题 → 列表元数据如实构建 payload → 去重入库 tenders。

站点结构说明（2026-08 实机逆向核对）：
- 站点为 Vue SPA（gpsite 模板），列表数据来自网关
  http://zfcg.qingdao.gov.cn:58060 的
  POST /api/siteservice/free/qd/site-info/page（请求体含 colCode/page/limit/sort）。
- colCode=0304 = 结果公告（5.2 万条）；0303=招标公告、2505=合同公示、
  2506=验收公告（其余栏目按需扩展）。
- 详情接口 read-notice-value 的 value/noticeBody 对公网返回空
  （正文走交易系统附件渠道）→ 本采集器为**列表元数据模式**：
  只入标题/编号/发布时间/区域等列表即可得的字段，
  并在 core_content 中如实注明口径，不虚构正文。

合规（全部由确定性机制保证）：
- robots.txt：启动时检查站点域（返回 SPA 壳即无禁止规则，视为全允许）；
- 限流：每请求受 domain_rate_limiter 约束（默认 8 秒同域间隔）；
- 403 即停：任何 403 抛 Collect403 并终止；
- 不绕验证码/登录墙：正文不开放就只采元数据，不攻坚。

用法：
    python scripts/collect_qingdao.py --limit 5            # 采 5 条（默认 8s 间隔）
    python scripts/collect_qingdao.py --limit 3 --dry-run  # 只解析不入库
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
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
    parse_notice_type,
)
from app.processors.simhash import compute_simhash  # noqa: E402

SITE_URL = "http://www.ccgp-qingdao.gov.cn"
API_ORIGIN = "http://zfcg.qingdao.gov.cn:58060"
LIST_PATH = "/api/siteservice/free/qd/site-info/page"
LIST_API = API_ORIGIN + LIST_PATH
COL_CODE = "0304"  # 结果公告栏目
DOMAIN = "zfcg.qingdao.gov.cn"
DETAIL_URL_TPL = SITE_URL + "/#/readnotice?id={rid}"
UA = "BidAgent/1.0 (+educational-research; compliant crawler)"
API_HEADERS = {"Referer": SITE_URL + "/", "Origin": SITE_URL}


class Collect403(Exception):
    """403 即停异常（合规红线）。"""


_PDATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})")


def build_query(page: int = 1, limit: int = 10, col_code: str = COL_CODE) -> dict:
    """列表 API 请求体（与站点前端 fetchNoticeList 逐字段一致，纯函数便于测试）。"""
    return {
        "subject": None, "page": page, "limit": limit, "colCode": col_code,
        "colCodes": None, "sort": "-pdate", "area": None, "areaType": None,
        "pdate": None, "pdates": ["", ""], "unitName": None,
        "projectCode": None, "projectName": None, "agentName": None,
        "pdateType": None, "kindOf": None, "projectType": None,
    }


def dig_records(payload) -> list:
    """防御性取出 records 数组（响应为 {data:{data:{records:[..]}}} 嵌套）。"""
    node = payload
    for _ in range(4):
        if isinstance(node, dict):
            if isinstance(node.get("records"), list):
                return node["records"]
            node = node.get("data")
        else:
            break
    return []


def parse_records(payload) -> list[dict]:
    """解析列表响应为 [{id, string_id, title, project_name, project_code,
    pdate, region}]；跳过无 ID/无标题的脏记录。"""
    items: list[dict] = []
    for o in dig_records(payload):
        if not isinstance(o, dict):
            continue
        rid = str(o.get("id") or "").strip()
        title = re.sub(r"\s+", " ", str(o.get("subject") or "")).strip()
        if not rid or not title:
            continue
        items.append({
            "id": rid,
            "string_id": str(o.get("stringId") or ""),
            "title": title,
            "project_name": re.sub(
                r"\s+", " ", str(o.get("projectName") or "")).strip(),
            "project_code": str(o.get("projectCode") or "").strip() or None,
            "pdate": str(o.get("pdate") or "").strip(),
            "region": str(o.get("regionName") or "").strip(),
        })
    return items


def filter_result_items(items: list[dict], all_types: bool = False) -> list[dict]:
    """默认仅保留中标/成交/结果类标题；all_types=True 时不过滤（纯函数便于测试）。"""
    if all_types:
        return items
    return [it for it in items
            if any(k in it["title"] for k in ("中标", "成交", "结果"))]


def parse_pdate(s: str) -> str | None:
    """'2026-08-20T21:11:50.000+08:00' → '2026-08-20 21:11:50'；非法返回 None。"""
    m = _PDATE_RE.search(s or "")
    return f"{m.group(1)} {m.group(2)}" if m else None


def pdate_to_datetime(s: str) -> datetime | None:
    """pdate → datetime（tenders.publish_time 列为 DateTime，需对象而非字符串）。"""
    m = _PDATE_RE.search(s or "")
    if not m:
        return None
    return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S")


def compose_text(rec: dict) -> str:
    """列表元数据如实拼成记录文本（正文不开放，不虚构）。"""
    publish = parse_pdate(rec.get("pdate", "")) or rec.get("pdate") or ""
    lines = [
        f"公告标题：{rec['title']}",
    ]
    if rec.get("project_name"):
        lines.append(f"项目名称：{rec['project_name']}")
    if rec.get("project_code"):
        lines.append(f"采购/项目编号：{rec['project_code']}")
    if publish:
        lines.append(f"发布时间：{publish}")
    if rec.get("region"):
        lines.append(f"所属区域：{rec['region']}")
    lines.append("来源：青岛市政府采购网（结果公告栏目 0304）")
    lines.append("口径说明：本站公告正文需经交易系统附件渠道查阅，"
                 "公开列表接口仅提供元数据；本记录按列表元数据如实入库。")
    return "\n".join(lines)


def build_payload(rec: dict) -> dict:
    """由列表记录构建入库字段（纯函数，便于测试）。

    列表元数据模式：win_amount/win_company/tender_org 列表接口不提供，
    置 None（宁可缺、不可编）；编号取列表 projectCode（官方采购编号）。
    大件五存证：collect 直入路径不走 _build_tender，必须在此落 SHA-256。
    """
    text = compose_text(rec)
    _core = text[:2000]
    content_sha = hashlib.sha256(_core.encode("utf-8")).hexdigest() if _core else None
    raw_sha = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None
    return {
        "project_name": rec["title"],
        "bid_number": rec.get("project_code"),
        "win_amount": None,
        "tender_org": None,
        "publish_time": pdate_to_datetime(rec.get("pdate", "")),
        "notice_type": parse_notice_type(rec["title"], text) or "award",
        "win_company": None,
        "source_platform": "qingdao",
        "source_url": DETAIL_URL_TPL.format(rid=rec["id"]),
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


async def fetch_records(client: httpx.AsyncClient, page: int = 1,
                        limit: int = 10) -> list[dict]:
    """POST 列表 API 并解析记录；403 即停，非 200 抛 RuntimeError。"""
    await domain_rate_limiter.wait(LIST_API)
    resp = await client.post(LIST_API, json=build_query(page, limit),
                             headers=API_HEADERS)
    _raise_if_403(resp, LIST_API)
    if resp.status_code != 200:
        raise RuntimeError(f"fetch failed: {LIST_API} status={resp.status_code}")
    return parse_records(resp.json())


async def collect_payloads(client: httpx.AsyncClient, limit: int = 5,
                           all_types: bool = False) -> list[dict]:
    """翻页采集至凑够 limit 条（最多 5 页，防无数据空转）。"""
    results: list[dict] = []
    for page in range(1, 6):
        try:
            records = await fetch_records(client, page=page, limit=max(limit, 10))
        except RuntimeError as exc:
            print(f"  [停止] 列表接口异常: {exc}")
            break
        items = filter_result_items(records, all_types=all_types)
        for rec in items:
            if not rec.get("project_code"):
                # 诚实原则：无编号且无正文渠道的记录不入中标库
                print(f"  [跳过] {rec['title'][:40]} 列表无编号")
                continue
            p = build_payload(rec)
            results.append(p)
            print(f"  [{p['notice_type']}] {p['project_name'][:40]}"
                  f" | 编号 {p['bid_number']} | {p['publish_time']}")
            if len(results) >= limit:
                return results
        if len(records) < max(limit, 10):
            break
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
    parser = argparse.ArgumentParser(description="青岛市政府采购网结果公告采集")
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
