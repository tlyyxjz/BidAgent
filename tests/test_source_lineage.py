"""来源谱系判定单元测试 (v4.1 第八章).

覆盖:
- 来源角色判定 (classify_source_role)
- 同源检测 (detect_same_origin)
- 独立性分类 (classify_independence)
- 便捷构造函数 (build_lineage_features)
"""
from __future__ import annotations

import pytest

from app.processors.source_lineage import (
    LINEAGE_CONSISTENT_UNKNOWN,
    LINEAGE_INDEPENDENT,
    LINEAGE_SAME_ORIGIN,
    LINEAGE_SINGLE_SOURCE,
    LINEAGE_VERSION_DIFFERENCE,
    RULE_VERSION,
    SOURCE_ROLE_COMMERCIAL_REPOST,
    SOURCE_ROLE_INDEX_ONLY,
    SOURCE_ROLE_OFFICIAL_ORIGINAL,
    SOURCE_ROLE_OFFICIAL_REPOST,
    SOURCE_ROLE_UNKNOWN,
    SourceLineageFeatures,
    build_lineage_features,
    classify_independence,
    classify_source_role,
    detect_same_origin,
)


# ========== 规则版本 ==========


class TestRuleVersion:
    def test_rule_version(self):
        assert RULE_VERSION == "source_lineage_v1.0"


# ========== 来源角色判定 ==========


class TestClassifySourceRole:
    def test_official_original_ccgp(self):
        """ccgp.gov.cn 无上游来源标注 → 原始页面."""
        f = SourceLineageFeatures(
            url="http://www.ccgp.gov.cn/cggg/zygg/gkzb/202607/t123.htm",
            title="某项目招标公告",
            project_identifier="GC-2026-001",
        )
        assert classify_source_role(f) == SOURCE_ROLE_OFFICIAL_ORIGINAL

    def test_official_original_ggzy(self):
        """ggzy.gov.cn → 原始页面."""
        f = SourceLineageFeatures(
            url="https://www.ggzy.gov.cn/info/123",
            title="某项目公告",
        )
        assert classify_source_role(f) == SOURCE_ROLE_OFFICIAL_ORIGINAL

    def test_official_repost_with_upstream(self):
        """官方域名 + 上游来源标注 → 官方转载."""
        f = SourceLineageFeatures(
            url="http://www.ccgp.gov.cn/cggg/zygg/gkzb/202607/t123.htm",
            title="某项目招标公告",
            upstream_source_mention="转载自财政部",
        )
        assert classify_source_role(f) == SOURCE_ROLE_OFFICIAL_REPOST

    def test_commercial_repost_qianlima(self):
        """千里马域名 → 商业转载."""
        f = SourceLineageFeatures(
            url="https://www.qianlima.com/zb/kw-abc/",
            title="某项目招标公告",
        )
        assert classify_source_role(f) == SOURCE_ROLE_COMMERCIAL_REPOST

    def test_commercial_repost_with_upstream(self):
        """非官方域名 + 上游来源标注 → 商业转载."""
        f = SourceLineageFeatures(
            url="https://example.com/news/123",
            title="某项目公告",
            upstream_source_mention="来源: 中国政府采购网",
        )
        assert classify_source_role(f) == SOURCE_ROLE_COMMERCIAL_REPOST

    def test_index_only_no_attachment_no_project(self):
        """无附件 + 无项目编号 → 索引页面."""
        f = SourceLineageFeatures(
            url="https://example.com/list/page1",
            title="招标信息列表",
        )
        assert classify_source_role(f) == SOURCE_ROLE_INDEX_ONLY

    def test_unknown_domain_with_project(self):
        """未知域名 + 有项目编号 → unknown (非索引)."""
        f = SourceLineageFeatures(
            url="https://unknown-site.com/detail/123",
            title="某项目公告",
            project_identifier="GC-2026-001",
        )
        assert classify_source_role(f) == SOURCE_ROLE_UNKNOWN


# ========== 同源检测 ==========


class TestDetectSameOrigin:
    def test_same_url_same_origin(self):
        """URL 完全相同 → same_origin (1.0)."""
        fa = SourceLineageFeatures(url="http://example.com/a", title="A")
        fb = SourceLineageFeatures(url="http://example.com/a", title="B")
        status, conf = detect_same_origin(fa, fb)
        assert status == LINEAGE_SAME_ORIGIN
        assert conf == 1.0

    def test_same_project_same_type_simhash_close(self):
        """项目编号相同 + 公告类型相同 + SimHash近 → same_origin (0.9)."""
        fa = SourceLineageFeatures(
            url="http://ccgp.gov.cn/a",
            title="某项目招标公告",
            notice_type="tender",
            project_identifier="GC-2026-001",
            content_simhash=0x1234567890ABCDEF,
        )
        fb = SourceLineageFeatures(
            url="http://ccgp.gov.cn/b",
            title="某项目招标公告",
            notice_type="tender",
            project_identifier="GC-2026-001",
            content_simhash=0x1234567890ABCDEF,  # 完全相同
        )
        status, conf = detect_same_origin(fa, fb)
        assert status == LINEAGE_SAME_ORIGIN
        assert conf == 0.9

    def test_same_project_different_type_version_difference(self):
        """项目编号相同 + 公告类型不同 → version_difference (0.7)."""
        fa = SourceLineageFeatures(
            url="http://ccgp.gov.cn/a",
            title="某项目招标公告",
            notice_type="tender",
            project_identifier="GC-2026-001",
        )
        fb = SourceLineageFeatures(
            url="http://ccgp.gov.cn/b",
            title="某项目中标公告",
            notice_type="award",
            project_identifier="GC-2026-001",
        )
        status, conf = detect_same_origin(fa, fb)
        assert status == LINEAGE_VERSION_DIFFERENCE
        assert conf == 0.7

    def test_simhash_close_no_evidence_consistent_unknown(self):
        """SimHash近但无其他佐证 → consistent_unknown (0.5)."""
        fa = SourceLineageFeatures(
            url="http://site-a.com/a",
            title="完全不同的标题甲",
            content_simhash=0x1234567890ABCDEF,
        )
        fb = SourceLineageFeatures(
            url="http://site-b.com/b",
            title="完全不同的标题乙",
            content_simhash=0x1234567890ABCDEF,  # 完全相同但标题不同
        )
        status, conf = detect_same_origin(fa, fb)
        assert status == LINEAGE_CONSISTENT_UNKNOWN
        assert conf == 0.5

    def test_no_match_independent(self):
        """无任何匹配特征 → independent (0.0)."""
        fa = SourceLineageFeatures(
            url="http://site-a.com/a",
            title="项目A招标公告",
            project_identifier="GC-2026-001",
        )
        fb = SourceLineageFeatures(
            url="http://site-b.com/b",
            title="项目B中标公告",
            project_identifier="GC-2026-002",
        )
        status, conf = detect_same_origin(fa, fb)
        assert status == LINEAGE_INDEPENDENT
        assert conf == 0.0

    def test_simhash_close_title_similar_same_origin(self):
        """SimHash近 + 标题高度相似 → same_origin (0.8)."""
        # 构造标题几乎相同的两个来源
        fa = SourceLineageFeatures(
            url="http://site-a.com/a",
            title="某机关单位办公设备采购招标公告",
            content_simhash=0x1234567890ABCDEF,
        )
        fb = SourceLineageFeatures(
            url="http://site-b.com/b",
            title="某机关单位办公设备采购招标公告",  # 完全相同
            content_simhash=0x1234567890ABCDEF,
        )
        status, conf = detect_same_origin(fa, fb)
        assert status == LINEAGE_SAME_ORIGIN
        assert conf == 0.8


# ========== 独立性分类 ==========


class TestClassifyIndependence:
    def test_single_source(self):
        """只有一个来源 → single_source."""
        f = SourceLineageFeatures(url="http://a.com", title="A")
        assert classify_independence([f]) == LINEAGE_SINGLE_SOURCE

    def test_empty_list_single_source(self):
        """空列表 → single_source."""
        assert classify_independence([]) == LINEAGE_SINGLE_SOURCE

    def test_all_independent(self):
        """所有来源两两独立 → independent."""
        fa = SourceLineageFeatures(
            url="http://a.com",
            title="项目A",
            project_identifier="GC-001",
        )
        fb = SourceLineageFeatures(
            url="http://b.com",
            title="项目B",
            project_identifier="GC-002",
        )
        assert classify_independence([fa, fb]) == LINEAGE_INDEPENDENT

    def test_has_same_origin(self):
        """存在同源来源 → same_origin."""
        fa = SourceLineageFeatures(url="http://a.com", title="A")
        fb = SourceLineageFeatures(url="http://a.com", title="B")  # 同URL
        fc = SourceLineageFeatures(
            url="http://c.com",
            title="C",
            project_identifier="GC-003",
        )
        assert classify_independence([fa, fb, fc]) == LINEAGE_SAME_ORIGIN

    def test_has_consistent_unknown(self):
        """存在独立性未知 → consistent_unknown."""
        fa = SourceLineageFeatures(
            url="http://a.com",
            title="标题甲",
            content_simhash=0x1234567890ABCDEF,
        )
        fb = SourceLineageFeatures(
            url="http://b.com",
            title="标题乙",
            content_simhash=0x1234567890ABCDEF,
        )
        assert classify_independence([fa, fb]) == LINEAGE_CONSISTENT_UNKNOWN


# ========== 便捷构造函数 ==========


class TestBuildLineageFeatures:
    def test_build_with_content_text(self):
        """有正文 → 自动计算 SimHash."""
        f = build_lineage_features(
            url="http://ccgp.gov.cn/test",
            title="测试公告",
            content_text="这是一段测试正文内容用于计算SimHash指纹",
            project_identifier="GC-2026-001",
        )
        assert f.url == "http://ccgp.gov.cn/test"
        assert f.title == "测试公告"
        assert f.project_identifier == "GC-2026-001"
        assert f.content_simhash is not None
        assert isinstance(f.content_simhash, int)

    def test_build_without_content_text(self):
        """无正文 → SimHash 为 None."""
        f = build_lineage_features(
            url="http://ccgp.gov.cn/test",
            title="测试公告",
        )
        assert f.content_simhash is None

    def test_build_with_attachment_urls(self):
        """附件链接列表."""
        f = build_lineage_features(
            url="http://ccgp.gov.cn/test",
            title="测试",
            attachment_urls=["http://a.com/1.pdf", "http://b.com/2.docx"],
        )
        assert len(f.attachment_urls) == 2

    def test_build_default_attachment_urls(self):
        """附件链接默认空列表."""
        f = build_lineage_features(url="http://a.com", title="T")
        assert f.attachment_urls == []


# ========== SimHash 不单独决定 ==========


class TestSimHashNotSole:
    """v4.1 8.1 约束: SimHash 只能提供候选, 不能单独决定来源谱系."""

    def test_simhash_close_but_different_project_still_checked(self):
        """SimHash近但项目编号不同 → 不直接判同源, 需标题佐证."""
        fa = SourceLineageFeatures(
            url="http://a.com",
            title="完全不同的标题甲乙丙丁",
            project_identifier="GC-001",
            content_simhash=0x1234567890ABCDEF,
        )
        fb = SourceLineageFeatures(
            url="http://b.com",
            title="完全不同的标题戊己庚辛壬",
            project_identifier="GC-002",
            content_simhash=0x1234567890ABCDEF,  # SimHash相同
        )
        status, _ = detect_same_origin(fa, fb)
        # SimHash近但标题不相似且项目不同 → consistent_unknown (不是 same_origin)
        assert status == LINEAGE_CONSISTENT_UNKNOWN
        assert status != LINEAGE_SAME_ORIGIN
