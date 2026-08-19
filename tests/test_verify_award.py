"""中标真实性核验器测试。

纯函数单测 + DB 集成测试。DB 路径：环境变量 BIDAGENT_DB 优先（沙箱/CI 用副本），
否则用仓库默认 data/bidagent.db；文件不存在时集成测试自动跳过。
"""
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import verify_award as va  # noqa: E402

DB = os.environ.get("BIDAGENT_DB") or str(REPO / "data" / "bidagent.db")
HAS_DB = Path(DB).exists()
requires_db = pytest.mark.skipif(not HAS_DB, reason="bidagent.db 不存在，跳过集成测试")


# ---------------------------------------------------------------- 纯函数单测

def test_norm_bid_number_strips_and_uppercases():
    assert va._norm_bid_number(" gh hx2026000062 ") == "GHHX2026000062"
    assert va._norm_bid_number("") == ""


def test_to_yuan_plain_number():
    assert va._to_yuan("225320") == 225320.0
    assert va._to_yuan(225320) == 225320.0


def test_to_yuan_wan_units():
    assert va._to_yuan("22.532万元") == 225320.0
    assert va._to_yuan("￥22.532万") == 225320.0
    assert va._to_yuan("22.5320000（万元）") == 225320.0


def test_to_yuan_yi_unit():
    assert va._to_yuan("1.5亿元") == 150000000.0


def test_to_yuan_comma_and_invalid():
    assert va._to_yuan("2,253,20") == 225320.0
    assert va._to_yuan("abc") is None
    assert va._to_yuan(None) is None
    assert va._to_yuan("") is None


def test_norm_name_removes_spaces():
    assert va._norm_name("青岛 盛信 合创") == "青岛盛信合创"
    assert va._norm_name("　全角　空格　") == "全角空格"


def test_extract_winners_finds_names_and_filters_junk():
    text = (
        "供应商名称：青岛盛信合创电子技术有限公司\n"
        "中标供应商：北京某科技有限公司\n"
        "供应商名称：2026年8月\n"
        "供应商名称：86条\n"
    )
    names = va._extract_winners_from_text(text)
    assert "青岛盛信合创电子技术有限公司" in names
    assert "北京某科技有限公司" in names
    assert not any(va._JUNK_VALUE.fullmatch(n) for n in names)


def test_extract_winners_dedupes():
    text = "供应商名称：甲公司\n供应商名称：甲公司\n"
    assert va._extract_winners_from_text(text) == ["甲公司"]


def test_name_matches_exact_and_containment():
    assert va._name_matches("青岛盛信合创电子技术有限公司", ["青岛盛信合创电子技术有限公司"])
    # 输入是简称，库里是全称
    assert va._name_matches("盛信合创", ["青岛盛信合创电子技术有限公司"])
    # 库里是简称，输入是全称
    assert va._name_matches("青岛盛信合创电子技术有限公司", ["盛信合创"])


def test_name_matches_short_input_no_reverse_match():
    # 输入只有2个字，不允许"库名包含于输入"方向的匹配
    assert not va._name_matches("公司", ["青岛盛信合创电子技术有限公司"])
    assert not va._name_matches("", ["青岛盛信合创电子技术有限公司"])


# ---------------------------------------------------------------- DB 集成测试

@requires_db
def test_verify_full_match_returns_verified():
    r = va.verify_award(
        "GHHX2026000062",
        amount="22.532万元",
        winner="青岛盛信合创电子技术有限公司",
        purchaser="中国电子口岸数据中心青岛分中心",
        db_path=DB,
    )
    assert r["verdict"] == "verified"
    assert r["evidence"]["project_name"]
    assert "供应商名称" in (r["evidence"]["winner_line"] or "")


@requires_db
def test_verify_tampered_amount_returns_suspicious():
    r = va.verify_award(
        "GHHX2026000062",
        amount="500万元",
        winner="青岛盛信合创电子技术有限公司",
        db_path=DB,
    )
    assert r["verdict"] == "suspicious"
    bad = [c for row in r["rows"] for c in row["checks"] if not c["match"]]
    assert any(c["field"] == "中标金额" for c in bad)


@requires_db
def test_verify_swapped_winner_returns_suspicious():
    r = va.verify_award(
        "GHHX2026000062",
        amount="22.532万元",
        winner="青岛某某贸易有限公司",
        db_path=DB,
    )
    assert r["verdict"] == "suspicious"
    bad = [c for row in r["rows"] for c in row["checks"] if not c["match"]]
    assert any(c["field"] == "中标人" for c in bad)


@requires_db
def test_verify_unknown_number_returns_fake():
    r = va.verify_award("XXTEST20260001", amount="100万元", db_path=DB)
    assert r["verdict"] == "fake"
    assert r["rows"] == []


@requires_db
def test_verify_number_only_returns_suspicious():
    r = va.verify_award("GHHX2026000062", db_path=DB)
    assert r["verdict"] == "suspicious"
    assert "没有任何可比对字段" in r["reason"]


@requires_db
def test_verify_short_number_returns_fake():
    r = va.verify_award("3", amount="100万元", db_path=DB)
    assert r["verdict"] == "fake"
    assert "格式异常" in r["reason"]


@requires_db
def test_verify_multirow_bid_number_matches_one_row():
    """一号码多包：任一行全对即真。用库中该编号某行的真实数据回填验证。"""
    import sqlite3

    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT win_amount, tender_org, source_raw_text FROM tenders "
        "WHERE bid_number='OITC-G260882522' AND win_amount IS NOT NULL LIMIT 1"
    ).fetchall()
    conn.close()
    if not rows:
        pytest.skip("库中无 OITC 带金额数据")
    win_amount, tender_org, raw = rows[0]
    winners = va._extract_winners_from_text(raw)
    if not winners:
        pytest.skip("该行原文无供应商名称")
    r = va.verify_award(
        "OITC-G260882522",
        amount=str(win_amount),
        winner=winners[0],
        purchaser=tender_org or "",
        db_path=DB,
    )
    assert r["verdict"] == "verified"
    assert len(r["rows"]) > 1  # 确认确实匹配了多行
