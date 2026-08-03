"""v4.1 §8.1 转载识别 10 项特征测试。

覆盖：
- RepostFeatures 数据类（10 项特征 + match_count + to_dict）
- compute_repost_features 函数（10 项特征计算）
- judge_repost_with_features 函数（转载判定）
- _title_similarity 辅助函数
"""
from __future__ import annotations

import pytest

from app.processors.source_lineage import (
    REPOST_MATCH_THRESHOLD,
    RepostFeatures,
    _title_similarity,
    compute_repost_features,
    judge_repost_with_features,
)


# ========== RepostFeatures 数据类测试 ==========


class TestRepostFeatures:
    """RepostFeatures 数据类测试。"""

    def test_default_all_false(self):
        """默认所有特征 match=False。"""
        f = RepostFeatures()
        assert f.domain_match[0] is False
        assert f.repost_marker[0] is False
        assert f.project_identifier_match[0] is False
        assert f.notice_type_match[0] is False
        assert f.title_similarity[0] is False
        assert f.simhash_similar[0] is False
        assert f.publisher_match[0] is False
        assert f.time_relation[0] is False
        assert f.attachment_link_match[0] is False
        assert f.upstream_source_mention[0] is False

    def test_match_count_zero(self):
        """默认 match_count=0。"""
        assert RepostFeatures().match_count() == 0

    def test_match_count_partial(self):
        """部分特征匹配时 match_count 正确。"""
        f = RepostFeatures(
            domain_match=(True, "domain"),
            project_identifier_match=(True, "id"),
            publisher_match=(True, "pub"),
        )
        assert f.match_count() == 3

    def test_match_count_all(self):
        """全部特征匹配时 match_count=10。"""
        f = RepostFeatures(
            domain_match=(True, ""),
            repost_marker=(True, ""),
            project_identifier_match=(True, ""),
            notice_type_match=(True, ""),
            title_similarity=(True, ""),
            simhash_similar=(True, ""),
            publisher_match=(True, ""),
            time_relation=(True, ""),
            attachment_link_match=(True, ""),
            upstream_source_mention=(True, ""),
        )
        assert f.match_count() == 10

    def test_to_dict_contains_all_fields(self):
        """to_dict 包含 10 项特征 + match_count。"""
        d = RepostFeatures().to_dict()
        assert "domain_match" in d
        assert "repost_marker" in d
        assert "project_identifier_match" in d
        assert "notice_type_match" in d
        assert "title_similarity" in d
        assert "simhash_similar" in d
        assert "publisher_match" in d
        assert "time_relation" in d
        assert "attachment_link_match" in d
        assert "upstream_source_mention" in d
        assert "match_count" in d
        assert len(d) == 11  # 10 项特征 + match_count


# ========== _title_similarity 测试 ==========


class TestTitleSimilarity:
    """标题相似度计算测试。"""

    def test_identical_titles(self):
        """相同标题相似度 1.0。"""
        assert _title_similarity("招标公告", "招标公告") == 1.0

    def test_empty_title(self):
        """空标题相似度 0.0。"""
        assert _title_similarity("", "招标") == 0.0
        assert _title_similarity("招标", "") == 0.0

    def test_similar_titles(self):
        """相似标题相似度高。"""
        sim = _title_similarity("上海市政府采购中心服务器招标公告", "上海市政府采购中心服务器招标公告")
        assert sim >= 0.8

    def test_different_titles(self):
        """不同标题相似度低。"""
        sim = _title_similarity("服务器招标公告", "办公用品采购公告")
        assert sim < 0.5


# ========== compute_repost_features 测试 ==========


class TestComputeRepostFeatures:
    """10 项特征计算测试。"""

    def test_domain_match_same_domain(self):
        """相同域名 → domain_match=True。"""
        f = compute_repost_features(
            "https://www.ccgp.gov.cn/notice/1",
            "内容A",
            "https://www.ccgp.gov.cn/notice/2",
            "内容B",
        )
        assert f.domain_match[0] is True

    def test_domain_match_different_domain(self):
        """不同域名 → domain_match=False。"""
        f = compute_repost_features(
            "https://www.ccgp.gov.cn/notice/1",
            "内容A",
            "https://www.chinabidding.cn/notice/2",
            "内容B",
        )
        assert f.domain_match[0] is False

    def test_repost_marker(self):
        """含转载标记 → repost_marker=True。"""
        f = compute_repost_features(
            "https://a.com/1",
            "本文转载自 ccgp",
            "https://b.com/2",
            "原文内容",
        )
        assert f.repost_marker[0] is True

    def test_project_identifier_match(self):
        """项目编号一致 → project_identifier_match=True。"""
        f = compute_repost_features(
            "https://a.com/1",
            "内容A",
            "https://b.com/2",
            "内容B",
            project_identifier_a="ZFCG-2026-001",
            project_identifier_b="ZFCG-2026-001",
        )
        assert f.project_identifier_match[0] is True

    def test_project_identifier_mismatch(self):
        """项目编号不同 → project_identifier_match=False。"""
        f = compute_repost_features(
            "https://a.com/1",
            "内容A",
            "https://b.com/2",
            "内容B",
            project_identifier_a="ZFCG-2026-001",
            project_identifier_b="ZFCG-2026-002",
        )
        assert f.project_identifier_match[0] is False

    def test_notice_type_match(self):
        """公告类型一致 → notice_type_match=True。"""
        f = compute_repost_features(
            "https://a.com/1",
            "内容A",
            "https://b.com/2",
            "内容B",
            notice_type_a="tender",
            notice_type_b="tender",
        )
        assert f.notice_type_match[0] is True

    def test_title_similarity_high(self):
        """标题高度相似 → title_similarity=True。"""
        f = compute_repost_features(
            "https://a.com/1",
            "内容A",
            "https://b.com/2",
            "内容B",
            title_a="上海市政府采购中心服务器招标公告",
            title_b="上海市政府采购中心服务器招标公告",
        )
        assert f.title_similarity[0] is True

    def test_simhash_similar(self):
        """正文 SimHash 相似 → simhash_similar=True。"""
        # 使用相同内容
        content = "这是一段测试用的公告正文内容，用于验证 SimHash 相似度计算功能是否正常工作。"
        f = compute_repost_features(
            "https://a.com/1",
            content,
            "https://b.com/2",
            content,
        )
        assert f.simhash_similar[0] is True

    def test_publisher_match(self):
        """发布主体一致 → publisher_match=True。"""
        f = compute_repost_features(
            "https://a.com/1",
            "内容A",
            "https://b.com/2",
            "内容B",
            publisher_a="上海市财政局",
            publisher_b="上海市财政局",
        )
        assert f.publisher_match[0] is True

    def test_time_relation_same_day(self):
        """同日发布 → time_relation=True。"""
        f = compute_repost_features(
            "https://a.com/1",
            "内容A",
            "https://b.com/2",
            "内容B",
            publish_time_a="2026-08-01 10:00:00",
            publish_time_b="2026-08-01 14:00:00",
        )
        assert f.time_relation[0] is True

    def test_time_relation_different_day(self):
        """不同日期发布 → time_relation=False。"""
        f = compute_repost_features(
            "https://a.com/1",
            "内容A",
            "https://b.com/2",
            "内容B",
            publish_time_a="2026-08-01 10:00:00",
            publish_time_b="2026-08-02 14:00:00",
        )
        assert f.time_relation[0] is False

    def test_attachment_link_match(self):
        """附件链接一致 → attachment_link_match=True。"""
        f = compute_repost_features(
            "https://a.com/1",
            "内容A",
            "https://b.com/2",
            "内容B",
            attachment_links_a=["https://a.com/file1.pdf", "https://a.com/file2.pdf"],
            attachment_links_b=["https://a.com/file1.pdf"],
        )
        assert f.attachment_link_match[0] is True

    def test_upstream_source_mention(self):
        """含上游来源说明 → upstream_source_mention=True。"""
        f = compute_repost_features(
            "https://a.com/1",
            "本文转自中国政府采购网",
            "https://b.com/2",
            "原文内容",
        )
        assert f.upstream_source_mention[0] is True

    def test_all_features_computed(self):
        """传入所有参数时 10 项特征都计算。"""
        content = "这是一段测试用的公告正文内容，用于验证 SimHash 相似度计算功能是否正常工作。"
        f = compute_repost_features(
            "https://www.ccgp.gov.cn/notice/1",
            content + " 转自 ccgp",
            "https://www.ccgp.gov.cn/notice/2",
            content,
            project_identifier_a="ZFCG-2026-001",
            project_identifier_b="ZFCG-2026-001",
            notice_type_a="tender",
            notice_type_b="tender",
            title_a="上海市政府采购中心服务器招标公告",
            title_b="上海市政府采购中心服务器招标公告",
            publisher_a="上海市财政局",
            publisher_b="上海市财政局",
            publish_time_a="2026-08-01 10:00:00",
            publish_time_b="2026-08-01 14:00:00",
            attachment_links_a=["https://a.com/file.pdf"],
            attachment_links_b=["https://a.com/file.pdf"],
        )
        # 绝大部分特征应匹配
        assert f.match_count() >= 8


# ========== judge_repost_with_features 测试 ==========


class TestJudgeRepostWithFeatures:
    """基于特征的转载判定测试。"""

    def test_same_source_repost(self):
        """同源转载判定。"""
        f = RepostFeatures(
            domain_match=(True, ""),
            repost_marker=(True, ""),
            project_identifier_match=(True, ""),
            simhash_similar=(True, ""),
        )
        judgment, reason = judge_repost_with_features(f)
        assert judgment == "same_source_repost"

    def test_likely_repost(self):
        """可能转载判定（特征匹配但 SimHash 不相似）。"""
        f = RepostFeatures(
            domain_match=(True, ""),
            project_identifier_match=(True, ""),
            notice_type_match=(True, ""),
        )
        judgment, reason = judge_repost_with_features(f)
        assert judgment == "likely_repost"

    def test_independent(self):
        """独立来源判定。"""
        f = RepostFeatures()
        judgment, reason = judge_repost_with_features(f)
        assert judgment == "independent"

    def test_threshold_is_three(self):
        """阈值是 3。"""
        assert REPOST_MATCH_THRESHOLD == 3

    def test_below_threshold_independent(self):
        """低于阈值 → independent。"""
        f = RepostFeatures(
            domain_match=(True, ""),
            project_identifier_match=(True, ""),
        )
        judgment, _ = judge_repost_with_features(f)
        assert judgment == "independent"
