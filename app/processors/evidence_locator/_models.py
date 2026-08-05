"""Evidence locator shared data models and enums."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

# v4.1 §10.12 证据定位规则版本
EVIDENCE_RULE_VERSION = "evidence_locator_v1.0"


class MatchType(str, Enum):
    """匹配类型枚举。"""
    EXACT = "exact"           # L1 精确匹配
    STRIPPED = "stripped"     # L2 去空白匹配
    NO_PUNCT = "no_punct"     # L3 去标点匹配（Day 2）
    SUBSTRING = "substring"   # L4 核心子串匹配（Day 2）
    NOT_FOUND = "not_found"   # L5 未匹配


class SupportLevel(str, Enum):
    """抽取支持度枚举。"""
    DIRECT = "direct"                 # 直接证据（原文精确出现）
    EQUIVALENT = "equivalent"         # 等价证据（规范化后匹配）
    INFERRED = "inferred"             # 推导证据（L3/L4 匹配）
    UNSUPPORTED = "unsupported"       # 无依据
    CONTRADICTED = "contradicted"     # 冲突证据


@dataclass
class EvidenceLocation:
    """证据定位结果。

    约束：
    - start/end 是基于 clean_raw_text 的偏移量
    - text 是实际匹配到的原文片段（必须等于 raw_text[start:end]）
    - match_type 标记匹配级别
    - confidence 在 0.0~1.0 之间
    """
    start: int                          # 原始文本偏移（含）
    end: int                            # 原始文本偏移（不含）
    text: str                           # 实际匹配到的原文片段
    match_type: MatchType               # 匹配类型
    confidence: float                   # 置信度 0.0~1.0
    normalized_start: int = -1          # 规范化文本偏移（含），-1 表示未计算
    normalized_end: int = -1            # 规范化文本偏移（不含），-1 表示未计算
    support_level: SupportLevel = SupportLevel.UNSUPPORTED  # 支持度

    def to_dict(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "match_type": self.match_type.value,
            "confidence": self.confidence,
            "normalized_start": self.normalized_start,
            "normalized_end": self.normalized_end,
            "support_level": self.support_level.value,
        }


@dataclass
class LocateResult:
    """单次定位结果。"""
    found: bool
    location: Optional[EvidenceLocation] = None
    search_from: int = 0
    elapsed_ms: float = 0.0
    error: Optional[str] = None
