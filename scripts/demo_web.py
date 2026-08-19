# -*- coding: utf-8 -*-
"""复赛演示页（独立 FastAPI 服务，不碰主应用）。

启动：python scripts/demo_web.py
访问：http://127.0.0.1:8001/demo

三个页签：文本核验（四字段） / 图片核验（OCR） / 实时采集（D4）。
全部复用 verify_award / verify_award_image / evidence_card；
实时采集复用 collect_realtime + ingest_scrape_result（零逻辑复制）。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from fastapi import FastAPI, UploadFile  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from verify_award import verify_award  # noqa: E402
from verify_award_image import verify_image  # noqa: E402
from evidence_card import cards_from_verification, render_html  # noqa: E402

DB = os.environ.get("BIDAGENT_DB") or str(REPO / "data" / "bidagent.db")
PAGE = (REPO / "scripts" / "demo_page.html").read_text(encoding="utf-8")

# ---------------------------------------------------------------------------
# 实时采集层依赖 app.config（SECRET_KEY 等强校验）：
# 独立运行时给默认值，DATABASE_URL 必须与 BIDAGENT_DB 指向同一文件，
# 保证"采集入库 → 核验查询"看到同一份数据。必须在 import app 前设置。
# ---------------------------------------------------------------------------
os.environ.setdefault("SECRET_KEY", "a" * 64)
os.environ.setdefault("ADMIN_SECRET", "demo-admin-secret")
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{DB}")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from app.core.robots_checker import robots_checker  # noqa: E402
from app.processors.simhash import compute_simhash  # noqa: E402
from app.processors.tender_ingestor import ingest_scrape_result  # noqa: E402
from app.services.realtime_sources import collect_realtime, resolve_adapters  # noqa: E402

app = FastAPI(title="标小智 · 复赛演示")

# 中文状态文案（robots_denied/blocked_403/timeout/error/empty/ok）
_STATUS_TEXT = {
    "ok": "成功",
    "empty": "本次无新公告",
    "robots_denied": "robots.txt 禁止（合规跳过）",
    "blocked_403": "站点 403 拒绝（已熔断）",
    "timeout": "超时",
    "error": "失败",
}


class VerifyIn(BaseModel):
    bid_number: str
    amount: str = ""
    winner: str = ""
    purchaser: str = ""


@app.get("/demo", response_class=HTMLResponse)
def page():
    return HTMLResponse(PAGE)


@app.post("/demo/api/verify")
def api_verify(body: VerifyIn):
    result = verify_award(
        body.bid_number, body.amount, body.winner, body.purchaser, DB,
    )
    return {"result": result}


@app.post("/demo/api/verify-image")
async def api_verify_image(file: UploadFile):
    tmp = Path(DB).parent / "_demo_upload.png"
    tmp.write_bytes(await file.read())
    try:
        out = verify_image(str(tmp), DB)
    finally:
        tmp.unlink(missing_ok=True)
    return {
        "fields": out["fields"],
        "result": out["result"],
        "warnings": out["warnings"],
        "ocr_text": out["ocr_text"][:800],
    }


@app.get("/demo/api/cards", response_class=HTMLResponse)
def api_cards(bid_number: str, amount: str = "", winner: str = "", purchaser: str = ""):
    result = verify_award(bid_number, amount, winner, purchaser, DB)
    import sqlite3
    from verify_award import _norm_bid_number, _query_tenders

    raw_texts = {}
    conn = sqlite3.connect(DB)
    try:
        for r in _query_tenders(conn, _norm_bid_number(bid_number)):
            raw_texts[r["bid_number"]] = r["source_raw_text"]
    finally:
        conn.close()
    cards = cards_from_verification(result, raw_texts)
    return HTMLResponse(render_html(cards))


@app.get("/demo/api/realtime")
async def api_realtime(limit: int = 3):
    """D4：现场抓取四省最新中标公告并串行入库。

    默认 3 条/源：域名限流 8 秒/请求（列表 1 次 + 详情 N 次），
    单源约 (1+3)×8≈32 秒，在适配器 45s 超时内；演示时前端提示等待。

    流程：resolve_adapters() → collect_realtime（并发抓取，单源失败降级）
    → 逐源串行 ingest_scrape_result（避免 SQLite 并发写锁）。
    template 传 source 名（如 "hubei"）：既作 source_platform，
    也避免 _infer_platform 把分站误判为 ccgp 触发公告栏目过滤。
    """
    limit = max(1, min(int(limit), 20))
    start = time.monotonic()
    adapters = resolve_adapters()
    outcome = await collect_realtime(adapters, limit=limit, robots_checker=robots_checker)

    sources = []
    total_inserted = 0
    for adapter, r in zip(adapters, outcome["results"]):
        entry = {
            "source": r.source,
            "display_name": getattr(adapter, "display_name", r.source),
            "status": r.status,
            "status_text": _STATUS_TEXT.get(r.status, r.status),
            "ok": r.ok,
            "fetched": r.fetched,
            "elapsed_ms": r.elapsed_ms,
            "error": r.error,
            "ingest": None,
        }
        if r.ok and r.payloads:
            try:
                summary = await ingest_scrape_result(
                    scrape_result={
                        "url": getattr(adapter, "list_url", ""),
                        "data": r.payloads,
                        "pages_scraped": 1,
                    },
                    template=r.source,
                    simhash_computer=compute_simhash,
                )
                entry["ingest"] = summary
                total_inserted += summary.get("inserted", 0)
            except Exception as exc:  # noqa: BLE001
                entry["ok"] = False
                entry["status"] = "error"
                entry["status_text"] = "入库失败"
                entry["error"] = f"抓取成功但入库失败：{exc}"
        sources.append(entry)

    return {
        "sources": sources,
        "total_inserted": total_inserted,
        "ok_count": outcome["ok_count"],
        "elapsed_ms": int((time.monotonic() - start) * 1000),
        "limit": limit,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="warning")
    print("演示页: http://127.0.0.1:8001/demo")
