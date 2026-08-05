"""EvidenceLocator: main search engine combining matcher and coordinate mixins."""
from __future__ import annotations

import time
from typing import List, Optional

from app.processors.normalizer import normalize_text, OffsetMapping
from app.processors.evidence_locator._coordinate_mapping import _CoordinateMappingMixin
from app.processors.evidence_locator._matchers import _MatchersMixin
from app.processors.evidence_locator._models import (
    EvidenceLocation,
    LocateResult,
    MatchType,
    SupportLevel,
)


class EvidenceLocator(_MatchersMixin, _CoordinateMappingMixin):
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
            # Day 2 默认 L1 → L2 → L3 → L4 → L5(UNSUPPORTED)
            levels = [MatchType.EXACT, MatchType.STRIPPED, MatchType.NO_PUNCT, MatchType.SUBSTRING, MatchType.NOT_FOUND]

        # 依次尝试各级匹配
        for level in levels:
            if level == MatchType.EXACT:
                result = self._match_exact(candidate_text, search_from)
            elif level == MatchType.STRIPPED:
                result = self._match_stripped(candidate_text, search_from)
            elif level == MatchType.NO_PUNCT:
                result = self._match_no_punct(candidate_text, search_from)
            elif level == MatchType.SUBSTRING:
                result = self._match_substring(candidate_text, search_from)
            elif level == MatchType.NOT_FOUND:
                # L5：所有级别失败，返回 UNSUPPORTED 标记
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                unsupported_location = EvidenceLocation(
                    start=-1,
                    end=-1,
                    text="",
                    match_type=MatchType.NOT_FOUND,
                    confidence=0.0,
                    normalized_start=-1,
                    normalized_end=-1,
                    support_level=SupportLevel.UNSUPPORTED,
                )
                return LocateResult(
                    found=False,
                    location=unsupported_location,
                    search_from=search_from,
                    elapsed_ms=elapsed_ms,
                    error="unsupported: no evidence found in raw text",
                )
            else:
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
