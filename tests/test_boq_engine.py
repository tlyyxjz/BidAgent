"""BOQ 报价异常检测引擎测试。"""
from __future__ import annotations

import pytest

from app.processors import boq_engine
from app.processors.boq_engine import (
    _analyze,
    _check_all,
    _extract,
    _find_category,
    analyze_boq,
)


@pytest.mark.parametrize(
    ("name", "expected_avg"),
    [
        ("充电桩", 45000),
        ("服务器", 85000),
        ("电脑", 5000),
    ],
)
def test_exact_category_match(name: str, expected_avg: float):
    benchmark = _find_category(name)

    assert benchmark is not None
    assert benchmark["avg"] == expected_avg


def test_category_without_match():
    assert _find_category("生鲜蔬菜") is None


def test_computer_repair_does_not_match_computer():
    assert _find_category("电脑维修") is None


def test_computer_purchase_matches_computer():
    benchmark = _find_category("电脑采购")

    assert benchmark is not None
    assert benchmark["avg"] == 5000


def test_longer_category_has_priority(monkeypatch):
    monkeypatch.setitem(
        boq_engine._CATEGORY_BENCHMARKS,
        "桩",
        {"avg": 100, "unit": "个", "std": 0.1},
    )

    benchmark = _find_category("充电桩采购")

    assert benchmark is not None
    assert benchmark["avg"] == 45000


def test_extract_quantity_before_name():
    items, _ = _extract("采购100台电脑，单价5000元，预算50万")

    assert len(items) == 1
    assert items[0]["name"] == "电脑"
    assert items[0]["quantity"] == 100
    assert items[0]["unit"] == "台"


def test_extract_name_before_quantity():
    items, _ = _extract("采购电脑100台，单价5000元，预算50万")

    assert len(items) == 1
    assert items[0]["name"] == "电脑"
    assert items[0]["quantity"] == 100
    assert items[0]["unit"] == "台"


def test_extract_multiple_items():
    text = (
        "采购1台电脑，单价5000元；"
        "采购2台打印机，单价3000元；预算2万元"
    )

    items, budget = _extract(text)

    assert [item["name"] for item in items] == ["电脑", "打印机"]
    assert items[0]["unit_price"] == 5000
    assert items[1]["unit_price"] == 3000
    assert budget == 20000


def test_extract_repeated_name_is_deduplicated():
    text = (
        "采购1台电脑，单价5000元；"
        "电脑1台，单价5000元；预算1万元"
    )

    items, _ = _extract(text)

    assert len(items) == 1
    assert items[0]["name"] == "电脑"


def test_check_all_also_deduplicates_items():
    items = [
        {
            "name": "电脑",
            "quantity": 1,
            "unit": "台",
            "unit_price": 5000,
            "total_price": 5000,
        },
        {
            "name": "电脑",
            "quantity": 2,
            "unit": "台",
            "unit_price": 5000,
            "total_price": 10000,
        },
    ]

    checked = _check_all(items)

    assert len(checked) == 1


def test_extract_budget_in_ten_thousand_yuan():
    _, budget = _extract("采购1台电脑，单价5000元，预算约12.5万元")

    assert budget == 125000


def test_underpriced_item():
    report = _analyze(
        "采购1台电脑，单价100元，预算1万元",
        "低价电脑项目",
    )

    assert report.items[0]["status"] == "underpriced"
    assert report.suspicious_count == 1


def test_overpriced_item():
    report = _analyze(
        "采购1台电脑，单价20000元，预算3万元",
        "高价电脑项目",
    )

    assert report.items[0]["status"] == "overpriced"
    assert report.suspicious_count == 1


def test_normal_price_item():
    report = _analyze(
        "采购1台电脑，单价5000元，预算1万元",
        "正常电脑项目",
    )

    assert report.items[0]["status"] == "normal"
    assert report.suspicious_count == 0


def test_no_benchmark_price_remains_normal():
    report = _analyze(
        "采购1台生鲜蔬菜，单价5000元，预算1万元",
        "生鲜采购",
    )

    assert report.items[0]["status"] == "normal"
    assert report.items[0]["remark"] == "未匹配到市场基准品类"
    assert report.suspicious_count == 0


def test_zero_anomaly_score_is_100_with_budget():
    report = _analyze(
        "采购1台电脑，单价5000元，预算1万元",
        "正常项目",
    )

    assert report.score == 100
    assert report.suspicious_count == 0
    assert report.summary == "清单校验通过。"


def test_all_anomalies_score_is_40_with_budget():
    text = (
        "采购1台电脑，单价100元；"
        "采购1台打印机，单价100元；预算1万元"
    )

    report = _analyze(text, "全异常项目")

    assert report.suspicious_count == 2
    assert report.score == 40


def test_missing_budget_deducts_ten_points():
    report = _analyze(
        "采购1台电脑，单价5000元",
        "无预算项目",
    )

    assert report.suspicious_count == 0
    assert report.score == 90
    assert "未提取预算金额" in report.summary


def test_all_anomalies_without_budget_score_is_30():
    report = _analyze(
        "采购1台电脑，单价100元",
        "无预算异常项目",
    )

    assert report.suspicious_count == 1
    assert report.score == 30


def test_empty_text():
    report = _analyze("", "空项目")

    assert report.items == []
    assert report.total_budget == 0
    assert report.score == 100
    assert report.summary == "未提取到工程量清单数据"


def test_text_without_boq_data():
    report = _analyze("这是项目背景和资格要求。", "无清单项目")

    assert report.items == []
    assert report.suspicious_count == 0
    assert report.score == 100


def test_single_item_without_unit_price():
    report = _analyze(
        "采购1台电脑，预算1万元",
        "无单价项目",
    )

    assert len(report.items) == 1
    assert report.items[0]["unit_price"] == 0
    assert report.items[0]["status"] == "normal"
    assert "未提供单价" in report.items[0]["remark"]
    assert report.score == 100


@pytest.mark.asyncio
async def test_analyze_boq_async_public_api():
    result = await analyze_boq(
        "采购1台服务器，单价85000元，预算10万元",
        "服务器采购",
    )

    assert result["version"] == "v2"
    assert result["project_name"] == "服务器采购"
    assert result["score"] == 100
    assert result["risk_level"] == "低风险"
    assert result["items"][0]["status"] == "normal"
    assert result["engine"] == "benchmark_normalized_v2"
