# -*- coding: utf-8 -*-
"""一页尽调单守卫测试（大件三）。

真实 fixture 来自库内 id=1：编号 GHHX2026000062 / 青岛盛信合创电子技术有限公司 /
225320 元（22.532万）/ 中国电子口岸数据中心青岛分中心。
"""
import pytest

from scripts.receivable_due_diligence import run_due_diligence

WINNER = "青岛盛信合创电子技术有限公司"
PURCHASER = "中国电子口岸数据中心青岛分中心"
NUMBER = "GHHX2026000062"


@pytest.fixture(scope="module")
def api_client():
    # 直接用 demo_api router 构造最小 app：既测端点行为，又守卫 patch 接线
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.demo_api import router

    app = FastAPI()
    app.include_router(router)
    # 跨 FastAPI 版本查已注册路径：0.14x 起 app.routes 里只剩 _IncludedRouter
    # 占位对象（无 .path），得靠 openapi()["paths"]；并集写法兼容 0.13x/0.14x。
    registered = {getattr(r, "path", None) for r in app.routes}
    registered |= set(app.openapi().get("paths", {}) or {})
    assert "/api/demo/due-diligence" in registered, "demo_api 未挂接 due-diligence 端点"
    return TestClient(app)


def test_verified_full_match():
    r = run_due_diligence(
        winner=WINNER, amount="22.532万元", purchaser=PURCHASER, bid_number=NUMBER
    )
    assert r["verdict"] == "verified"
    assert "通过" in r["report_text"]
    assert "证据链" in r["report_text"]
    assert "口径声明" in r["report_text"]


def test_fake_number_existence_fail():
    r = run_due_diligence(winner=WINNER, amount="22.532万元", bid_number="FAKE00000001")
    assert r["verdict"] == "fake"
    assert any(rk["level"] == "critical" and "存在性不成立" in rk["text"]
               for rk in r["risks"])


def test_suspicious_amount_mismatch():
    r = run_due_diligence(winner=WINNER, amount="999万元", bid_number=NUMBER)
    assert r["verdict"] == "suspicious"
    assert any(rk["level"] == "critical" and "字段不一致" in rk["text"]
               for rk in r["risks"])


def test_overfinance_risk_critical():
    # 公告金额 225320 元，申请融资 30 万元 → 超额融资 critical
    r = run_due_diligence(
        winner=WINNER, amount="22.532万元", purchaser=PURCHASER,
        bid_number=NUMBER, finance_amount="30万元",
    )
    assert r["verdict"] == "verified"
    assert any(rk["level"] == "critical" and "超额融资" in rk["text"]
               for rk in r["risks"])


def test_finance_within_amount_no_overfinance():
    r = run_due_diligence(
        winner=WINNER, amount="22.532万元", purchaser=PURCHASER,
        bid_number=NUMBER, finance_amount="20万元",
    )
    assert not any("超额融资" in rk["text"] for rk in r["risks"])


def test_no_number_fallback_by_winner():
    # 通知书不带编号：按中标人反查 → 仍能核验通过
    r = run_due_diligence(winner=WINNER, amount="22.532万元")
    assert r["verdict"] == "verified"


def test_winner_not_found():
    r = run_due_diligence(winner="不存在的幻觉科技有限公司九九九", amount="1万元")
    assert r["verdict"] == "not_found"
    assert any(rk["level"] == "critical" for rk in r["risks"])


def test_cannot_verify_without_materials():
    r = run_due_diligence(winner="", amount="")
    assert r["verdict"] == "cannot_verify"


def test_report_one_page_structure():
    r = run_due_diligence(winner=WINNER, amount="22.532万元", bid_number=NUMBER)
    text = r["report_text"]
    for section in ("一页尽调单", "核验结论", "逐项核对", "风险提示"):
        assert section in text


# ========== 演示 API 端点（POST /api/demo/due-diligence） ==========

def test_api_endpoint_verified(api_client):
    resp = api_client.post("/api/demo/due-diligence", json={
        "winner": WINNER, "amount": "22.532万元",
        "purchaser": PURCHASER, "bid_number": NUMBER,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["verdict"] == "verified"
    assert "一页尽调单" in data["report_text"]


def test_api_endpoint_overfinance_risk(api_client):
    resp = api_client.post("/api/demo/due-diligence", json={
        "winner": WINNER, "amount": "22.532万元",
        "bid_number": NUMBER, "finance_amount": "30万元",
    })
    assert resp.status_code == 200
    assert any("超额融资" in rk["text"] for rk in resp.json()["risks"])


def test_api_endpoint_requires_materials(api_client):
    resp = api_client.post("/api/demo/due-diligence", json={})
    assert resp.status_code == 400
