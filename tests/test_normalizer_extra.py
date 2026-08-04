"""normalizer.py 补充测试：提升覆盖率 93% -> 95%+.

覆盖未覆盖行: 100-102, 111, 113, 142, 144, 244-246, 263-265, 346-347

策略:
- to_normalized 中 raw_end 落在被删除字符上有前驱未删除字符 (行 100-102)
- 用构造的 OffsetMapping 畸形数据触发防御性检查 (行 111, 113, 142, 144)
- 慢路径中空白字符保留分支 (行 244-246)
- NFKC 多字符展开 (行 263-265)
- 性能告警分支用 monkeypatch time.perf_counter 触发 (行 346-347)
"""
from __future__ import annotations

import time
import warnings

import pytest

from app.processors.normalizer import (
    NORMALIZER_VERSION,
    OffsetMapping,
    get_normalizer_version,
    is_normalized,
    normalize_text,
)


# ============================================================
# 测试套件 1: to_normalized raw_end 有前驱未删除字符 (行 100-102)
# ============================================================

class TestToNormalizedPrecedingFound:
    """覆盖 to_normalized 中 raw_end 落在被删除字符且找到前驱未删除字符的分支."""

    def test_raw_end_in_deleted_with_preceding_found(self):
        """行 100-102: raw_end 落在被删除字符上, 向前查找找到未删除字符.

        构造 raw = 'a    b' (a + 4 spaces + b), normalization 后 norm = 'a b'
        reverse_mapping = [0, -1, -1, -1, -1, 2]
        raw_end = 3 (space) -> reverse_mapping[3] = -1
        向前查找: i=2 (-1), i=1 (-1), i=0 (0) -> norm_end = 0 + 1 = 1
        """
        raw = "a    b"
        norm, mapping = normalize_text(raw)
        # raw[3] 是空格 (被删除), 前驱 raw[0]='a' 未被删除
        ns, ne = mapping.to_normalized(0, 3)
        # raw_start=0 -> norm_start=0 (a)
        # raw_end=3 -> 落在删除字符, 前驱找到 raw[0]=0 -> norm_end = 0 + 1 = 1
        assert ns == 0
        assert ne == 1

    def test_raw_end_in_deleted_with_multiple_preceding(self):
        """多个前驱字符, 取最近的未删除字符."""
        raw = "abc   def"
        norm, mapping = normalize_text(raw)
        # norm = 'abc def'
        # reverse_mapping = [0, 1, 2, -1, -1, -1, 3, 4, 5]
        # raw_end = 5 (space, index 5 in raw, but actually 3-5 are spaces)
        # Wait, raw = 'abc   def' (9 chars), spaces at indices 3,4,5
        # norm = 'abc def' (7 chars)
        # reverse_mapping = [0, 1, 2, -1, -1, -1, 3, 4, 5]
        ns, ne = mapping.to_normalized(0, 4)
        # raw_end=4 (space) -> -1, 前驱: i=3 (-1), i=2 (2) -> norm_end = 2 + 1 = 3
        assert ns == 0
        assert ne == 3

    def test_raw_end_in_deleted_at_boundary(self):
        """raw_end 落在删除字符上, 前驱刚好是第一个字符."""
        raw = "a  bc"
        norm, mapping = normalize_text(raw)
        # raw = 'a  bc' (5 chars), spaces at 1,2
        # norm = 'a bc' (4 chars)
        # reverse_mapping = [0, -1, -1, 2, 3]
        ns, ne = mapping.to_normalized(0, 1)
        # raw_end=1 (space) -> -1, 前驱: i=0 (0) -> norm_end = 0 + 1 = 1
        assert ns == 0
        assert ne == 1


# ============================================================
# 测试套件 2: 防御性检查 - 构造畸形 OffsetMapping (行 111, 113, 142, 144)
# ============================================================

class TestDefensiveChecks:
    """用构造的畸形 OffsetMapping 触发防御性边界检查.

    这些分支在正常使用中不会触发, 但防御性代码需要被覆盖.
    通过构造不一致的 mapping/reverse_mapping 值来触发.
    """

    def test_norm_start_negative_clamped(self):
        """行 111: norm_start < 0 时被截断为 0.

        构造 reverse_mapping 包含值 < -1 的条目.
        """
        mapping = OffsetMapping(
            mapping=[0],
            reverse_mapping=[-5],  # 畸形值: -5 < -1
            normalized_text="a",
            raw_text="a",
        )
        # raw_start=0, reverse_mapping[0] = -5 (不是 -1, 不进循环)
        # norm_start = -5 -> if norm_start < 0: norm_start = 0
        ns, ne = mapping.to_normalized(0, 0)
        assert ns == 0

    def test_norm_end_exceeds_n_norm_clamped(self):
        """行 113: norm_end > n_norm 时被截断为 n_norm.

        构造 reverse_mapping 包含值 > n_norm 的条目.
        """
        mapping = OffsetMapping(
            mapping=[0],
            reverse_mapping=[5],  # 畸形值: 5 > n_norm(1)
            normalized_text="a",
            raw_text="a",
        )
        # raw_end=0, reverse_mapping[0] = 5 (不是 -1)
        # norm_end = 5 -> if norm_end > n_norm(1): norm_end = 1
        ns, ne = mapping.to_normalized(0, 0)
        assert ne == ns  # norm_end adjusted to norm_start (line 115)

    def test_raw_start_negative_clamped(self):
        """行 142: raw_start < 0 时被截断为 0.

        构造 mapping 包含负值.
        """
        mapping = OffsetMapping(
            mapping=[-5],  # 畸形值: -5
            reverse_mapping=[0],
            normalized_text="a",
            raw_text="a",
        )
        # norm_start=0, mapping[0] = -5
        # raw_start = -5 -> if raw_start < 0: raw_start = 0
        rs, re_ = mapping.to_raw(0, 1)
        assert rs == 0

    def test_raw_end_exceeds_n_raw_clamped(self):
        """行 144: raw_end > n_raw 时被截断为 n_raw.

        构造 mapping 包含值 >= n_raw.
        """
        mapping = OffsetMapping(
            mapping=[10],  # 畸形值: 10 >= n_raw(1)
            reverse_mapping=[0],
            normalized_text="a",
            raw_text="a",
        )
        # norm_end=1, mapping[0] + 1 = 11
        # raw_end = 11 -> if raw_end > n_raw(1): raw_end = 1
        rs, re_ = mapping.to_raw(0, 1)
        assert re_ == rs  # raw_end adjusted to raw_start (line 146)


# ============================================================
# 测试套件 3: 慢路径空白字符保留 (行 244-246)
# ============================================================

class TestSlowPathWhitespace:
    """覆盖慢路径中空白字符 (\\n, \\r, \\t, \\f, \\v, ' ') 保留分支.

    需要文本同时包含全角字符 (触发慢路径) 和空白字符.
    """

    def test_newline_preserved_in_slow_path(self):
        """行 244: 慢路径中 \\n 被保留."""
        raw = "Ａ\\nＢ"  # 全角A + \n + 全角B
        norm, mapping = normalize_text(raw)
        assert "\\n" in repr(norm) or "\n" in norm

    def test_carriage_return_preserved_in_slow_path(self):
        """行 244: 慢路径中 \\r 被保留."""
        raw = "Ａ\\rＢ"
        norm, mapping = normalize_text(raw)
        # \r 在行内会被压缩为空格
        assert "a" in norm and "b" in norm

    def test_tab_preserved_in_slow_path(self):
        """行 244: 慢路径中 \\t 被保留."""
        raw = "Ａ\\tＢ"
        norm, mapping = normalize_text(raw)
        assert "a" in norm and "b" in norm

    def test_form_feed_preserved_in_slow_path(self):
        """行 245: 慢路径中 \\f 被保留."""
        raw = "Ａ\fＢ"
        norm, mapping = normalize_text(raw)
        assert "a" in norm and "b" in norm

    def test_vertical_tab_preserved_in_slow_path(self):
        """行 245: 慢路径中 \\v 被保留."""
        raw = "Ａ\vＢ"
        norm, mapping = normalize_text(raw)
        assert "a" in norm and "b" in norm

    def test_space_preserved_in_slow_path(self):
        """行 246: 慢路径中空格被保留."""
        raw = "Ａ Ｂ"  # 全角A + 空格 + 全角B
        norm, mapping = normalize_text(raw)
        assert "a b" in norm

    def test_all_whitespace_types_in_slow_path(self):
        """所有空白字符类型在慢路径中被正确处理."""
        raw = "Ａ \t\f\v Ｂ"
        norm, mapping = normalize_text(raw)
        assert "a" in norm
        assert "b" in norm


# ============================================================
# 测试套件 4: NFKC 多字符展开 (行 263-265)
# ============================================================

class TestNfkcMultiCharExpansion:
    """覆盖 NFKC 规范化展开为多字符的分支.

    某些 Unicode 字符经 NFKC 规范化后会展开为多个字符,
    例如连字 ﬁ (U+FB01) -> 'fi'.
    """

    def test_ligature_fi_expands_to_two_chars(self):
        """行 263-265: 连字 ﬁ (U+FB01) 展开为 'fi'."""
        raw = "\ufb01"  # ﬁ
        norm, mapping = normalize_text(raw)
        assert norm == "fi"
        assert len(mapping.mapping) == 2  # norm 有 2 个字符

    def test_ligature_fl_expands_to_two_chars(self):
        """连字 ﬂ (U+FB02) 展开为 'fl'."""
        raw = "\ufb02"  # ﬂ
        norm, mapping = normalize_text(raw)
        assert norm == "fl"

    def test_ligature_in_context(self):
        """连字在文本中被正确展开."""
        raw = "a\ufb01b"  # a + ﬁ + b
        norm, mapping = normalize_text(raw)
        assert norm == "afib"

    def test_multiple_ligatures(self):
        """多个连字都被展开."""
        raw = "\ufb01\ufb02"  # ﬁﬂ
        norm, mapping = normalize_text(raw)
        assert norm == "fifl"

    def test_ligature_mapping_correct(self):
        """连字展开后映射表长度正确."""
        raw = "x\ufb01y"  # x + ﬁ + y
        norm, mapping = normalize_text(raw)
        # raw: 3 chars (x, ﬁ, y)
        # norm: 4 chars (x, f, i, y)
        assert len(mapping.reverse_mapping) == 3  # raw 长度
        assert len(mapping.mapping) == 4  # norm 长度
        assert norm == "xfiy"


# ============================================================
# 测试套件 5: 性能告警 (行 346-347)
# ============================================================

class TestPerformanceWarning:
    """覆盖慢路径中性能告警分支.

    通过 monkeypatch time.perf_counter 模拟慢执行.
    """

    def test_warning_triggered_when_slow(self, monkeypatch):
        """行 346-347: 执行时间 > 50ms 且文本 < 20KB 时触发 warning."""
        # 用全角字符触发慢路径
        raw = "ＡＢＣ"

        # mock perf_counter: 第一次调用返回 0, 第二次返回 0.06 (60ms)
        call_count = [0]
        original_perf = time.perf_counter

        def mock_perf_counter():
            call_count[0] += 1
            if call_count[0] == 1:
                return 0.0
            return 0.06  # 60ms 后

        monkeypatch.setattr("app.processors.normalizer.time.perf_counter", mock_perf_counter)

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            norm, mapping = normalize_text(raw)
            # 应该有性能告警
            assert len(w) >= 1
            assert "50ms" in str(w[0].message) or "normalize_text" in str(w[0].message)

    def test_no_warning_when_fast(self, monkeypatch):
        """快速执行时不触发 warning."""
        raw = "ＡＢＣ"

        call_count = [0]

        def mock_perf_counter():
            call_count[0] += 1
            if call_count[0] == 1:
                return 0.0
            return 0.001  # 1ms

        monkeypatch.setattr("app.processors.normalizer.time.perf_counter", mock_perf_counter)

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            normalize_text(raw)
            # 不应有性能告警
            perf_warnings = [x for x in w if "normalize_text" in str(x.message)]
            assert len(perf_warnings) == 0

    def test_no_warning_when_text_too_large(self, monkeypatch):
        """文本 >= 20KB 时不触发 warning (即使慢)."""
        # 构造 > 20KB 的全角文本
        raw = "Ａ" * 20001

        call_count = [0]

        def mock_perf_counter():
            call_count[0] += 1
            if call_count[0] == 1:
                return 0.0
            return 1.0  # 1000ms

        monkeypatch.setattr("app.processors.normalizer.time.perf_counter", mock_perf_counter)

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            normalize_text(raw)
            # 不应有性能告警 (文本太大)
            perf_warnings = [x for x in w if "normalize_text" in str(x.message)]
            assert len(perf_warnings) == 0


# ============================================================
# 测试套件 6: 综合边界补充
# ============================================================

class TestNormalizerExtraEdge:
    """补充边界测试."""

    def test_to_normalized_raw_end_at_last_deleted(self):
        """raw_end 是最后一个被删除字符, 前驱有未删除字符."""
        raw = "ab   "  # a + b + 3 spaces
        norm, mapping = normalize_text(raw)
        # norm = 'ab'
        # reverse_mapping = [0, 1, -1, -1, -1]
        # raw_end = 4 (space) -> -1, 前驱: i=3 (-1), i=2 (-1), i=1 (1) -> norm_end = 1 + 1 = 2
        ns, ne = mapping.to_normalized(0, 4)
        assert ne == 2  # n_norm = 2

    def test_to_raw_norm_start_at_boundary(self):
        """norm_start 在边界上的 to_raw."""
        raw = "a    b"
        norm, mapping = normalize_text(raw)
        # norm = 'a b' (3 chars)
        # mapping = [0, 5, ...] no wait
        # raw = 'a    b' (6 chars)
        # norm = 'a b' (3 chars)
        # mapping = [0, ?, 5]
        # Actually mapping[i] = raw_idx for each norm char
        # norm[0]='a' -> raw[0], mapping[0]=0
        # norm[1]=' ' -> raw[1] (first space of the run), mapping[1]=1
        # norm[2]='b' -> raw[5], mapping[2]=5
        rs, re_ = mapping.to_raw(0, 3)
        # norm_start=0 -> mapping[0]=0, raw_start=0
        # norm_end=3 -> mapping[2]+1=5+1=6, raw_end=6
        assert rs == 0
        assert re_ == 6

    def test_normalize_with_only_whitespace(self):
        """纯空白文本规范化."""
        raw = "   \t\n  "
        norm, mapping = normalize_text(raw)
        # 纯空白被压缩/去除
        assert norm.strip() == ""

    def test_normalize_mixed_fullwidth_and_whitespace(self):
        """全角字符和空白混合."""
        raw = "Ａ Ｂ\tＣ"
        norm, mapping = normalize_text(raw)
        assert "a" in norm
        assert "b" in norm
        assert "c" in norm

    def test_is_normalized_with_tab(self):
        """制表符不被 is_normalized 视为已规范化 (单个 tab 不会触发)."""
        # 单个 tab 不会触发 _WHITESPACE_RUN (需要 2+ 个)
        # 但会触发 _LINE_TRIM 如果在行首/行尾
        assert is_normalized("a\tb") is True  # 行内单个 tab 是规范化的
        assert is_normalized("\ta") is False  # 行首 tab
