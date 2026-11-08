"""企业中标画像测试：纯函数聚合单测 + DB 集成测试。"""
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import winner_profile as wp  # noqa: E402

DB = os.environ.get("BIDAGENT_DB") or str(REPO / "data" / "bidagent.db")
HAS_DB = Path(DB).exists()
requires_db = pytest.mark.skipif(not HAS_DB, reason="bidagent.db 不存在，跳过集成测试")


# ---------------------------------------------------------------- aggregate 单测

def _win(amount, year="2026", org="采购人A", location="湖南"):
    return {
        "win_amount": amount,
        "publish_time": f"{year}-08-05 10:00:00",
        "tender_org": org,
        "location": location,
    }


def test_aggregate_totals_and_amount_stats():
    wins = [_win("10万元"), _win(200000), _win("30万元", year="2025", org="采购人B", location="北京")]
    s = wp.aggregate(wins)
    assert s["total_wins"] == 3
    assert s["total_amount"] == 600000.0
    assert s["avg_amount"] == 200000.0
    assert s["max_amount"] == 300000.0
    assert s["min_amount"] == 100000.0


def test_aggregate_yearly_trend_and_locations():
    wins = [_win("10万元", "2026"), _win("20万元", "2026"), _win("30万元", "2025")]
    s = wp.aggregate(wins)
    assert s["yearly_trend"] == {"2025": 1, "2026": 2}
    assert s["locations"] == {"湖南": 3}


def test_aggregate_customer_concentration():
    wins = [_win("10万元", org="A"), _win(200000, org="A"), _win("30万元", org="B")]
    s = wp.aggregate(wins)
    assert s["customers"][0]["purchaser"] == "A"
    assert s["customers"][0]["count"] == 2
    assert s["top1_share_pct"] == 50.0


def test_aggregate_none_amount_win_counted_but_not_in_amounts():
    wins = [_win("10万元"), _win(None)]
    s = wp.aggregate(wins)
    assert s["total_wins"] == 2
    assert s["total_amount"] == 100000.0
    assert s["avg_amount"] == 100000.0


def test_aggregate_empty_returns_safe_defaults():
    s = wp.aggregate([])
    assert s["total_wins"] == 0
    assert s["total_amount"] == 0
    assert s["avg_amount"] is None
    assert s["top1_share_pct"] is None


# ---------------------------------------------------------------- DB 集成测试

@requires_db
def test_profile_multi_win_company_returns_three_wins_with_evidence():
    r = wp.profile("湖南创益蔚来进出口有限公司", DB)
    assert r["stats"]["total_wins"] >= 3
    for w in r["wins"]:
        assert w["evidence_line"]
        assert "供应商名称" in w["evidence_line"]
        assert w["source_url"].startswith("http")


@requires_db
def test_profile_short_name_matching():
    """简称（≥4字）也能命中全称。"""
    r = wp.profile("创益蔚来", DB)
    assert r["stats"]["total_wins"] >= 3


@requires_db
def test_profile_unknown_company_returns_zero():
    r = wp.profile("不存在的某某公司XYZ", DB)
    assert r["stats"]["total_wins"] == 0
    assert r["wins"] == []


@requires_db
def test_list_all_companies_nonempty_and_sorted_desc():
    companies = wp.list_all_companies(DB)
    assert companies
    counts = list(companies.values())
    assert counts == sorted(counts, reverse=True)
