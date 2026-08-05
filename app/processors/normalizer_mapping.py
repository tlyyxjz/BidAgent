"""W2-02 双坐标映射表。

从 normalizer.py 拆分而来，承载 OffsetMapping 数据结构与坐标互转方法。

对应总规划 v4.1 第四章 4.10 偏移量映射原则 + 第六章 6.1「建立原始文本与规范化文本字符映射」。

双坐标映射：
- normalized_index → clean_raw_text_index（正向查询）
- clean_raw_text_index → normalized_index（反向查询）
- 处理全角转半角后字符数改变（如全角"１"→半角"1"，长度不变；
  但连续空白压缩后会减少字符数）

工程约束：
- 映射表必须可序列化和反序列化（to_dict / from_dict）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

from app.processors.normalizer_constants import NORMALIZER_VERSION


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
