# -*- coding: utf-8 -*-
"""演示页实时采集端点离线测试（D4）。

零网络：collect_realtime / ingest_scrape_result 全部 monkeypatch 掉，
只验证端点的编排逻辑（状态映射、串行入库、降级、limit 钳制）。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# demo_web 是 scripts/ 下的独立脚本（非包），用 spec 加载
_spec = importlib.util.spec_from_file_location("demo_web", REPO / "scripts" / "demo_web.py")
demo_web = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(demo_web)

from app.services.realtime_sources import SourceFetchResult, STATUS_OK, STATUS_ERROR, STATUS_EMPTY


def _fake_result(source, ok=True, status=STATUS_OK, fetched=0, payloads=None, error=None):
    return SourceFetchResult(
        source=source, ok=ok, status=status, fetched=fetched,
        payloads=payloads or [], error=error, elapsed_ms=10,
    )


def _install(monkeypatch, results, ingest_summary=None, ingest_raises=None):
    """替换 demo_web 内的抓取与入库，返回调用记录。"""
    calls = {"collect": [], "ingest": []}

    async def fake_collect(adapters, limit=20, max_concurrent=3, robots_checker=None):
        calls["collect"].append({"limit": limit, "adapters": [a.source for a in adapters]})
        return {"results": results, "total_payloads": sum(len(r.payloads) for r in results),
                "ok_count": sum(1 for r in results if r.ok)}

    async def fake_ingest(scrape_result, template=None, simhash_computer=None, db=None):
        calls["ingest"].append({"template": template, "n": len(scrape_result["data"]),
                                "url": scrape_result["url"]})
        if ingest_raises is not None:
            raise ingest_raises
        return ingest_summary or {"total": len(scrape_result["data"]),
                                  "inserted": len(scrape_result["data"]),
                                  "duplicates": 0, "errors": 0, "inserted_ids": []}

    monkeypatch.setattr(demo_web, "collect_realtime", fake_collect)
    monkeypatch.setattr(demo_web, "ingest_scrape_result", fake_ingest)
    return calls


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    return TestClient(demo_web.app)


def test_realtime_happy_path_ingests_per_source(monkeypatch, client):
    """两源各抓 2 条 → 串行入库两次，template 用 source 名。"""
    results = [
        _fake_result("hubei", fetched=2, payloads=[{"project_name": f"A{i}"} for i in range(2)]),
        _fake_result("jiangsu", fetched=2, payloads=[{"project_name": f"B{i}"} for i in range(2)]),
    ]
    calls = _install(monkeypatch, results)
    r = client.get("/demo/api/realtime?limit=2")
    assert r.status_code == 200
    d = r.json()
    assert d["total_inserted"] == 4
    assert d["ok_count"] == 2
    assert [s["source"] for s in d["sources"]] == ["hubei", "jiangsu"]
    # 串行入库：每源一次，template=source 名（避免 _infer_platform 误判 ccgp）
    assert len(calls["ingest"]) == 2
    assert [c["template"] for c in calls["ingest"]] == ["hubei", "jiangsu"]
    assert [c["n"] for c in calls["ingest"]] == [2, 2]
    assert d["sources"][0]["ingest"]["inserted"] == 2
    assert d["sources"][0]["status_text"] == "成功"


def test_realtime_error_source_skips_ingest(monkeypatch, client):
    """失败源不入库，不阻断其他源。"""
    results = [
        _fake_result("yunnan", ok=False, status=STATUS_ERROR, error="boom"),
        _fake_result("shandong", fetched=1, payloads=[{"project_name": "C0"}]),
    ]
    calls = _install(monkeypatch, results)
    d = client.get("/demo/api/realtime").json()
    assert d["ok_count"] == 1
    assert len(calls["ingest"]) == 1 and calls["ingest"][0]["template"] == "shandong"
    src_yn = d["sources"][0]
    assert src_yn["ok"] is False and src_yn["status"] == "error"
    assert src_yn["status_text"] == "失败" and src_yn["error"] == "boom"
    assert src_yn["ingest"] is None


def test_realtime_ingest_failure_degrades(monkeypatch, client):
    """抓取成功但入库异常 → 该源降级为 error，不 500。"""
    results = [_fake_result("hubei", fetched=1, payloads=[{"project_name": "X"}])]
    _install(monkeypatch, results, ingest_raises=RuntimeError("db locked"))
    r = client.get("/demo/api/realtime")
    assert r.status_code == 200
    d = r.json()
    assert d["total_inserted"] == 0
    s = d["sources"][0]
    assert s["ok"] is False and s["status"] == "error"
    assert s["status_text"] == "入库失败"
    assert "db locked" in s["error"]


def test_realtime_empty_source_not_ingested(monkeypatch, client):
    """empty 源（ok 但 0 条）不触发入库。"""
    results = [_fake_result("jiangsu", status=STATUS_EMPTY, fetched=0)]
    calls = _install(monkeypatch, results)
    d = client.get("/demo/api/realtime").json()
    assert calls["ingest"] == []
    assert d["sources"][0]["status_text"] == "本次无新公告"
    assert d["sources"][0]["ingest"] is None


def test_realtime_limit_clamped(monkeypatch, client):
    """limit 钳制到 [1,20] 并透传给编排器。"""
    calls = _install(monkeypatch, [])
    client.get("/demo/api/realtime?limit=999")
    assert calls["collect"][0]["limit"] == 20
    client.get("/demo/api/realtime?limit=0")
    assert calls["collect"][1]["limit"] == 1
    # 默认 limit=3
    client.get("/demo/api/realtime")
    assert calls["collect"][2]["limit"] == 3
