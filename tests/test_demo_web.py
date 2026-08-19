# -*- coding: utf-8 -*-
"""复赛演示页 API 测试（TestClient，离线）。"""
import importlib.util
import io
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO))

from fastapi.testclient import TestClient  # noqa: E402

import demo_web  # noqa: E402

client = TestClient(demo_web.app)
HAS_OCR = importlib.util.find_spec("easyocr") is not None


def test_page_served():
    r = client.get("/demo")
    assert r.status_code == 200
    assert "标小智" in r.text
    assert "文本核验" in r.text


def test_verify_ok_case():
    r = client.post("/demo/api/verify", json={
        "bid_number": "GHHX2026000062",
        "amount": "22.532万元",
        "winner": "青岛盛信合创电子技术有限公司",
        "purchaser": "中国电子口岸数据中心青岛分中心",
    })
    assert r.status_code == 200
    assert r.json()["result"]["verdict"] == "verified"


def test_verify_tampered_amount():
    r = client.post("/demo/api/verify", json={
        "bid_number": "GHHX2026000062",
        "amount": "500万元",
        "winner": "青岛盛信合创电子技术有限公司",
    })
    assert r.json()["result"]["verdict"] == "suspicious"


def test_verify_fake_number():
    r = client.post("/demo/api/verify", json={
        "bid_number": "XXTEST20260001", "amount": "100万元",
    })
    assert r.json()["result"]["verdict"] == "fake"


def test_cards_endpoint_returns_evidence_html():
    r = client.get("/demo/api/cards", params={
        "bid_number": "GHHX2026000062", "amount": "22.532万元",
        "winner": "青岛盛信合创电子技术有限公司",
    })
    assert r.status_code == 200
    assert "证据" in r.text and "供应商名称" in r.text


@pytest.mark.skipif(not HAS_OCR, reason="easyocr 未安装")
def test_verify_image_endpoint():
    """合成假通知书 → OCR → verified。"""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1600, 800), "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 44)
    lines = ["中标通知书", "一、项目编号：GHHX2026000062",
             "供应商名称：青岛盛信合创电子技术有限公司",
             "中标（成交）金额：22.5320000（万元）",
             "四、采购人信息", "名 称：中国电子口岸数据中心青岛分中心"]
    y = 60
    for ln in lines:
        d.text((80, y), ln, font=font, fill="black")
        y += 85
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    r = client.post("/demo/api/verify-image",
                    files={"file": ("notice.png", buf.getvalue(), "image/png")})
    assert r.status_code == 200
    body = r.json()
    assert body["result"]["verdict"] in ("verified", "suspicious")
    assert body["fields"]["bid_number"] == "GHHX2026000062"
