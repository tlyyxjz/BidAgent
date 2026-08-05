"""组织名称规范化与消歧逻辑（从 organization.py 拆出）。

对应总规划 v4.1 第四章 4.4 Organization 名称消歧部分。

职责：
- 名称规范化清洗（normalize_org_name）
- 名称哈希计算（compute_name_hash）
- 消歧结果数据类（DisambiguationResult）
- 消歧主逻辑（disambiguate_organization）：raw_name → organization_id 映射

消歧基于名称规范化 + 统一社会信用代码 + SimHash 模糊匹配。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Optional


# ========== 名称规范化 + 消歧 ==========

# 名称规范化清洗规则
_NAME_CLEAN_PATTERNS = [
    (re.compile(r"[（(].*?[)）]"), ""),           # 去除括号内容
    (re.compile(r"股份有限公司$"), "股份有限公司"),  # 统一后缀
    (re.compile(r"有限公司$"), "有限公司"),
    (re.compile(r"责任公司$"), "责任公司"),
    (re.compile(r"\s+"), ""),                     # 去除空白
    (re.compile(r"[·•・]"), "·"),                 # 统一间隔号
]


def normalize_org_name(raw_name: str) -> str:
    """规范化组织名称（用于消歧）。

    清洗规则：
    1. 去除括号内容（如"(上海)"）
    2. 统一公司后缀（股份有限公司/有限公司/责任公司）
    3. 去除空白
    4. 统一间隔号

    Args:
        raw_name: 原始名称

    Returns:
        规范化后的名称
    """
    if not raw_name:
        return ""
    name = raw_name.strip()
    for pattern, replacement in _NAME_CLEAN_PATTERNS:
        name = pattern.sub(replacement, name)
    return name.strip()


def compute_name_hash(normalized_name: str) -> str:
    """计算规范化名称的哈希（用于快速查重）。

    Args:
        normalized_name: 规范化后的名称

    Returns:
        SHA256 前 16 字符
    """
    return hashlib.sha256(normalized_name.encode("utf-8")).hexdigest()[:16]


# ========== 消歧结果数据类 ==========

@dataclass
class DisambiguationResult:
    """组织名称消歧结果。"""
    # 是否消歧成功
    matched: bool
    # 匹配到的 organization_id（若 matched=False 则为 None）
    organization_id: Optional[str] = None
    # 规范化后的名称
    normalized_name: str = ""
    # 名称哈希
    name_hash: str = ""
    # 置信度（0.0-1.0）
    confidence: float = 0.0
    # 匹配方式（exact_credit_code / exact_name / fuzzy_name / no_match）
    match_method: str = "no_match"
    # 候选列表（模糊匹配时可能有多个候选）
    candidates: list = None

    def __post_init__(self):
        if self.candidates is None:
            self.candidates = []


# ========== 消歧逻辑 ==========

def disambiguate_organization(
    raw_name: str,
    *,
    unified_credit_code: str = "",
    existing_orgs: list = None,
    fuzzy_threshold: int = 3,
) -> DisambiguationResult:
    """组织名称消歧。

    消歧优先级（从高到低）：
    1. unified_credit_code 精确匹配（置信度 1.0）
    2. normalized_name 精确匹配（置信度 0.95）
    3. 名称模糊匹配（SimHash 汉明距离 ≤ threshold，置信度 0.7-0.9）
    4. 无匹配（新建组织）

    Args:
        raw_name: 原始名称
        unified_credit_code: 统一社会信用代码（可选）
        existing_orgs: 现有组织列表 [(organization_id, normalized_name, unified_credit_code)]
        fuzzy_threshold: 模糊匹配汉明距离阈值（默认 3）

    Returns:
        DisambiguationResult
    """
    if not raw_name or not raw_name.strip():
        return DisambiguationResult(matched=False, match_method="empty_name")

    normalized = normalize_org_name(raw_name)
    name_hash = compute_name_hash(normalized)

    if not existing_orgs:
        return DisambiguationResult(
            matched=False,
            normalized_name=normalized,
            name_hash=name_hash,
            match_method="no_match",
        )

    # 1. unified_credit_code 精确匹配
    if unified_credit_code:
        for org_id, org_name, org_code in existing_orgs:
            if org_code and org_code == unified_credit_code:
                return DisambiguationResult(
                    matched=True,
                    organization_id=org_id,
                    normalized_name=normalized,
                    name_hash=name_hash,
                    confidence=1.0,
                    match_method="exact_credit_code",
                )

    # 2. normalized_name 精确匹配
    for org_id, org_name, org_code in existing_orgs:
        if org_name and org_name == normalized:
            return DisambiguationResult(
                matched=True,
                organization_id=org_id,
                normalized_name=normalized,
                name_hash=name_hash,
                confidence=0.95,
                match_method="exact_name",
            )

    # 3. 模糊匹配（SimHash）
    from app.processors.simhash import compute_simhash, hamming_distance

    target_hash = compute_simhash(normalized)
    candidates = []
    for org_id, org_name, org_code in existing_orgs:
        if not org_name:
            continue
        cand_hash = compute_simhash(org_name)
        if target_hash == 0 or cand_hash == 0:
            continue
        dist = hamming_distance(target_hash, cand_hash)
        if dist <= fuzzy_threshold:
            # 汉明距离越小置信度越高
            confidence = max(0.7, 0.9 - 0.1 * dist)
            candidates.append((org_id, org_name, dist, confidence))

    if candidates:
        # 按汉明距离升序，取最相似的
        candidates.sort(key=lambda x: x[2])
        best = candidates[0]
        return DisambiguationResult(
            matched=True,
            organization_id=best[0],
            normalized_name=normalized,
            name_hash=name_hash,
            confidence=best[3],
            match_method="fuzzy_name",
            candidates=candidates,
        )

    # 4. 无匹配
    return DisambiguationResult(
        matched=False,
        normalized_name=normalized,
        name_hash=name_hash,
        match_method="no_match",
    )
