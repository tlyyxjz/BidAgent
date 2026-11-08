"""跨源交叉验证守卫测试（app/services/cross_validation.py + API 端点）。

覆盖：
- 编号归一化与 L1/L2 匹配规则（含同平台排除、不猜原则）
- SimHash 近邻仅辅助标注不升级
- 孤证标注口径
- 全库报告统计
- /api/real/realtime/cross-validation{/{tid}} 端点
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.database import AsyncSessionLocal
from app.models.tender import Tender
from app.services.cross_validation import (
    LEVEL_BID_NUMBER,
    LEVEL_TITLE_STRONG,
    STATUS_CORROBORATED,
    STATUS_SINGLE_SOURCE,
    NoticeRef,
    build_corroboration_report,
    corroborate_notice,
    find_cross_matches,
    normalize_bid_number,
)


def _ref(tid, plat, bn=None, name=None, sh=None):
    return NoticeRef(tender_id=tid, source_platform=plat, bid_number=bn,
                     project_name=name, simhash=sh)


# ========== 归一化 ==========

def test_normalize_bid_number_upper_strip():
    assert normalize_bid_number(" jszc-320000-A1 ") == "JSZC-320000-A1"


def test_normalize_bid_number_empty_to_none():
    assert normalize_bid_number(None) is None
    assert normalize_bid_number("") is None
    assert normalize_bid_number("   ") is None


# ========== L1 编号严格匹配 ==========

def test_l1_bid_number_match_cross_platform():
    tgt = _ref(1, "ccgp", bn="JSZC-320000-SMDG-G2026-0067",
               name="江苏省医疗器械检验所设备采购项目")
    cand = _ref(2, "jiangsu", bn="jszc-320000-smdg-g2026-0067",
                name="江苏省医疗器械检验所设备采购项目中标公告")
    ms = find_cross_matches(tgt, [cand])
    assert len(ms) == 1
    assert ms[0].level == LEVEL_BID_NUMBER
    assert ms[0].bid_number_matched is True
    assert ms[0].source_platform == "jiangsu"


def test_same_platform_excluded():
    """同平台重复不是佐证（qingdao 重复入库场景）。"""
    tgt = _ref(1, "qingdao", bn="SDGP370200000202602000929", name="青岛项目")
    cand = _ref(2, "qingdao", bn="SDGP370200000202602000929", name="青岛项目")
    assert find_cross_matches(tgt, [cand]) == []


# ========== L2 标题强匹配 ==========

def test_l2_title_strong_match():
    tgt = _ref(1, "ccgp", name="江苏省南通中学附属实验学校教育教学设备采购项目")
    cand = _ref(2, "jiangsu",
                name="江苏省南通中学附属实验学校教育教学设备采购项目中标公告")
    ms = find_cross_matches(tgt, [cand])
    assert len(ms) == 1
    assert ms[0].level == LEVEL_TITLE_STRONG
    assert ms[0].title_similarity >= 0.80


def test_l2_low_similarity_no_match():
    """宁可缺不可猜：标题不足门槛且无编号 → 不匹配。"""
    tgt = _ref(1, "ccgp", name="某医院设备采购项目")
    cand = _ref(2, "jiangsu", name="某学校物业服务采购项目")
    assert find_cross_matches(tgt, [cand]) == []


def test_no_bn_no_title_no_match():
    tgt = _ref(1, "ccgp")
    cand = _ref(2, "jiangsu")
    assert find_cross_matches(tgt, [cand]) == []


# ========== SimHash 仅辅助 ==========

def test_simhash_near_only_annotation():
    """SimHash 近邻只在 reason 中辅助标注，不改变级别。"""
    tgt = _ref(1, "ccgp", bn="SDGP370000000202602003681", name="x",
               sh=0b1111)
    cand = _ref(2, "shandong", bn="SDGP370000000202602003681", name="y",
                sh=0b1110)  # 汉明距离 1
    ms = find_cross_matches(tgt, [cand])
    assert len(ms) == 1
    assert ms[0].level == LEVEL_BID_NUMBER
    assert ms[0].simhash_near is True
    assert "SimHash" in ms[0].reason


def test_l1_sorted_before_l2():
    long_title = "某某省人民医院医疗设备更新改造二期采购项目中标结果"
    tgt = _ref(1, "ccgp", bn="ABC-123", name=long_title)
    l1 = _ref(2, "jiangsu", bn="abc-123", name="完全不同的名字")
    l2 = _ref(3, "shandong", name=long_title + "公告")
    ms = find_cross_matches(tgt, [l2, l1])
    assert [m.level for m in ms] == [LEVEL_BID_NUMBER, LEVEL_TITLE_STRONG]


# ========== 佐证判定 ==========

def test_corroborate_single_source_when_no_match():
    tgt = _ref(1, "ccgp", bn="UNIQUE-001", name="独一无二项目")
    res = corroborate_notice(tgt, [_ref(2, "jiangsu", name="其他项目")])
    assert res.status == STATUS_SINGLE_SOURCE
    assert res.matches == []
    assert res.to_dict()["match_count"] == 0


def test_corroborate_status_when_matched():
    tgt = _ref(1, "ccgp", bn="YNZC2026-G1-04409", name="云南项目")
    res = corroborate_notice(tgt, [_ref(2, "yunnan", bn="ynzc2026-g1-04409",
                                        name="云南项目")])
    assert res.status == STATUS_CORROBORATED


# ========== 全库报告 ==========

def test_report_stats_and_by_platform():
    notices = [
        _ref(1, "ccgp", bn="K-1", name="项目甲"),
        _ref(2, "jiangsu", bn="k-1", name="项目甲"),
        _ref(3, "hubei", bn="WHMY-X", name="独立项目"),
    ]
    rep = build_corroboration_report(notices)
    assert rep["total"] == 3
    assert rep["corroborated"] == 2
    assert rep["single_source"] == 1
    assert rep["by_platform"]["hubei"]["corroborated"] == 0
    assert rep["by_platform"]["ccgp"]["corroborated"] == 1
    # 匹配记录双向
    ids = {(m["target_tender_id"], m["match_tender_id"])
           for m in rep["matches"]}
    assert (1, 2) in ids and (2, 1) in ids


def test_report_empty_input():
    rep = build_corroboration_report([])
    assert rep["total"] == 0 and rep["matches"] == []


# ========== API 端点 ==========

def _client():
    return AsyncClient(transport=ASGITransport(app=app),
                       base_url="http://test")


async def _seed_tender(**kwargs) -> int:
    defaults = dict(
        project_name="跨源验证测试项目",
        bid_number="XV-2026-001",
        notice_type="result",
        source_platform="ccgp",
        source_url="http://example.gov.cn/xv/001",
        publish_time=datetime(2026, 8, 21),
        win_amount=Decimal("1000000"),
        core_content="跨源验证测试内容",
    )
    defaults.update(kwargs)
    async with AsyncSessionLocal() as db:
        t = Tender(**defaults)
        db.add(t)
        await db.commit()
        await db.refresh(t)
        return t.id


async def _delete_tender(tid: int):
    from sqlalchemy import delete
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Tender).where(Tender.id == tid))
        await db.commit()


@pytest.mark.asyncio
async def test_endpoint_report_corroborated_pair():
    id1 = await _seed_tender(source_platform="ccgp",
                             source_url="http://example.gov.cn/xv/a")
    id2 = await _seed_tender(source_platform="jiangsu",
                             source_url="http://example.gov.cn/xv/b")
    try:
        async with _client() as c:
            r = await c.get("/api/real/realtime/cross-validation")
            assert r.status_code == 200
            data = r.json()["data"]
            assert data["total"] >= 2
            assert data["corroborated"] >= 2

            r2 = await c.get(f"/api/real/realtime/cross-validation/{id1}")
            assert r2.status_code == 200
            d2 = r2.json()["data"]
            assert d2["status"] == STATUS_CORROBORATED
            assert any(m["tender_id"] == id2 for m in d2["matches"])
    finally:
        await _delete_tender(id1)
        await _delete_tender(id2)


@pytest.mark.asyncio
async def test_endpoint_notice_not_found():
    async with _client() as c:
        r = await c.get("/api/real/realtime/cross-validation/99999999")
        assert r.status_code == 404
