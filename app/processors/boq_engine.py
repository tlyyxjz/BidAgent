"""
鐗堟潈澹版槑锛歅roprietary and Confidential. All rights reserved.

鏈枃浠朵负 BidAgent Open Core 妯″紡涓嬬殑涓撴湁浠ｇ爜锛屼笉鍦?Apache License 2.0 鎺堟潈鑼冨洿鍐呫€?浠呬緵璇勪及銆佸鏈瓟杈╀笌鍟嗕笟鎺堟潈瀹㈡埛浣跨敤銆傛湭缁忎功闈㈣鍙紝涓嶅緱鐢ㄤ簬鍟嗕笟鐢熶骇鐜銆?
濡傞渶鍟嗕笟鎺堟潈锛岃鑱旂郴锛?3566878907@163.com

Copyright 2026 寰愭禋閽? 鐜嬬ク鏄?(鏍囧皬鏅哄洟闃?. All rights reserved.
"""
"""BOQ 工程量清单智能校验引擎。

提供清单提取、市场基准价格匹配、报价异常检测和归一化评分。
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from app.utils.logger import get_logger

logger = get_logger("boq_engine")


@dataclass
class BOQReport:
    """BOQ 报价分析报告。"""

    project_name: str = ""
    total_budget: float = 0.0
    items: list[dict[str, Any]] = field(default_factory=list)
    suspicious_count: int = 0
    score: float = 100.0
    summary: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典。"""
        score = round(self.score, 1)
        return {
            "version": "v2",
            "project_name": self.project_name,
            "total_budget": self.total_budget,
            "items": self.items,
            "suspicious_count": self.suspicious_count,
            "score": score,
            "risk_level": (
                "高风险"
                if score < 60
                else ("中风险" if score < 80 else "低风险")
            ),
            "summary": self.summary,
            "created_at": (
                self.created_at or time.strftime("%Y-%m-%d %H:%M:%S")
            ),
            "engine": "benchmark_normalized_v2",
        }


_CATEGORY_BENCHMARKS: dict[str, dict[str, Any]] = {
    "充电桩": {"avg": 45000, "unit": "台", "std": 0.30},
    "服务器": {"avg": 85000, "unit": "台", "std": 0.35},
    "电脑": {"avg": 5000, "unit": "台", "std": 0.20},
    "交换机": {"avg": 12000, "unit": "台", "std": 0.35},
    "空调": {"avg": 8000, "unit": "台", "std": 0.25},
    "电梯": {"avg": 250000, "unit": "部", "std": 0.30},
    "水泵": {"avg": 15000, "unit": "台", "std": 0.30},
    "变压器": {"avg": 150000, "unit": "台", "std": 0.35},
    "打印机": {"avg": 3000, "unit": "台", "std": 0.30},
    "LED屏": {"avg": 8000, "unit": "平米", "std": 0.40},
    "电缆": {"avg": 120, "unit": "米", "std": 0.20},
    "家具": {"avg": 3000, "unit": "套", "std": 0.50},
    "软件": {"avg": 50000, "unit": "套", "std": 0.60},
    "监理": {"avg": 50000, "unit": "项", "std": 0.40},
    "设计": {"avg": 80000, "unit": "项", "std": 0.50},
    "物业服务": {"avg": 500000, "unit": "项", "std": 0.40},
    "车辆": {"avg": 200000, "unit": "辆", "std": 0.30},
    "医疗器械": {"avg": 50000, "unit": "台", "std": 0.50},
    "教学设备": {"avg": 15000, "unit": "套", "std": 0.40},
    "办公用品": {"avg": 200, "unit": "套", "std": 0.30},
}

# 品类名称虽然存在，但这些上下文通常表示服务、工程或附属品。
_NON_PRODUCT_CONTEXTS = {
    "维修",
    "维护",
    "维保",
    "租赁",
    "培训",
    "工程",
    "施工",
    "服务",
    "回收",
    "配件",
    "耗材",
}

_IGNORED_NAMES = {"采购", "共计", "合计", "预算", "总额"}
_UNITS = r"平方米|平米|公里|台|套|个|项|米|部|辆"
_ITEM_END = r"(?=\s*(?:[,，。；;、]|和|及|单价|预算|合计|共计|总额|$))"


def _normalize_name(name: str) -> str:
    """规范化品名，用于匹配和去重。"""
    return re.sub(r"[\s,，。；;、:：()（）\-_/\\]+", "", name).lower()


def _clean_name(name: str) -> str:
    """清除提取结果中的常见采购描述前缀。"""
    cleaned = name.strip(" ,，。；;、:：")
    prefixes = (
        "本项目",
        "项目",
        "计划",
        "拟",
        "需要",
        "需",
        "采购",
        "购置",
        "购买",
        "共计",
        "合计",
        "的",
    )

    changed = True
    while changed:
        changed = False
        for prefix in prefixes:
            if cleaned.startswith(prefix) and len(cleaned) > len(prefix):
                cleaned = cleaned[len(prefix):]
                changed = True
                break

    return cleaned.strip()


def _find_category(name: str) -> dict[str, Any] | None:
    """按精确优先、长品类优先策略匹配市场品类。

    规则：
    1. 规范化后完全相等时直接命中；
    2. 否则只允许 category in name，不做反向包含；
    3. 品类按长度降序，避免短品类抢先命中；
    4. 维修、工程、租赁等非采购品上下文拒绝匹配。
    """
    normalized = _normalize_name(name)
    if not normalized:
        return None

    categories = sorted(
        _CATEGORY_BENCHMARKS.items(),
        key=lambda pair: len(_normalize_name(pair[0])),
        reverse=True,
    )

    for category, benchmark in categories:
        if normalized == _normalize_name(category):
            return benchmark

    for category, benchmark in categories:
        normalized_category = _normalize_name(category)
        if normalized_category not in normalized:
            continue

        residual = normalized.replace(normalized_category, "", 1)
        if any(word in residual for word in _NON_PRODUCT_CONTEXTS):
            continue
        return benchmark

    return None


def _item_key(item: dict[str, Any]) -> str:
    """返回清单项去重键。"""
    return _normalize_name(str(item.get("name", "")))


def _append_unique(
    items: list[dict[str, Any]],
    name: str,
    quantity: str,
    unit: str,
) -> None:
    """追加清单项，同时按规范化品名去重。"""
    cleaned = _clean_name(name)
    if len(cleaned) < 2 or cleaned in _IGNORED_NAMES:
        return

    key = _normalize_name(cleaned)
    if not key or any(_item_key(item) == key for item in items):
        return

    items.append(
        {
            "name": cleaned,
            "quantity": float(quantity),
            "unit": unit,
            "unit_price": 0.0,
            "total_price": 0.0,
        }
    )


def _extract(text: str) -> tuple[list[dict[str, Any]], float]:
    """提取清单项、数量、单价和预算。"""
    if not text or not text.strip():
        return [], 0.0

    items: list[dict[str, Any]] = []

    quantity_first = re.compile(
        rf"(\d+(?:\.\d+)?)\s*({_UNITS})\s*[的]?"
        rf"([\u4e00-\u9fa5A-Za-z]{{2,20}}?){_ITEM_END}"
    )
    name_first = re.compile(
        rf"([\u4e00-\u9fa5A-Za-z]{{2,20}}?)\s*"
        rf"(\d+(?:\.\d+)?)\s*({_UNITS}){_ITEM_END}"
    )

    for match in quantity_first.finditer(text):
        _append_unique(
            items,
            match.group(3),
            match.group(1),
            match.group(2),
        )

    for match in name_first.finditer(text):
        _append_unique(
            items,
            match.group(1),
            match.group(2),
            match.group(3),
        )

    for item in items:
        index = text.find(item["name"])
        if index < 0:
            continue

        context = re.split(
            r"[；;。\n]",
            text[index:index + 100],
            maxsplit=1,
        )[0]
        price_match = re.search(
            r"(?:单价|单价为|单价是|单价约)\s*[:：]?\s*"
            r"[¥￥]?\s*(\d[\d,]*(?:\.\d+)?)",
            context,
        )
        if price_match:
            unit_price = float(price_match.group(1).replace(",", ""))
            item["unit_price"] = unit_price
            item["total_price"] = item["quantity"] * unit_price

    budget = 0.0
    budget_match = re.search(
        r"预算(?:金额|总额)?\s*[约共计为是]*\s*[:：]?\s*"
        r"[¥￥]?\s*(\d[\d,]*(?:\.\d+)?)\s*万(?:元)?",
        text,
    )
    if budget_match:
        budget = float(budget_match.group(1).replace(",", "")) * 10000

    return items, budget


def _check_all(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """批量校验价格，并再次按规范化品名去重。"""
    result: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in items:
        key = _item_key(item)
        if not key or key in seen:
            continue
        seen.add(key)

        entry = {
            **item,
            "status": "normal",
            "remark": "",
        }
        benchmark = _find_category(str(item.get("name", "")))
        unit_price = float(item.get("unit_price") or 0)

        if not benchmark:
            entry["remark"] = "未匹配到市场基准品类"
        elif unit_price <= 0:
            entry["remark"] = "未提供单价，无法进行价格异常判断"
        else:
            low = benchmark["avg"] * (1 - benchmark["std"]) * 0.6
            high = benchmark["avg"] * (1 + benchmark["std"]) * 1.5

            if unit_price < low:
                entry["status"] = "underpriced"
                entry["remark"] = (
                    f"远低于市场均价¥{benchmark['avg']:,}/"
                    f"{benchmark['unit']}，疑似漏项"
                )
            elif unit_price > high:
                entry["status"] = "overpriced"
                entry["remark"] = (
                    f"高于市场均价¥{benchmark['avg']:,}/"
                    f"{benchmark['unit']}，建议核查"
                )
            else:
                entry["remark"] = (
                    f"在市场合理范围内（均价¥{benchmark['avg']:,}/"
                    f"{benchmark['unit']}）"
                )

        result.append(entry)

    return result


def _analyze(text: str, project_name: str = "") -> BOQReport:
    """同步执行完整 BOQ 分析。"""
    report = BOQReport(
        project_name=project_name,
        created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    )

    items, budget = _extract(text)
    checked = _check_all(items)
    suspicious = [
        item for item in checked if item["status"] != "normal"
    ]

    report.total_budget = budget
    report.items = checked
    report.suspicious_count = len(suspicious)

    if not checked:
        report.score = 100.0
        report.summary = "未提取到工程量清单数据"
        return report

    anomaly_ratio = len(suspicious) / max(len(checked), 1)
    deduction = int(anomaly_ratio * 60)
    if budget == 0:
        deduction += 10

    report.score = max(0.0, 100.0 - deduction)

    parts: list[str] = []
    if suspicious:
        parts.append(f"{len(suspicious)}项价格异常")
    if budget == 0:
        parts.append("未提取预算金额")
    if not parts:
        parts.append("清单校验通过")

    report.summary = "，".join(parts) + "。"
    return report


async def analyze_boq(
    text: str,
    project_name: str = "",
) -> dict[str, Any]:
    """异步分析 BOQ，将同步正则和计算卸载到执行器。"""
    loop = asyncio.get_running_loop()
    task = partial(_analyze, text or "", project_name or "")
    report = await loop.run_in_executor(None, task)
    return report.to_dict()
