"""W2-02 文本规范化器与双坐标映射。

对应总规划 v4.1 第四章 4.10 偏移量映射原则 + 第六章 6.1「建立原始文本与规范化文本字符映射」。

规范化规则（normalizer_version = "1.0"）：
1. 全角字符转半角（Unicode NFKC 子集，仅处理常见招投标公告字符）
2. 英文字母大小写统一（转小写）
3. 连续空白压缩为单个空格（保留单个空格，但不删除行尾/行首空白后的换行）
4. 不改变语义内容

双坐标映射：
- normalized_index → clean_raw_text_index（正向查询）
- clean_raw_text_index → normalized_index（反向查询）
- 处理全角转半角后字符数改变（如全角"１"→半角"1"，长度不变；
  但连续空白压缩后会减少字符数）

工程约束：
- 规范化规则版本号必须记录（NORMALIZER_VERSION）
- 映射表必须可序列化和反序列化（to_dict / from_dict）
- 不依赖实时网页 DOM
- 性能目标：小于 20KB 文本 P95 不超过 50ms
"""
from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import List, Tuple

# 规范化器版本号（规则变更时必须升级）
NORMALIZER_VERSION = "1.0"

# 全角空格（U+3000）单独处理
_FULLWIDTH_SPACE = "\u3000"

# 连续空白正则（匹配 1 个或多个空白字符，包括空格、制表符、全角空格等，但不含换行）
_WHITESPACE_RUN = re.compile(r"[ \t\u3000\r\f\v]{2,}")

# 行首/行尾空白（去除每行首尾的空格和制表符，但保留换行）
_LINE_TRIM = re.compile(r"^[ \t\u3000]+|[ \t\u3000]+$", re.MULTILINE)


@dataclass
class OffsetMapping:
    """双坐标映射表。

    mapping[i] = j 表示 normalized_text[i] 对应 clean_raw_text[j]。
    反向查询通过 reverse_mapping[j] = i 完成（一对多时取最小 i）。

    约束：
    - mapping 长度等于 normalized_text 长度
    - reverse_mapping 长度等于 clean_raw_text 长度（-1 表示该原始字符被压缩删除）
    - mapping 必须严格递增（normalized 顺序对应 raw 顺序）
    """
    mapping: List[int] = field(default_factory=list)
    reverse_mapping: List[int] = field(default_factory=list)
    normalized_text: str = ""
    raw_text: str = ""
    normalizer_version: str = NORMALIZER_VERSION

    def to_normalized(self, raw_start: int, raw_end: int) -> Tuple[int, int]:
        """原始坐标 → 规范化坐标。

        如果 raw_start 或 raw_end 落在被压缩删除的字符上，
        取最近的未删除字符（向后/向前查找）。
        """
        n_raw = len(self.reverse_mapping)
        n_norm = len(self.mapping)

        if n_norm == 0:
            return (0, 0)

        # 处理 start
        norm_start = -1
        if 0 <= raw_start < n_raw:
            norm_start = self.reverse_mapping[raw_start]
            if norm_start == -1:
                for i in range(raw_start + 1, n_raw):
                    if self.reverse_mapping[i] != -1:
                        norm_start = self.reverse_mapping[i]
                        break
                if norm_start == -1:
                    norm_start = n_norm
        elif raw_start >= n_raw:
            norm_start = n_norm
        else:
            norm_start = 0

        # 处理 end
        norm_end = -1
        if 0 <= raw_end < n_raw:
            norm_end = self.reverse_mapping[raw_end]
            if norm_end == -1:
                for i in range(raw_end - 1, -1, -1):
                    if self.reverse_mapping[i] != -1:
                        norm_end = self.reverse_mapping[i] + 1
                        break
                if norm_end == -1:
                    norm_end = 0
        elif raw_end >= n_raw:
            norm_end = n_norm
        else:
            norm_end = 0

        if norm_start < 0:
            norm_start = 0
        if norm_end > n_norm:
            norm_end = n_norm
        if norm_end < norm_start:
            norm_end = norm_start

        return (norm_start, norm_end)

    def to_raw(self, norm_start: int, norm_end: int) -> Tuple[int, int]:
        """规范化坐标 → 原始坐标。"""
        n_norm = len(self.mapping)
        n_raw = len(self.reverse_mapping)

        if n_norm == 0 or n_raw == 0:
            return (0, 0)

        raw_start = 0
        if 0 <= norm_start < n_norm:
            raw_start = self.mapping[norm_start]
        elif norm_start >= n_norm:
            raw_start = n_raw

        raw_end = 0
        if norm_end <= 0:
            raw_end = 0
        elif norm_end <= n_norm:
            raw_end = self.mapping[norm_end - 1] + 1
        else:
            raw_end = n_raw

        if raw_start < 0:
            raw_start = 0
        if raw_end > n_raw:
            raw_end = n_raw
        if raw_end < raw_start:
            raw_end = raw_start

        return (raw_start, raw_end)

    def to_dict(self) -> dict:
        """序列化为字典（可 JSON 化）。"""
        return {
            "mapping": self.mapping,
            "reverse_mapping": self.reverse_mapping,
            "normalized_text": self.normalized_text,
            "raw_text": self.raw_text,
            "normalizer_version": self.normalizer_version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "OffsetMapping":
        """从字典反序列化。"""
        return cls(
            mapping=list(data["mapping"]),
            reverse_mapping=list(data["reverse_mapping"]),
            normalized_text=data["normalized_text"],
            raw_text=data["raw_text"],
            normalizer_version=data.get("normalizer_version", NORMALIZER_VERSION),
        )


def _normalize_char(ch: str) -> str:
    """规范化单个字符：全角转半角 + 大写转小写。"""
    if ch == _FULLWIDTH_SPACE:
        return " "

    # NFKC 规范化：全角字符 → 半角字符
    normalized = unicodedata.normalize("NFKC", ch)

    # 英文字母转小写
    if len(normalized) == 1 and normalized.isalpha() and normalized.isascii():
        return normalized.lower()

    return normalized


def normalize_text(raw_text: str) -> Tuple[str, OffsetMapping]:
    """规范化文本并生成双坐标映射表。

    规范化步骤：
    1. 逐字符规范化（全角转半角、大写转小写）
    2. 每行首尾空白去除
    3. 行内连续空白压缩为单个空格

    返回 (normalized_text, OffsetMapping)。

    性能：20KB 文本 < 50ms。
    """
    if not raw_text:
        return ("", OffsetMapping())

    start_time = time.perf_counter()

    # 第一步：逐字符规范化
    norm_chars: List[str] = []
    init_map: List[int] = []  # norm_chars 索引 → raw_text 索引

    for raw_idx, ch in enumerate(raw_text):
        norm = _normalize_char(ch)
        for c in norm:
            norm_chars.append(c)
            init_map.append(raw_idx)

    # 第二步：行首行尾空白去除 + 行内连续空白压缩
    final_chars: List[str] = []
    final_to_init: List[int] = []

    i = 0
    n = len(norm_chars)
    in_leading_ws = True

    while i < n:
        ch = norm_chars[i]

        if ch == "\n":
            final_chars.append(ch)
            final_to_init.append(i)
            in_leading_ws = True
            i += 1
            continue

        if ch in " \t\r\f\v":
            j = i
            while j < n and norm_chars[j] in " \t\r\f\v":
                j += 1

            is_line_start = in_leading_ws
            is_line_end = (j >= n) or (norm_chars[j] == "\n")

            if is_line_start or is_line_end:
                pass  # 跳过行首行尾空白
            else:
                final_chars.append(" ")
                final_to_init.append(i)

            i = j
            continue

        final_chars.append(ch)
        final_to_init.append(i)
        in_leading_ws = False
        i += 1

    # 构建 normalized_index → raw_index 映射
    # 同时记录哪些位置是压缩空白（用于反向映射时跳过）
    mapping: List[int] = []
    is_compressed_whitespace: List[bool] = []
    for fi in final_to_init:
        raw_idx = init_map[fi]
        ch = norm_chars[fi]
        mapping.append(raw_idx)
        # 压缩空白字符标记为 True（不设置反向映射）
        is_compressed_whitespace.append(ch in " \t\r\f\v")

    normalized_text = "".join(final_chars)

    # 构建反向映射：raw_index → normalized_index
    # 压缩空白的 raw 字符保持 -1（表示被删除）
    n_raw = len(raw_text)
    reverse_mapping: List[int] = [-1] * n_raw

    for norm_idx, raw_idx in enumerate(mapping):
        if 0 <= raw_idx < n_raw:
            if reverse_mapping[raw_idx] == -1 and not is_compressed_whitespace[norm_idx]:
                reverse_mapping[raw_idx] = norm_idx

    elapsed_ms = (time.perf_counter() - start_time) * 1000

    result = OffsetMapping(
        mapping=mapping,
        reverse_mapping=reverse_mapping,
        normalized_text=normalized_text,
        raw_text=raw_text,
        normalizer_version=NORMALIZER_VERSION,
    )

    if elapsed_ms > 50 and len(raw_text) < 20000:
        import warnings
        warnings.warn(
            f"normalize_text: {len(raw_text)} chars took {elapsed_ms:.1f}ms "
            f"(expected <50ms for <20KB)",
            stacklevel=2,
        )

    return (normalized_text, result)


def get_normalizer_version() -> str:
    """获取当前规范化器版本号。"""
    return NORMALIZER_VERSION


def is_normalized(text: str) -> bool:
    """快速检查文本是否已经是规范化形式。"""
    if not text:
        return True

    for ch in text:
        if ch == _FULLWIDTH_SPACE:
            return False
        if unicodedata.normalize("NFKC", ch) != ch:
            return False
        if ch.isalpha() and ch.isascii() and ch != ch.lower():
            return False

    if _WHITESPACE_RUN.search(text):
        return False

    if _LINE_TRIM.search(text):
        return False

    return True
