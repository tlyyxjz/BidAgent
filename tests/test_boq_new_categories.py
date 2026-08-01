"""BOQ 新增 12 品类专项测试（10 工程类 + 2 IT 服务类）。

验证 _CATEGORY_BENCHMARKS 覆盖 32 类，新增的工程/IT 服务品类存在且
avg/std 基准值正确，并验证文本中"土建工程"能被 analyze_boq 正确识别。
"""
from __future__ import annotations

import pytest

from app.processors.boq_engine import _CATEGORY_BENCHMARKS, analyze_boq


def test_32_categories_count():
    assert len(_CATEGORY_BENCHMARKS) == 32


def test_engineering_categories_exist():
    engineering = [
        "土建工程",
        "市政工程",
        "装修工程",
        "绿化工程",
        "水利工程",
        "公路工程",
        "房建工程",
        "桥梁工程",
        "管网工程",
        "消防工程",
    ]
    for name in engineering:
        assert name in _CATEGORY_BENCHMARKS, f"缺少工程类品类: {name}"


def test_it_service_categories_exist():
    it_service = ["系统集成", "运维服务"]
    for name in it_service:
        assert name in _CATEGORY_BENCHMARKS, f"缺少 IT 服务类品类: {name}"


@pytest.mark.parametrize(
    ("category", "expected_avg"),
    [
        ("土建工程", 500),
        ("市政工程", 300),
        ("装修工程", 800),
        ("桥梁工程", 8000000),
        ("公路工程", 5000000),
    ],
)
def test_engineering_avg_values(category: str, expected_avg: int):
    assert _CATEGORY_BENCHMARKS[category]["avg"] == expected_avg


@pytest.mark.parametrize(
    ("category", "expected_std"),
    [
        ("土建工程", 0.25),
        ("市政工程", 0.30),
        ("装修工程", 0.30),
        ("绿化工程", 0.35),
        ("桥梁工程", 0.40),
    ],
)
def test_engineering_std_values(category: str, expected_std: float):
    assert _CATEGORY_BENCHMARKS[category]["std"] == expected_std


async def test_engineering_category_matching():
    """包含"土建工程 1000 平米"的文本应被 analyze_boq 正确识别为土建品类。"""
    text = "土建工程 1000 平米"
    report = await analyze_boq(text, "测试项目")

    items = report.get("items", [])
    assert len(items) >= 1

    names = [it.get("name", "") for it in items]
    assert any("土建" in n for n in names), f"未提取到土建工程项: {names}"

    target = next(it for it in items if "土建" in it.get("name", ""))
    # 命中市场基准品类时，remark 不会是"未匹配到市场基准品类"
    assert target.get("remark") != "未匹配到市场基准品类"
    # 未提供单价时给出"未提供单价"提示，证明品类已命中基准库
    assert target.get("remark") == "未提供单价，无法进行价格异常判断"
