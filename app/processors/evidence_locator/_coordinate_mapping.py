"""Coordinate mapping utilities for evidence locator.

Provides index-building helpers for L2 (whitespace-stripped) and L3
(punctuation-stripped) matching, plus core substring extraction for L4.
"""
from __future__ import annotations

import re
from typing import List, Tuple


class _CoordinateMappingMixin:
    """Mixin: coordinate mapping utilities for EvidenceLocator.

    Expects the host class to set ``self._normalized_text`` and
    ``self._offset_mapping`` (an ``OffsetMapping`` instance) before any
    method here is invoked.
    """

    # 中文标点 → 半角/空映射（去标点时使用）
    # 注意：标点去除后不补任何字符，直接删除
    _PUNCT_PATTERN = re.compile(
        r"["
        r"\u3000-\u303F"      # CJK 符号和标点（、。·等）
        r"\uFF00-\uFFEF"       # 半角/全角形式（！＂＃等）
        r"!-/:-@\[-`{-~"       # ASCII 标点和符号
        r"\u2018-\u201F"       # 引号（'…'等）
        r"\u2026"              # 省略号
        r"]"
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

    def _strip_punctuation(self, text: str) -> str:
        """去除标点符号。"""
        return self._PUNCT_PATTERN.sub("", text)

    def _build_no_punct_index(self) -> Tuple[str, List[int]]:
        """构建无标点规范化文本及其到规范化文本的索引映射。

        在已去空白的基础上再去标点，缓存避免重复计算。
        """
        if not self._normalized_text:
            return ("", [])

        if hasattr(self, "_no_punct_cache"):
            return self._no_punct_cache

        no_ws_text, no_ws_to_norm = self._build_no_whitespace_index()

        no_punct_chars: List[str] = []
        no_punct_to_no_ws: List[int] = []

        for i, ch in enumerate(no_ws_text):
            if not self._PUNCT_PATTERN.match(ch):
                no_punct_chars.append(ch)
                no_punct_to_no_ws.append(i)

        no_punct_text = "".join(no_punct_chars)
        self._no_punct_cache = (no_punct_text, no_punct_to_no_ws)
        return self._no_punct_cache

    def _extract_core_substrings(self, candidate: str) -> List[str]:
        """提取候选证据的核心子串。

        策略：
        1. 按标点/空白切分候选文本
        2. 过滤掉长度<2的片段
        3. 如果切分后片段数<=1，且文本较长，用滑动窗口提取子串
        4. 额外尝试"去除首尾额外字符后的连续片段"（修复 L4 MISS bug）
        5. 按长度降序排序（优先长片段，更具有区分性）
        6. 返回前 15 个

        用于 L4 匹配：当 L1-L3 都失败时，用核心子串在原文中查找。

        修复历史：
        - v1.0: 只按标点切分，遇到含连字符的编号（如 QDQZZB-260702）会误切
        - v1.1: 加滑动窗口，但窗口边界可能切错
        - v1.2: 加"去除首尾额外字符"策略，保留候选中间的连字符/点号等
        """
        if not candidate:
            return []

        # 切分：按标点和空白（注意：连字符 - 在字符集中，会切分 QDQZZB-260702）
        parts = re.split(r"[\s\u3000-\u303F\uFF00-\uFFEF!-/:-@\[-`{-~\u2018-\u201F\u2026]+", candidate)
        # 过滤长度<2的片段
        parts = [p for p in parts if len(p) >= 2]

        # 修复 L4 MISS：尝试"去除首尾额外字符后的连续片段"
        # 例如候选 "根据公告QDQZZB-260702的内容"，提取 ASCII 片段 "QDQZZB-260702"
        # 用正则提取所有 ASCII 字母数字+连字符的连续片段（长度>=4）
        ascii_pattern = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-]{2,}[A-Za-z0-9]")
        for m in ascii_pattern.finditer(candidate):
            ascii_part = m.group()
            if len(ascii_part) >= 4 and ascii_part not in parts:
                parts.append(ascii_part)

        # 同时尝试"去除首尾额外字符后的连续片段"（用宽松切分）
        loose_parts = re.split(r"[\s\u3000-\u303F\uFF00-\uFFEF\u2018-\u201F\u2026]+", candidate)
        for lp in loose_parts:
            # 进一步去除首尾的非字母数字字符
            stripped = lp.strip("!-/:-@[-`{-~")
            if len(stripped) >= 4 and stripped not in parts:
                parts.append(stripped)

        # 额外：提取数字+单位组合（如 "229.577666万元"）
        amount_pattern = re.compile(r"\d+(?:\.\d+)?\s*(?:万元|亿元|元|万|亿)")
        for m in amount_pattern.finditer(candidate):
            amount_part = m.group()
            if len(amount_part) >= 4 and amount_part not in parts:
                parts.append(amount_part)

        # 如果切分后片段数<=1，且文本较长，用滑动窗口提取子串
        if len(parts) <= 1 and len(candidate) >= 6:
            window_size = min(len(candidate) - 2, max(6, len(candidate) // 2))
            for i in range(0, len(candidate) - window_size + 1, max(1, window_size // 3)):
                sub = candidate[i:i + window_size]
                if len(sub) >= 4:
                    parts.append(sub)

        # 按长度降序
        parts.sort(key=len, reverse=True)
        # 去重保持顺序
        seen = set()
        unique_parts = []
        for p in parts:
            if p not in seen:
                seen.add(p)
                unique_parts.append(p)
        # 取前 15 个
        return unique_parts[:15]
