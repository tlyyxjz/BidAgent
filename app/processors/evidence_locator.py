"""W2-03 确定性证据搜索与验证引擎。

对应总规划 v4.1 第六章 6.1「程序逐段搜索候选证据」+ 第六章 6.2 抽取支持度。

实现 5 级降级匹配策略：
- L1 精确匹配：候选证据文本在原文中直接出现
- L2 去空白匹配：忽略空白差异后匹配
- L3 去标点匹配：忽略标点符号差异后匹配（Day 2 实现）
- L4 核心子串匹配：取候选证据的核心子串进行匹配（Day 2 实现）
- L5 失败：标记为 unsupported

本文件 Day 1 实现 L1 + L2。

工程约束：
- 纯确定性算法，不调用 LLM
- 性能目标：小于 20KB 文本 P95 不超过 200ms
- 找不到证据时必须标记为 unsupported，不得伪造
- 匹配结果生成 EvidenceLocation，包含 start/end/text/match_type/confidence
- 支持 search_from 参数控制匹配起始位置
- 支持批量定位接口（解决同名文本多次出现）
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple

from app.processors.normalizer import normalize_text, OffsetMapping


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


class EvidenceLocator:
    """证据搜索引擎。

    在 clean_raw_text 中定位候选证据文本，返回 EvidenceLocation。

    使用 5 级降级匹配策略，Day 1 实现 L1 + L2。
    """

    def __init__(self, raw_text: str, precompute_normalized: bool = True):
        """初始化证据搜索引擎。

        Args:
            raw_text: 清洗后的原始文本（clean_raw_text）
            precompute_normalized: 是否预计算规范化文本（提升多次查询性能）
        """
        self.raw_text = raw_text
        self._normalized_text: Optional[str] = None
        self._offset_mapping: Optional[OffsetMapping] = None

        if precompute_normalized and raw_text:
            self._normalized_text, self._offset_mapping = normalize_text(raw_text)

    def locate(
        self,
        candidate_text: str,
        search_from: int = 0,
        levels: Optional[List[MatchType]] = None,
    ) -> LocateResult:
        """定位候选证据文本在原文中的位置。

        Args:
            candidate_text: 候选证据文本（来自 LLM 输出或人工标注）
            search_from: 从原文的哪个偏移开始搜索（用于同名文本多次出现）
            levels: 尝试的匹配级别（默认 L1→L2，Day 2 扩展到 L3→L4）

        Returns:
            LocateResult
        """
        start_time = time.perf_counter()

        if not candidate_text:
            return LocateResult(
                found=False,
                search_from=search_from,
                elapsed_ms=0,
                error="candidate_text is empty",
            )

        if not self.raw_text:
            return LocateResult(
                found=False,
                search_from=search_from,
                elapsed_ms=0,
                error="raw_text is empty",
            )

        if levels is None:
            # Day 1 默认 L1 → L2
            levels = [MatchType.EXACT, MatchType.STRIPPED]

        # 依次尝试各级匹配
        for level in levels:
            if level == MatchType.EXACT:
                result = self._match_exact(candidate_text, search_from)
            elif level == MatchType.STRIPPED:
                result = self._match_stripped(candidate_text, search_from)
            else:
                # Day 2 才实现 L3/L4
                continue

            if result is not None:
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                result.elapsed_ms = elapsed_ms
                return LocateResult(
                    found=True,
                    location=result,
                    search_from=search_from,
                    elapsed_ms=elapsed_ms,
                )

        elapsed_ms = (time.perf_counter() - start_time) * 1000
        return LocateResult(
            found=False,
            search_from=search_from,
            elapsed_ms=elapsed_ms,
        )

    def locate_batch(
        self,
        candidates: List[str],
        search_from: int = 0,
    ) -> List[LocateResult]:
        """批量定位多个候选证据。

        解决同一公告中多个相同证据文本的定位问题：
        每次成功匹配后，下一次搜索从上次匹配结束位置开始。

        Args:
            candidates: 候选证据文本列表
            search_from: 初始搜索位置

        Returns:
            List[LocateResult]，长度等于 candidates
        """
        results: List[LocateResult] = []
        current_pos = search_from

        for candidate in candidates:
            result = self.locate(candidate, search_from=current_pos)
            if result.found and result.location:
                # 下一次搜索从当前匹配结束位置开始
                current_pos = result.location.end
            results.append(result)

        return results

    def locate_all_occurrences(
        self,
        candidate_text: str,
        max_count: int = 100,
    ) -> List[EvidenceLocation]:
        """定位候选证据文本在原文中的所有出现位置。

        用于同名文本多次出现的场景。

        Args:
            candidate_text: 候选证据文本
            max_count: 最大返回数量（防止爆炸）

        Returns:
            List[EvidenceLocation]，按出现顺序排列
        """
        locations: List[EvidenceLocation] = []
        search_from = 0

        while len(locations) < max_count:
            result = self.locate(candidate_text, search_from=search_from)
            if not result.found or result.location is None:
                break
            locations.append(result.location)
            search_from = result.location.end

        return locations

    def _match_exact(
        self,
        candidate: str,
        search_from: int,
    ) -> Optional[EvidenceLocation]:
        """L1 精确匹配：候选证据文本在原文中直接出现。"""
        idx = self.raw_text.find(candidate, search_from)
        if idx == -1:
            return None

        start = idx
        end = idx + len(candidate)
        text = self.raw_text[start:end]

        # 计算规范化坐标
        norm_start, norm_end = -1, -1
        if self._offset_mapping:
            norm_start, norm_end = self._offset_mapping.to_normalized(start, end)

        return EvidenceLocation(
            start=start,
            end=end,
            text=text,
            match_type=MatchType.EXACT,
            confidence=1.0,
            normalized_start=norm_start,
            normalized_end=norm_end,
            support_level=SupportLevel.DIRECT,
        )

    def _match_stripped(
        self,
        candidate: str,
        search_from: int,
    ) -> Optional[EvidenceLocation]:
        """L2 去空白匹配：忽略空白差异后匹配。

        算法：
        1. 规范化原文和候选文本（去除所有空白）
        2. 在规范化原文中查找候选
        3. 反向映射回原始坐标
        """
        if not self._normalized_text or not self._offset_mapping:
            return None

        # 去除候选文本的所有空白
        candidate_stripped = re.sub(r"\s+", "", candidate)

        if not candidate_stripped:
            return None

        # 在规范化文本中查找（也需要去除规范化文本的空白进行匹配）
        # 优化：构建一个"无空白规范化文本"和反向映射
        # 但为简单起见，这里用滑动窗口

        # 构建：无空白规范化文本 + 索引映射
        no_ws_text, no_ws_to_norm = self._build_no_whitespace_index()

        if not no_ws_text:
            return None

        # 在无空白文本中查找
        idx = no_ws_text.find(candidate_stripped, 0)
        if idx == -1:
            return None

        # 反向映射：无空白索引 → 规范化索引 → 原始索引
        norm_start_idx = no_ws_to_norm[idx]
        norm_end_idx = no_ws_to_norm[idx + len(candidate_stripped) - 1] + 1

        # 规范化坐标 → 原始坐标
        raw_start, raw_end = self._offset_mapping.to_raw(norm_start_idx, norm_end_idx)

        # 限制搜索范围
        if raw_start < search_from:
            # 继续查找下一个出现
            # 在 no_ws_text 中从 idx+1 开始继续查找
            next_idx = no_ws_text.find(candidate_stripped, idx + 1)
            if next_idx == -1:
                return None
            norm_start_idx = no_ws_to_norm[next_idx]
            norm_end_idx = no_ws_to_norm[next_idx + len(candidate_stripped) - 1] + 1
            raw_start, raw_end = self._offset_mapping.to_raw(norm_start_idx, norm_end_idx)
            if raw_start < search_from:
                return None

        # 提取实际匹配的原文片段（可能包含空白）
        text = self.raw_text[raw_start:raw_end]

        return EvidenceLocation(
            start=raw_start,
            end=raw_end,
            text=text,
            match_type=MatchType.STRIPPED,
            confidence=0.9,
            normalized_start=norm_start_idx,
            normalized_end=norm_end_idx,
            support_level=SupportLevel.EQUIVALENT,
        )

    def _build_no_whitespace_index(self) -> Tuple[str, List[int]]:
        """构建无空白规范化文本及其到规范化文本的索引映射。

        缓存以避免重复计算。
        """
        if not self._normalized_text:
            return ("", [])

        if hasattr(self, "_no_ws_cache"):
            return self._no_ws_cache

        no_ws_chars: List[str] = []
        no_ws_to_norm: List[int] = []

        for i, ch in enumerate(self._normalized_text):
            if not ch.isspace():
                no_ws_chars.append(ch)
                no_ws_to_norm.append(i)

        no_ws_text = "".join(no_ws_chars)
        self._no_ws_cache = (no_ws_text, no_ws_to_norm)
        return self._no_ws_cache


def verify_evidence(
    raw_text: str,
    evidence_text: str,
    start: int,
    end: int,
) -> Tuple[bool, str]:
    """验证证据偏移量是否正确。

    Args:
        raw_text: 原始文本
        evidence_text: 证据文本
        start: 起始偏移（含）
        end: 结束偏移（不含）

    Returns:
        (valid, message)
    """
    if not raw_text:
        return (False, "raw_text is empty")

    if not evidence_text:
        return (False, "evidence_text is empty")

    if not (isinstance(start, int) and isinstance(end, int)):
        return (False, f"start/end must be int, got {type(start)}/{type(end)}")

    if start < 0 or end > len(raw_text) or start >= end:
        return (False, f"offset out of bounds: [{start},{end}], len={len(raw_text)}")

    actual = raw_text[start:end]

    # 完全匹配
    if actual == evidence_text:
        return (True, "exact match")

    # 容忍尾部换行符差异
    if actual.rstrip("\n") == evidence_text.rstrip("\n"):
        return (True, "match after trailing newline trim")

    # 容忍首尾空白差异
    if actual.strip() == evidence_text.strip():
        return (True, "match after strip")

    return (
        False,
        f"slice mismatch: expected='{evidence_text[:30]}...', actual='{actual[:30]}...'",
    )
