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

    # ========== L3 去标点匹配 ==========

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

    # ========== L4 核心子串匹配 ==========

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
