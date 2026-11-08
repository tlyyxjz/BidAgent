"""`app/api/evidence_span.py` 单元测试。

锁定「证据偏移解析」的契约——这是前后端对齐的唯一真相点：
接口返回的 ``start/end`` 必须满足 ``raw[start:end] == text`` 且 ``end-start == len(text)``；
偏移不可信时**就近重定位**、多处命中时**宁可返回不可定位也不猜**。
"""
from __future__ import annotations

from app.api.evidence_span import resolve_evidence_span, summarize


class TestTrustworthyOffset:
    def test_exact_slice_is_kept_untouched(self):
        raw = "xxABCDEFyy"
        s, e, slice_ok, relocated, locatable = resolve_evidence_span(raw, 2, 8, "ABCDEF")
        assert (s, e) == (2, 8)
        assert slice_ok is True and relocated is False and locatable is True

    def test_repeated_text_with_valid_offset_is_not_disambiguated(self):
        """库内偏移本身正确时，即使文本在文中多次出现也直接采信（无需消歧）。"""
        raw = "abcXabc"
        s, e, slice_ok, _rel, locatable = resolve_evidence_span(raw, 0, 3, "abc")
        assert (s, e) == (0, 3)
        assert slice_ok is True and locatable is True


class TestRelocation:
    def test_out_of_range_end_is_corrected_to_exclusive_bound(self):
        """排他上界恒等于 start+len(text)：越界的 end 要被纠正，不能原样透传。"""
        raw = "项目编号：BJGPC-2026-0042"  # 20 字符
        s, e, slice_ok, relocated, locatable = resolve_evidence_span(
            raw, 5, 21, "BJGPC-2026-0042"
        )
        assert raw[s:e] == "BJGPC-2026-0042"
        assert e - s == len("BJGPC-2026-0042")
        assert slice_ok is False and relocated is True and locatable is True

    def test_nearby_match_preferred_over_global_first(self):
        """文本在文中出现多次时，优先取靠近期望位置的那一处。"""
        filler = "。" * 600
        raw = "目标" + filler + "目标"  # 第一处在 0，第二处在 602 附近
        s, e, _ok, relocated, locatable = resolve_evidence_span(raw, 599, 601, "目标")
        assert locatable is True and relocated is True
        assert (s, e) == (602, 604)  # 靠近期望位置（~600）而非首次出现（0）

    def test_unique_global_match_is_accepted(self):
        raw = "xxabcxx"
        s, e, _ok, relocated, locatable = resolve_evidence_span(raw, 0, 3, "abc")
        assert (s, e) == (2, 5)
        assert relocated is True and locatable is True


class TestRefuseToGuess:
    def test_ambiguous_text_is_not_guessed(self):
        """偏移失效 + 文本多处命中 ⇒ 不可定位（宁可不给，也不猜错位置）。"""
        raw = "abcXabc"
        _s, _e, _ok, relocated, locatable = resolve_evidence_span(raw, 100, 103, "abc")
        assert locatable is False
        assert relocated is False

    def test_text_absent_from_raw_is_orphan(self):
        raw = "完全无关的正文"
        _s, _e, _ok, _rel, locatable = resolve_evidence_span(raw, 0, 3, "北京大学第三医院")
        assert locatable is False

    def test_empty_text_is_not_locatable(self):
        _s, _e, _ok, _rel, locatable = resolve_evidence_span("abcdef", 0, 3, "")
        assert locatable is False

    def test_empty_raw_is_not_locatable(self):
        _s, _e, _ok, _rel, locatable = resolve_evidence_span("", 0, 3, "abc")
        assert locatable is False


class TestSummarize:
    def test_counts_by_class(self):
        spans = [
            (0, 3, True, False, True),    # ok
            (5, 8, False, True, True),    # relocated
            (0, 0, False, False, False),  # orphan
        ]
        assert summarize(spans) == {"total": 3, "ok": 1, "relocated": 1, "orphan": 1}

    def test_empty(self):
        assert summarize([]) == {"total": 0, "ok": 0, "relocated": 0, "orphan": 0}
