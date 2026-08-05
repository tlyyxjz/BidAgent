"""Evidence matcher algorithms (L1-L4) for EvidenceLocator."""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from app.processors.evidence_locator._models import (
    EvidenceLocation,
    MatchType,
    SupportLevel,
)


class _MatchersMixin:
    """Mixin: 5-level degradation matchers used by EvidenceLocator.

    Expects the host class to provide:
    - ``self.raw_text`` (str)
    - ``self._normalized_text`` (Optional[str])
    - ``self._offset_mapping`` (Optional[OffsetMapping])
    - ``self._build_no_whitespace_index()`` -> Tuple[str, List[int]]
    - ``self._build_no_punct_index()`` -> Tuple[str, List[int]]
    - ``self._strip_punctuation(text)`` -> str
    """

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

    def _match_no_punct(
        self,
        candidate: str,
        search_from: int,
    ) -> Optional[EvidenceLocation]:
        """L3 去标点匹配：忽略标点符号差异后匹配。

        算法：
        1. 在 L2 去空白基础上，再去标点
        2. 在无标点文本中查找候选
        3. 反向映射：无标点 → 无空白 → 规范化 → 原始
        """
        if not self._normalized_text or not self._offset_mapping:
            return None

        # 候选文本去空白再去标点
        candidate_stripped = re.sub(r"\s+", "", candidate)
        candidate_no_punct = self._strip_punctuation(candidate_stripped)

        if not candidate_no_punct:
            return None

        no_punct_text, no_punct_to_no_ws = self._build_no_punct_index()
        if not no_punct_text:
            return None

        _, no_ws_to_norm = self._build_no_whitespace_index()

        # 在无标点文本中查找
        idx = no_punct_text.find(candidate_no_punct, 0)
        if idx == -1:
            return None

        # 反向映射：无标点 → 无空白 → 规范化 → 原始
        def _map_to_raw(punct_idx_start, punct_idx_end):
            no_ws_start = no_punct_to_no_ws[punct_idx_start]
            no_ws_end = no_punct_to_no_ws[punct_idx_end - 1] + 1
            norm_start = no_ws_to_norm[no_ws_start]
            norm_end = no_ws_to_norm[no_ws_end - 1] + 1
            return self._offset_mapping.to_raw(norm_start, norm_end)

        raw_start, raw_end = _map_to_raw(idx, idx + len(candidate_no_punct))

        # search_from 限制
        if raw_start < search_from:
            next_idx = no_punct_text.find(candidate_no_punct, idx + 1)
            if next_idx == -1:
                return None
            raw_start, raw_end = _map_to_raw(next_idx, next_idx + len(candidate_no_punct))
            if raw_start < search_from:
                return None

        text = self.raw_text[raw_start:raw_end]

        # 计算规范化坐标
        norm_start, norm_end = -1, -1
        if self._offset_mapping:
            norm_start, norm_end = self._offset_mapping.to_normalized(raw_start, raw_end)

        return EvidenceLocation(
            start=raw_start,
            end=raw_end,
            text=text,
            match_type=MatchType.NO_PUNCT,
            confidence=0.8,
            normalized_start=norm_start,
            normalized_end=norm_end,
            support_level=SupportLevel.INFERRED,
        )

    def _match_substring(
        self,
        candidate: str,
        search_from: int,
    ) -> Optional[EvidenceLocation]:
        """L4 核心子串匹配：用候选的核心子串在原文中查找。

        算法：
        1. 提取候选的核心子串（按标点切分，取长度>=2的前5个）
        2. 对每个子串尝试 L1 精确匹配
        3. 取第一个匹配成功的子串作为证据位置
        4. 证据范围扩展到包含该子串的原文片段
        """
        core_subs = self._extract_core_substrings(candidate)
        if not core_subs:
            return None

        for sub in core_subs:
            # 用 L1 精确匹配查找子串
            idx = self.raw_text.find(sub, search_from)
            if idx == -1:
                continue

            start = idx
            end = idx + len(sub)
            text = self.raw_text[start:end]

            # 计算规范化坐标
            norm_start, norm_end = -1, -1
            if self._offset_mapping:
                norm_start, norm_end = self._offset_mapping.to_normalized(start, end)

            return EvidenceLocation(
                start=start,
                end=end,
                text=text,
                match_type=MatchType.SUBSTRING,
                confidence=0.6,
                normalized_start=norm_start,
                normalized_end=norm_end,
                support_level=SupportLevel.INFERRED,
            )

        return None
