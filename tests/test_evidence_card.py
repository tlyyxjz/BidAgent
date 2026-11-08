"""证据回溯卡片测试：纯函数单测 + DB 集成测试。"""
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import evidence_card as ec  # noqa: E402

DB = os.environ.get("BIDAGENT_DB") or str(REPO / "data" / "bidagent.db")
HAS_DB = Path(DB).exists()
requires_db = pytest.mark.skipif(not HAS_DB, reason="bidagent.db 不存在，跳过集成测试")


# ---------------------------------------------------------------- 纯函数单测

RAW = (
    "一、项目编号：GHHX2026000062\n"
    "三、中标（成交）信息\n"
    "供应商名称：青岛盛信合创电子技术有限公司\n"
    "中标（成交）金额：22.5320000（万元）\n"
)


def test_locate_finds_position():
    assert ec._locate(RAW, "GHHX2026000062") == (7, 21)


def test_locate_missing_returns_none():
    assert ec._locate(RAW, "不存在") is None


def test_field_evidence_amount_and_winner():
    ev, pos = ec._field_evidence(RAW, "中标金额")
    assert ev and "22.5320000" in ev and pos[0] < pos[1]
    ev2, pos2 = ec._field_evidence(RAW, "中标人")
    assert ev2 and "青岛盛信合创" in ev2
    ev3, _ = ec._field_evidence(RAW, "项目编号")
    assert ev3 and "GHHX2026000062" in ev3


FAKE_VERIFY = {
    "verdict": "suspicious",
    "reason": "编号存在（1 条相关公告），但提供的字段与官方公告不一致：中标金额",
    "rows": [{
        "bid_number": "GHHX2026000062",
        "project_name": "某项目中标公告",
        "source_url": "http://example.gov.cn/x",
        "publish_time": "2026-08-05 18:05:54",
        "checks": [{
            "field": "中标金额",
            "input": "500万元",
            "db": 225320,
            "match": False,
        }],
    }],
}


def test_cards_from_verification_structure():
    cards = ec.cards_from_verification(FAKE_VERIFY, {"GHHX2026000062": RAW})
    assert cards[0]["kind"] == "verdict"
    check = cards[1]
    assert check["kind"] == "check"
    assert check["match"] is False
    assert "22.5320000" in check["evidence_text"]
    assert check["evidence_pos"] == ec._locate(RAW, check["evidence_text"])


def test_render_text_contains_pos_and_source():
    cards = ec.cards_from_verification(FAKE_VERIFY, {"GHHX2026000062": RAW})
    text = ec.render_text(cards)
    assert "存疑" in text
    assert "原文第" in text
    assert "http://example.gov.cn/x" in text


def test_render_html_escapes_and_badges():
    cards = ec.cards_from_verification(FAKE_VERIFY, {"GHHX2026000062": RAW})
    h = ec.render_html(cards)
    assert "<html" in h
    assert "存疑" in h
    assert "http://example.gov.cn/x" in h
    assert "<div class=\"evidence\">" in h


def test_render_html_escapes_angle_brackets():
    cards = [{
        "kind": "check", "verdict": "verified", "title": "t",
        "claim": "<script>alert(1)</script>", "db_value": "d",
        "match": True, "evidence_text": None, "evidence_pos": None,
        "source_url": None, "publish_time": None, "project_name": None,
    }]
    h = ec.render_html(cards)
    assert "<script>alert(1)</script>" not in h
    assert "&lt;script&gt;" in h


# ---------------------------------------------------------------- DB 集成测试

@requires_db
def test_verify_mode_cards_have_evidence_positions():
    import verify_award as va
    result = va.verify_award(
        "GHHX2026000062", amount="22.532万元",
        winner="青岛盛信合创电子技术有限公司", db_path=DB,
    )
    raw = va._query_tenders(
        __import__("sqlite3").connect(DB), "GHHX2026000062"
    )[0]["source_raw_text"]
    cards = ec.cards_from_verification(result, {"GHHX2026000062": raw})
    checks = [c for c in cards if c["kind"] == "check"]
    assert len(checks) == 2
    for c in checks:
        assert c["match"] is True
        if c["title"] == "中标金额核验":
            assert c["evidence_pos"] is not None


@requires_db
def test_profile_mode_cards_and_html_file():
    import winner_profile as wp
    result = wp.profile("湖南创益蔚来进出口有限公司", DB)
    cards = ec.cards_from_profile(result)
    assert cards[0]["kind"] == "summary"
    checks = [c for c in cards if c["kind"] == "check"]
    assert len(checks) >= 3
    h = ec.render_html(cards)
    assert "供应商名称" in h
