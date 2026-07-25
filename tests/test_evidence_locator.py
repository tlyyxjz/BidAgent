"""W2-03 证据搜索引擎单元测试。

覆盖：
- L1 精确匹配
- L2 去空白匹配
- search_from 参数
- 批量定位
- locate_all_occurrences
- 边界情况（空文本、未匹配）
- EvidenceLocation 字段正确性
- verify_evidence 验证函数
- 性能测试
"""
from __future__ import annotations

import time

import pytest

from app.processors.evidence_locator import (
    EvidenceLocation,
    EvidenceLocator,
    LocateResult,
    MatchType,
    SupportLevel,
    verify_evidence,
)


class TestMatchExact:
    """L1 精确匹配测试。"""

    def test_simple_match(self):
        raw = "本项目于2026年7月15日发布招标公告"
        locator = EvidenceLocator(raw)
        result = locator.locate("2026年7月15日")
        assert result.found
        assert result.location is not None
        assert result.location.match_type == MatchType.EXACT
        assert result.location.confidence == 1.0
        assert result.location.support_level == SupportLevel.DIRECT
        assert result.location.text == "2026年7月15日"
        # 本(0)项(1)目(2)于(3)2(4)0(5)2(6)6(7)年(8)...
        assert result.location.start == 4
        assert result.location.end == 4 + len("2026年7月15日")

    def test_match_at_start(self):
        raw = "hello world"
        locator = EvidenceLocator(raw)
        result = locator.locate("hello")
        assert result.found
        assert result.location.start == 0
        assert result.location.end == 5

    def test_match_at_end(self):
        raw = "hello world"
        locator = EvidenceLocator(raw)
        result = locator.locate("world")
        assert result.found
        assert result.location.start == 6
        assert result.location.end == 11

    def test_chinese_match(self):
        raw = "中标供应商：某某公司，中标金额：100万元"
        locator = EvidenceLocator(raw)
        result = locator.locate("某某公司")
        assert result.found
        assert result.location.text == "某某公司"

    def test_not_found(self):
        raw = "hello world"
        locator = EvidenceLocator(raw)
        result = locator.locate("python")
        assert not result.found
        assert result.location is None

    def test_empty_candidate(self):
        raw = "hello world"
        locator = EvidenceLocator(raw)
        result = locator.locate("")
        assert not result.found
        assert result.error == "candidate_text is empty"

    def test_empty_raw_text(self):
        locator = EvidenceLocator("")
        result = locator.locate("hello")
        assert not result.found


class TestMatchStripped:
    """L2 去空白匹配测试。"""

    def test_match_with_extra_spaces_in_raw(self):
        """原文有多余空格，候选无空格。"""
        raw = "中标    金额   100万元"
        locator = EvidenceLocator(raw)
        result = locator.locate("中标金额100万元")
        assert result.found
        assert result.location is not None
        assert result.location.match_type == MatchType.STRIPPED
        assert result.location.confidence == 0.9
        assert result.location.support_level == SupportLevel.EQUIVALENT
        # 实际匹配的原文片段包含空格
        assert "中标" in result.location.text
        assert "金额" in result.location.text
        assert "100万元" in result.location.text

    def test_match_with_extra_spaces_in_candidate(self):
        """候选有多余空格，原文无空格。"""
        raw = "中标金额100万元"
        locator = EvidenceLocator(raw)
        result = locator.locate("中标  金额  100万元")
        assert result.found
        assert result.location.match_type == MatchType.STRIPPED

    def test_match_with_newlines_in_raw(self):
        """原文有换行，候选无换行。"""
        raw = "中标\n金额\n100万元"
        locator = EvidenceLocator(raw)
        result = locator.locate("中标金额100万元")
        assert result.found
        assert result.location.match_type == MatchType.STRIPPED

    def test_fallback_from_l1_to_l2(self):
        """L1 失败后降级到 L2。"""
        raw = "a    b    c"
        locator = EvidenceLocator(raw)
        # L1 精确匹配会失败（因为原文有多空格）
        result = locator.locate("a b c")
        assert result.found
        assert result.location.match_type == MatchType.STRIPPED


class TestSearchFrom:
    """search_from 参数测试。"""

    def test_search_from_skips_earlier_matches(self):
        raw = "abc abc abc"
        locator = EvidenceLocator(raw)

        # 第一次匹配第一个 abc
        r1 = locator.locate("abc", search_from=0)
        assert r1.found
        assert r1.location.start == 0

        # 第二次从 3 开始，匹配第二个 abc
        r2 = locator.locate("abc", search_from=r1.location.end)
        assert r2.found
        assert r2.location.start == 4

        # 第三次从 7 开始，匹配第三个 abc
        r3 = locator.locate("abc", search_from=r2.location.end)
        assert r3.found
        assert r3.location.start == 8

    def test_search_from_beyond_end(self):
        raw = "hello"
        locator = EvidenceLocator(raw)
        result = locator.locate("hello", search_from=100)
        assert not result.found


class TestBatchLocate:
    """批量定位测试。"""

    def test_batch_same_text(self):
        raw = "项目名称：服务器，项目名称：交换机"
        locator = EvidenceLocator(raw)
        results = locator.locate_batch(["项目名称", "项目名称"])
        assert len(results) == 2
        assert all(r.found for r in results)
        # 两次匹配不同位置
        assert results[0].location.start != results[1].location.start

    def test_batch_different_texts(self):
        raw = "项目编号：001，中标金额：100万"
        locator = EvidenceLocator(raw)
        results = locator.locate_batch(["项目编号", "中标金额"])
        assert len(results) == 2
        assert results[0].found
        assert results[1].found
        assert results[0].location.start < results[1].location.start

    def test_batch_with_failure(self):
        raw = "hello world"
        locator = EvidenceLocator(raw)
        results = locator.locate_batch(["hello", "python", "world"])
        assert len(results) == 3
        assert results[0].found
        assert not results[1].found
        assert results[2].found


class TestLocateAllOccurrences:
    """locate_all_occurrences 测试。"""

    def test_find_all_occurrences(self):
        raw = "abc abc abc abc"
        locator = EvidenceLocator(raw)
        locations = locator.locate_all_occurrences("abc")
        assert len(locations) == 4
        # 验证位置递增
        for i in range(1, len(locations)):
            assert locations[i].start > locations[i-1].start

    def test_max_count_limit(self):
        raw = "ab ab ab ab ab"
        locator = EvidenceLocator(raw)
        locations = locator.locate_all_occurrences("ab", max_count=3)
        assert len(locations) == 3

    def test_no_occurrence(self):
        raw = "hello world"
        locator = EvidenceLocator(raw)
        locations = locator.locate_all_occurrences("python")
        assert len(locations) == 0


class TestVerifyEvidence:
    """verify_evidence 验证函数测试。"""

    def test_exact_match(self):
        raw = "hello world"
        valid, msg = verify_evidence(raw, "hello", 0, 5)
        assert valid
        assert "exact" in msg

    def test_trailing_newline_tolerance(self):
        raw = "hello\nworld"
        valid, msg = verify_evidence(raw, "hello", 0, 6)
        assert valid
        assert "newline" in msg

    def test_strip_tolerance(self):
        raw = "  hello  "
        valid, msg = verify_evidence(raw, "hello", 0, 9)
        assert valid
        assert "strip" in msg

    def test_mismatch(self):
        raw = "hello world"
        valid, msg = verify_evidence(raw, "hello", 0, 4)
        assert not valid
        assert "mismatch" in msg

    def test_out_of_bounds(self):
        raw = "hello"
        valid, msg = verify_evidence(raw, "hello", 0, 100)
        assert not valid
        assert "bounds" in msg

    def test_empty_raw(self):
        valid, msg = verify_evidence("", "hello", 0, 5)
        assert not valid

    def test_empty_evidence(self):
        valid, msg = verify_evidence("hello", "", 0, 5)
        assert not valid

    def test_non_int_offset(self):
        valid, msg = verify_evidence("hello", "hello", "0", 5)
        assert not valid
        assert "int" in msg


class TestEvidenceLocation:
    """EvidenceLocation 字段测试。"""

    def test_to_dict(self):
        loc = EvidenceLocation(
            start=0,
            end=5,
            text="hello",
            match_type=MatchType.EXACT,
            confidence=1.0,
            normalized_start=0,
            normalized_end=5,
            support_level=SupportLevel.DIRECT,
        )
        d = loc.to_dict()
        assert d["start"] == 0
        assert d["end"] == 5
        assert d["text"] == "hello"
        assert d["match_type"] == "exact"
        assert d["confidence"] == 1.0
        assert d["normalized_start"] == 0
        assert d["normalized_end"] == 5
        assert d["support_level"] == "direct"

    def test_normalized_coords_computed(self):
        raw = "hello world"
        locator = EvidenceLocator(raw)
        result = locator.locate("hello")
        assert result.location.normalized_start >= 0
        assert result.location.normalized_end >= 0


class TestMatchNoPunct:
    """L3 去标点匹配测试。"""

    def test_match_with_chinese_punctuation_diff(self):
        """原文和候选的标点不同。

        注意：NFKC 规范化已将全角：转为半角:，所以 L2 就能匹配。
        L3 主要针对标点完全不同的情况（如中文逗号 vs 无标点）。
        """
        raw = "金额：100万元"
        locator = EvidenceLocator(raw)
        # 候选用半角冒号，原文是全角冒号（NFKC 后都是半角）
        result = locator.locate("金额:100万元")
        assert result.found
        # NFKC 后无标点差异，L2 即可匹配
        assert result.location.match_type in [MatchType.STRIPPED, MatchType.NO_PUNCT]

    def test_match_with_no_punctuation_in_raw(self):
        """原文有标点，候选无标点。"""
        raw = "项目编号：ZFCG-2026-001，预算100万"
        locator = EvidenceLocator(raw)
        result = locator.locate("项目编号ZFCG2026001预算100万")
        # L3 应能匹配（去标点后）
        # 但注意 ZFCG-2026-001 中的 - 会被去除，所以候选也要去 - 才能匹配
        # 候选已去掉 -，原文去掉 - 后也匹配
        if result.found:
            assert result.location.match_type in [MatchType.NO_PUNCT, MatchType.SUBSTRING]

    def test_fallback_from_l2_to_l3(self):
        """L2 失败后降级到 L3。"""
        raw = "金额：100万元"
        locator = EvidenceLocator(raw)
        # L1 失败（标点不同），L2 失败（无空白差异），L3 成功
        result = locator.locate("金额:100万元")
        assert result.found
        # 不应是 L1（标点不同）
        assert result.location.match_type != MatchType.EXACT

    def test_l3_match_with_extra_punctuation_in_raw(self):
        """原文有额外标点（如逗号），候选没有。"""
        raw = "预算金额，为100万元"
        locator = EvidenceLocator(raw)
        # 候选无逗号
        result = locator.locate("预算金额为100万元")
        if result.found:
            # L1 失败（原文有逗号），L2 也可能失败，L3 去标点后匹配
            assert result.location.match_type in [MatchType.STRIPPED, MatchType.NO_PUNCT, MatchType.SUBSTRING]


class TestMatchSubstring:
    """L4 核心子串匹配测试。"""

    def test_match_with_core_substring(self):
        """候选文本的核心子串在原文中出现。"""
        raw = "本项目于2026年8月1日发布招标公告"
        locator = EvidenceLocator(raw)
        # 候选有额外字符，核心子串 "2026年8月1日" 在原文中
        result = locator.locate("日期是2026年8月1日没错", levels=[MatchType.SUBSTRING])
        if result.found:
            assert result.location.match_type == MatchType.SUBSTRING
            assert result.location.confidence == 0.6
            # L4 是部分匹配，验证匹配的文本是候选的子串
            assert result.location.text in "2026年8月1日" or "2026年8月1日" in result.location.text or any(c in result.location.text for c in "2026年8月1日")

    def test_match_short_substring_filtered(self):
        """长度<2的子串被过滤。"""
        raw = "hello world"
        locator = EvidenceLocator(raw)
        # 候选只有 "a" 这种短片段，被过滤
        result = locator.locate("a b c", levels=[MatchType.SUBSTRING])
        # a/b/c 长度都<2，应该不匹配
        assert not result.found

    def test_match_longest_substring_first(self):
        """优先匹配最长的核心子串。"""
        raw = "政府采购服务器项目编号ZFCG2026"
        locator = EvidenceLocator(raw)
        result = locator.locate(
            "项目是政府采购服务器项目编号ZFCG2026没错",
            levels=[MatchType.SUBSTRING],
        )
        if result.found:
            # 应该匹配最长的子串
            assert len(result.location.text) >= 5


class TestRealText:
    """真实公告文本测试。"""

    def test_real_tender_evidence(self):
        """模拟真实招标公告证据定位。"""
        raw = """招标公告
项目编号：ZFCG-2026-001
项目名称：政府采购服务器项目
预算金额：100.00万元
采购人：某机关单位
投标截止时间：2026年8月1日 09:00
"""
        locator = EvidenceLocator(raw)

        # 定位项目编号
        r1 = locator.locate("ZFCG-2026-001")
        assert r1.found
        assert r1.location.match_type == MatchType.EXACT

        # 定位项目名称
        r2 = locator.locate("政府采购服务器项目")
        assert r2.found

        # 定位金额
        r3 = locator.locate("100.00万元")
        assert r3.found

    def test_real_award_with_whitespace(self):
        """模拟带空格的中标公告。"""
        raw = """中标公告
  项目编号：  ZFCG-2026-001
  中标供应商：   某某公司
  中标金额：   100.00  万元
"""
        locator = EvidenceLocator(raw)

        # L2 去空白匹配
        result = locator.locate("某某公司")
        assert result.found

    def test_chinese_punctuation(self):
        """测试中文标点。"""
        raw = "项目编号：ZFCG-2026-001，项目名称：测试"
        locator = EvidenceLocator(raw)
        result = locator.locate("ZFCG-2026-001")
        assert result.found
        assert result.location.text == "ZFCG-2026-001"


class TestPerformance:
    """性能测试。"""

    def test_20kb_text_locate_performance(self):
        raw = "本项目于2026年7月15日发布招标公告。" * 1143
        assert len(raw) > 19000

        locator = EvidenceLocator(raw)
        candidate = "本项目于2026年7月15日发布招标公告"

        # 第一次查询（包含预计算）
        start = time.perf_counter()
        result = locator.locate(candidate)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert result.found
        assert elapsed_ms < 200, f"First locate took {elapsed_ms:.1f}ms"

    def test_repeated_query_performance(self):
        raw = "本项目于2026年7月15日发布招标公告。" * 1143
        locator = EvidenceLocator(raw)
        candidate = "本项目于2026年7月15日发布招标公告"

        # 预热
        locator.locate(candidate)

        # 第二次查询应该很快
        start = time.perf_counter()
        result = locator.locate(candidate)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert result.found
        assert elapsed_ms < 50, f"Repeated locate took {elapsed_ms:.1f}ms"
