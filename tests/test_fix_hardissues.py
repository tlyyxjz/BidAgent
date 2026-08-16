# -*- coding: utf-8 -*-
"""硬伤②③修复的回归测试。

③ 修法 X：金额类候选禁止 L4 子串猜测——原文多金额时锚错风险，
   宁可标"无依据"（not_found），不硬猜。
② 自证：主题关键词只加 query 中实际出现的词，不扩散同组近义词。
"""
import pytest

from app.processors.evidence_locator import EvidenceLocator
from app.processors.evidence_locator._models import MatchType


RAW_MULTI_AMOUNT = (
    "本项目预算金额为100.00万元，最高限价95万元，"
    "投标保证金2.5万元，中标金额为93.8万元。"
)


class TestAmountGuard:
    """③：金额候选不走 L4 猜测。"""

    def test_amount_candidate_refused_at_l4(self):
        """候选金额与原文任何金额都不整串相等时，L4 必须拒绝（不得锚到别的金额）。"""
        locator = EvidenceLocator(RAW_MULTI_AMOUNT)
        # 331 万元在原文中不存在；旧逻辑会用核心子串 "331"/"331万元" 去猜
        result = locator.locate("331万元", levels=[MatchType.SUBSTRING])
        assert not result.found

    def test_amount_candidate_full_chain_not_found(self):
        """完整降级链 L1→L5：猜不出就标无依据。"""
        locator = EvidenceLocator(RAW_MULTI_AMOUNT)
        result = locator.locate("中标金额为331万元")
        assert not result.found
        assert result.location is None or result.location.match_type == MatchType.NOT_FOUND

    def test_amount_exact_match_still_works(self):
        """L1 精确匹配不受影响：原文真有的金额照常定位。"""
        locator = EvidenceLocator(RAW_MULTI_AMOUNT)
        result = locator.locate("93.8万元")
        assert result.found
        assert result.location.match_type == MatchType.EXACT
        assert RAW_MULTI_AMOUNT[result.location.start:result.location.end] == "93.8万元"

    def test_amount_no_punct_still_works(self):
        """L3 去标点匹配不受影响。"""
        raw = "预算金额，为100万元"
        locator = EvidenceLocator(raw)
        result = locator.locate("预算金额为100万元")
        assert result.found
        assert result.location.match_type != MatchType.SUBSTRING

    def test_non_amount_l4_unaffected(self):
        """非金额候选的 L4 能力保留（回归防护）。"""
        raw = "本项目于2026年8月1日发布招标公告"
        locator = EvidenceLocator(raw)
        result = locator.locate("日期是2026年8月1日没错", levels=[MatchType.SUBSTRING])
        assert result.found
        assert result.location.match_type == MatchType.SUBSTRING


class TestTopicKeywordNoSynonymSpread:
    """②：只加 query 中出现的词本身，不扩散同组近义词。"""

    def test_only_matched_word_added(self):
        from app.llm.parser import _fallback_keyword_parse

        parsed = _fallback_keyword_parse("帮我监控充电站相关项目")
        assert "充电站" in parsed.keywords
        assert "充电桩" not in parsed.keywords
        assert "充电设备" not in parsed.keywords

    def test_gov_procurement_no_spread(self):
        from app.llm.parser import _fallback_keyword_parse

        parsed = _fallback_keyword_parse("办公设备采购项目")
        assert "采购" in parsed.keywords
        assert "政府采购" not in parsed.keywords
