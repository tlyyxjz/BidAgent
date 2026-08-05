"""反幻觉校验：核心内容与原文事实一致性检查。

命题硬要求：core_content 必须与原文事实一致（反幻觉）。

实现思路：
- 把 core_content 切成关键事实陈述（数字/日期/金额/单位）
- 与 source_text 原文比对，每个关键事实必须能在原文找到
- 不在原文的事实标记为"幻觉"，记录到校验报告

工程规范：
- 纯函数，无副作用
- 不依赖 LLM（避免循环依赖 + 控制成本）
- 误报率优先于漏报率（宁可放过，不可错杀）

M-4 修复：招标编号正则进一步收紧，要求字母+数字混合或常见前缀。
M-5 修复：金额/日期归一化后比对，避免 "1 万元" vs "10000 元" 误判。
m-1 修复：数量正则去掉重复的"套"。

正则模式与归一化函数拆分至 app.processors.hallucination_patterns。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.processors.hallucination_patterns import (  # noqa: F401  re-export 保持向后兼容
    _AMOUNT_RE,
    _DATE_RE,
    _PATTERNS,
    _normalize_amount,
    _normalize_date,
)
from app.utils.logger import get_logger

logger = get_logger("hallucination_checker")


@dataclass
class Fact:
    """一个事实陈述。"""

    category: str  # 金额/日期/百分比/数量/...
    value: str     # 原始匹配字符串
    in_source: bool = False  # 是否在原文中找到


@dataclass
class CheckReport:
    """反幻觉校验报告。"""

    passed: bool                  # 整体是否通过（无幻觉）
    total_facts: int = 0          # 提取的事实总数
    verified_facts: int = 0       # 在原文找到的事实数
    hallucinated_facts: int = 0   # 幻觉事实数
    facts: list[Fact] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "total_facts": self.total_facts,
            "verified_facts": self.verified_facts,
            "hallucinated_facts": self.hallucinated_facts,
            "hallucinated_values": [
                {"category": f.category, "value": f.value}
                for f in self.facts if not f.in_source
            ],
        }


def extract_facts(text: str) -> list[Fact]:
    """从 core_content 提取关键事实。"""
    if not text:
        return []
    facts: list[Fact] = []
    seen: set[tuple[str, str]] = set()
    for category, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(0).strip()
            key = (category, value)
            if key in seen:
                continue
            seen.add(key)
            facts.append(Fact(category=category, value=value))
    return facts


def _fact_in_source(fact: Fact, source_text: str) -> bool:
    """判断单个事实是否在原文中找到。

    M-5 修复：金额/日期先归一化后比对，避免 "1万元" vs "10000元" 误判。
    其他类别仍用去空格子串匹配。
    """
    if not source_text:
        return False

    normalized_source = re.sub(r"\s+", "", source_text)
    normalized_value = re.sub(r"\s+", "", fact.value)
    if normalized_value in normalized_source:
        return True

    # M-5 归一化比对：金额 / 日期
    if fact.category == "金额":
        target_norm = _normalize_amount(fact.value)
        if target_norm:
            # 新-3 修复：原文也必须匹配带单位的金额，避免误匹配纯数字
            for m in _AMOUNT_RE.finditer(source_text):
                src_norm = _normalize_amount(m.group(0))
                if src_norm and src_norm == target_norm:
                    return True
        return False

    if fact.category == "日期":
        target_norm = _normalize_date(fact.value)
        if target_norm:
            # 新-6：_DATE_RE 已支持点号格式
            for m in _DATE_RE.finditer(source_text):
                src_norm = _normalize_date(m.group(0))
                if src_norm and src_norm == target_norm:
                    return True
        return False

    return False


def check_content(
    core_content: str,
    source_text: str,
    strict: bool = False,
) -> CheckReport:
    """校验 core_content 与 source_text 的事实一致性。

    Args:
        core_content: 待校验的核心内容（可能是 LLM 生成）
        source_text: 原文（采集到的页面文本）
        strict: 严格模式（任何事实不在原文都视为幻觉）
                False 时：金额/日期/招标编号必须找到，其他类别宽容

    Returns:
        CheckReport
    """
    if not core_content:
        return CheckReport(passed=True)

    if not source_text:
        # 无原文可比，无法校验，默认通过（避免阻塞）
        logger.warning("source_text is empty, skip hallucination check")
        return CheckReport(passed=True)

    facts = extract_facts(core_content)
    if not facts:
        # 没提取到事实，默认通过
        return CheckReport(passed=True, total_facts=0)

    # 严格类别：必须找到
    strict_categories = {"金额", "日期", "招标编号", "联系电话", "邮箱"}

    verified = 0
    hallucinated = 0
    for fact in facts:
        if _fact_in_source(fact, source_text):
            fact.in_source = True
            verified += 1
        else:
            if strict or fact.category in strict_categories:
                fact.in_source = False
                hallucinated += 1
                logger.info(
                    "hallucination detected: category=%s value=%s",
                    fact.category, fact.value,
                )
            else:
                # 非严格模式下，非严格类别视为通过
                fact.in_source = True
                verified += 1

    passed = hallucinated == 0
    return CheckReport(
        passed=passed,
        total_facts=len(facts),
        verified_facts=verified,
        hallucinated_facts=hallucinated,
        facts=facts,
    )


def check_items(
    items: list[dict[str, Any]],
    source_texts: dict[str, str] | None = None,
) -> dict[str, Any]:
    """批量校验。

    Args:
        items: tender item 列表，必须有 core_content 字段
        source_texts: 可选，{source_url: source_text} 映射

    Returns:
        {
            "total_items": N,
            "passed_items": N,
            "failed_items": N,
            "hallucinated_total": N,
            "details": [...]
        }
    """
    source_texts = source_texts or {}
    total = len(items)
    passed_count = 0
    failed_count = 0
    total_hallucinated = 0
    details: list[dict[str, Any]] = []

    for idx, item in enumerate(items):
        core = item.get("core_content") or ""
        source_url = item.get("source_url") or ""
        source_text = source_texts.get(source_url, "")

        report = check_content(core, source_text)
        if report.passed:
            passed_count += 1
        else:
            failed_count += 1
            total_hallucinated += report.hallucinated_facts

        details.append({
            "index": idx,
            "source_url": source_url,
            "passed": report.passed,
            "total_facts": report.total_facts,
            "hallucinated_facts": report.hallucinated_facts,
        })

    return {
        "total_items": total,
        "passed_items": passed_count,
        "failed_items": failed_count,
        "hallucinated_total": total_hallucinated,
        "details": details,
    }
