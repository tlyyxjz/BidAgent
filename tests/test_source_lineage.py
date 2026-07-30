"""W3-01 来源谱系判定引擎测试。

覆盖 W3 周验收要求：
- 同一项目不同公告不会被误判为冲突
- 同一公告转载不会被误判为独立验证
- SimHash 同源转载候选识别
- 来源角色判定（official_original/official_repost/commercial_repost/unknown）
"""
from __future__ import annotations

import pytest

from app.processors.source_lineage import (
    SOURCE_ROLE_COMMERCIAL_REPOST,
    SOURCE_ROLE_OFFICIAL_ORIGINAL,
    SOURCE_ROLE_OFFICIAL_REPOST,
    SOURCE_ROLE_UNKNOWN,
    ConflictJudgment,
    SourceLineageResult,
    compute_fact_assertion_key,
    compute_source_group,
    find_repost_candidates,
    judge_field_conflict,
    judge_source_lineage,
    judge_source_role,
)


# ========== 来源角色判定测试 ==========

class TestJudgeSourceRole:
    """来源角色判定。"""

    def test_official_original_by_flag(self):
        """调用方标记为首发 → official_original。"""
        role, reason = judge_source_role(
            "http://example.com/notice/1",
            is_original_publication=True,
        )
        assert role == SOURCE_ROLE_OFFICIAL_ORIGINAL
        assert "首发" in reason

    def test_official_original_ccgp_domain(self):
        """ccgp 域名无转载标记 → official_original。"""
        role, reason = judge_source_role(
            "http://www.ccgp.gov.cn/notice/123",
            content_text="项目编号：ZFCG-2026-001",
        )
        assert role == SOURCE_ROLE_OFFICIAL_ORIGINAL
        assert "ccgp" in reason

    def test_official_repost_with_marker(self):
        """官方域名 + 含转载标记 → official_repost。"""
        role, reason = judge_source_role(
            "http://www.ccgp.gov.cn/repost/456",
            content_text="转载自某省政府采购网\n项目编号：ZFCG-2026-001",
        )
        assert role == SOURCE_ROLE_OFFICIAL_REPOST
        assert "转载标记" in reason

    def test_commercial_repost_bidcenter(self):
        """bidcenter 域名 → commercial_repost。"""
        role, reason = judge_source_role(
            "http://www.bidcenter.com.cn/news/789",
            content_text="项目编号：ZFCG-2026-001",
        )
        assert role == SOURCE_ROLE_COMMERCIAL_REPOST
        assert "商业域名" in reason

    def test_commercial_repost_unknown_domain_with_marker(self):
        """非官方域名 + 含转载标记 → commercial_repost。"""
        role, _ = judge_source_role(
            "http://www.somethingsite.com/news/1",
            content_text="文章来源：某网\n项目编号：ZFCG-2026-001",
        )
        assert role == SOURCE_ROLE_COMMERCIAL_REPOST

    def test_unknown_role_no_domain_feature(self):
        """无域名特征 + 无转载标记 → unknown。"""
        role, reason = judge_source_role(
            "http://www.somethingsite.com/news/1",
            content_text="项目编号：ZFCG-2026-001",
        )
        assert role == SOURCE_ROLE_UNKNOWN
        assert "未知" in reason

    def test_empty_url(self):
        """空 URL → unknown。"""
        role, _ = judge_source_role("")
        assert role == SOURCE_ROLE_UNKNOWN

    def test_gov_domain(self):
        """gov 域名 → official_original。"""
        role, _ = judge_source_role(
            "http://www.shanghai.gov.cn/notice/1",
            content_text="项目编号",
        )
        assert role == SOURCE_ROLE_OFFICIAL_ORIGINAL

    def test_ggzy_domain(self):
        """ggzy 域名 → official_original。"""
        role, _ = judge_source_role(
            "http://www.ggzy.gov.cn/notice/1",
            content_text="项目编号",
        )
        assert role == SOURCE_ROLE_OFFICIAL_ORIGINAL


# ========== 来源谱系组生成测试 ==========

class TestComputeSourceGroup:
    """来源谱系组 ID 生成。"""

    def test_same_input_same_group(self):
        """相同输入 → 相同 source_group。"""
        g1 = compute_source_group("http://a.com/1", 12345)
        g2 = compute_source_group("http://a.com/1", 12345)
        assert g1 == g2
        assert len(g1) == 16

    def test_different_url_different_group(self):
        """不同 URL → 不同 source_group。"""
        g1 = compute_source_group("http://a.com/1", 12345)
        g2 = compute_source_group("http://b.com/2", 12345)
        assert g1 != g2

    def test_different_simhash_different_group(self):
        """不同 SimHash → 不同 source_group。"""
        g1 = compute_source_group("http://a.com/1", 12345)
        g2 = compute_source_group("http://a.com/1", 67890)
        assert g1 != g2

    def test_group_is_hex(self):
        """source_group 是 16 位十六进制。"""
        g = compute_source_group("http://a.com/1", 0)
        assert len(g) == 16
        int(g, 16)  # 能解析为十六进制


# ========== 事实断言键生成测试 ==========

class TestComputeFactAssertionKey:
    """事实断言键生成。"""

    def test_same_input_same_key(self):
        """相同输入 → 相同 key。"""
        k1 = compute_fact_assertion_key("amount", "100万", "ZFCG-2026-001")
        k2 = compute_fact_assertion_key("amount", "100万", "ZFCG-2026-001")
        assert k1 == k2
        assert len(k1) == 16

    def test_different_project_different_key(self):
        """不同项目同字段同值 → 不同 key（项目隔离）。"""
        k1 = compute_fact_assertion_key("amount", "100万", "ZFCG-2026-001")
        k2 = compute_fact_assertion_key("amount", "100万", "ZFCG-2026-002")
        assert k1 != k2

    def test_different_field_different_key(self):
        """同项目不同字段 → 不同 key。"""
        k1 = compute_fact_assertion_key("amount", "100万", "ZFCG-2026-001")
        k2 = compute_fact_assertion_key("winner_name", "某公司", "ZFCG-2026-001")
        assert k1 != k2

    def test_different_value_different_key(self):
        """同项目同字段不同值 → 不同 key（版本差异检测基础）。"""
        k1 = compute_fact_assertion_key("amount", "100万", "ZFCG-2026-001")
        k2 = compute_fact_assertion_key("amount", "120万", "ZFCG-2026-001")
        assert k1 != k2

    def test_no_project_identifier(self):
        """无项目编号也能生成 key（但不同项目可能碰撞）。"""
        k = compute_fact_assertion_key("amount", "100万")
        assert len(k) == 16


# ========== 同源转载候选识别测试 ==========

class TestFindRepostCandidates:
    """同源转载候选识别。"""

    def test_no_match(self):
        """无匹配（汉明距离 > 3）。"""
        candidates = [("src1", 0xFFFFFFFFFFFFFFFF, "url1")]
        result = find_repost_candidates(0x0000000000000000, candidates)
        assert result == []

    def test_exact_match(self):
        """完全匹配（汉明距离 0）。"""
        target = 0x123456789ABCDEF0
        candidates = [("src1", target, "url1"), ("src2", 0xFFFFFFFFFFFFFFFF, "url2")]
        result = find_repost_candidates(target, candidates)
        assert len(result) == 1
        assert result[0][0] == "src1"

    def test_near_match_within_threshold(self):
        """汉明距离 ≤ 3 视为匹配。"""
        # 用非零 target 避免触发 target==0 保护
        target = 0x1000000000000000
        # 汉明距离 1
        cand1 = ("src1", 0x1000000000000001, "url1")
        # 汉明距离 3
        cand2 = ("src2", 0x1000000000000007, "url2")
        # 汉明距离 4（不匹配）
        cand3 = ("src3", 0x100000000000000F, "url3")
        result = find_repost_candidates(target, [cand1, cand2, cand3])
        assert len(result) == 2
        assert result[0][0] == "src1"  # 距离最近
        assert result[1][0] == "src2"

    def test_empty_target(self):
        """target_simhash=0 → 返回空。"""
        result = find_repost_candidates(0, [("src1", 12345, "url1")])
        assert result == []

    def test_sorted_by_distance(self):
        """结果按汉明距离升序。"""
        # 用非零 target 避免触发 target==0 保护
        target = 0x1000000000000000
        candidates = [
            ("far", 0x1000000000000007, "url_far"),    # 距离 3
            ("near", 0x1000000000000001, "url_near"),  # 距离 1
        ]
        result = find_repost_candidates(target, candidates)
        assert result[0][0] == "near"
        assert result[1][0] == "far"


# ========== 主判定函数测试 ==========

class TestJudgeSourceLineage:
    """来源谱系判定主函数。"""

    def test_full_pipeline_official(self):
        """完整流程：官方域名 + 字段信息。"""
        result = judge_source_lineage(
            source_url="http://www.ccgp.gov.cn/notice/1",
            content_text="项目编号：ZFCG-2026-001\n预算金额：100万",
            field_name="amount",
            normalized_value="1000000",
            project_identifier="ZFCG-2026-001",
        )
        assert isinstance(result, SourceLineageResult)
        assert result.source_role == SOURCE_ROLE_OFFICIAL_ORIGINAL
        assert result.simhash != 0
        assert len(result.source_group) == 16
        assert result.fact_assertion_key is not None
        assert len(result.fact_assertion_key) == 16

    def test_pipeline_without_field_info(self):
        """无字段信息 → fact_assertion_key 为 None。"""
        result = judge_source_lineage(
            source_url="http://www.ccgp.gov.cn/notice/1",
            content_text="项目编号",
        )
        assert result.fact_assertion_key is None
        assert result.simhash != 0

    def test_pipeline_with_repost_candidates(self):
        """含转载候选 → 填充 repost_candidates。"""
        text = "项目编号：ZFCG-2026-001\n预算金额：100万"
        result = judge_source_lineage(
            source_url="http://www.ccgp.gov.cn/notice/1",
            content_text=text,
            repost_candidates=[("src1", compute_simhash_dup(text), "url1")],
        )
        # 同文本 SimHash 相同，汉明距离 0
        assert len(result.repost_candidates) == 1

    def test_idempotent(self):
        """幂等：相同输入相同输出。"""
        r1 = judge_source_lineage("http://a.com/1", "测试文本", field_name="amount", normalized_value="100")
        r2 = judge_source_lineage("http://a.com/1", "测试文本", field_name="amount", normalized_value="100")
        assert r1.source_role == r2.source_role
        assert r1.simhash == r2.simhash
        assert r1.source_group == r2.source_group
        assert r1.fact_assertion_key == r2.fact_assertion_key


def compute_simhash_dup(text: str) -> int:
    """辅助函数：计算 SimHash（避免循环导入）。"""
    from app.processors.simhash import compute_simhash
    return compute_simhash(text)


# ========== 冲突判定测试（W3 周验收核心）==========

class TestJudgeFieldConflict:
    """版本差异 vs 事实冲突判定（W3 周验收核心）。"""

    def test_same_fact_key_no_conflict(self):
        """相同 fact_key → 非冲突（同值）。"""
        key = compute_fact_assertion_key("amount", "100万", "ZFCG-2026-001")
        j = judge_field_conflict(
            key, key, "amount", "100万", "100万",
            "ZFCG-2026-001", "tender", "tender"
        )
        assert not j.is_conflict
        assert not j.is_version_diff

    def test_version_diff_different_notice_type(self):
        """同项目不同公告类型同字段不同值 → 版本差异（非冲突）。

        W3 周验收要求：同一项目不同公告不会被误判为冲突。
        """
        k1 = compute_fact_assertion_key("amount", "100万", "ZFCG-2026-001")
        k2 = compute_fact_assertion_key("amount", "120万", "ZFCG-2026-001")
        j = judge_field_conflict(
            k1, k2, "amount", "100万", "120万",
            "ZFCG-2026-001", "tender", "award"
        )
        assert not j.is_conflict
        assert j.is_version_diff
        assert "版本" in j.reason or "不同公告类型" in j.reason

    def test_conflict_same_notice_type_different_value(self):
        """同项目同公告类型同字段不同值 → 事实冲突。"""
        k1 = compute_fact_assertion_key("amount", "100万", "ZFCG-2026-001")
        k2 = compute_fact_assertion_key("amount", "120万", "ZFCG-2026-001")
        j = judge_field_conflict(
            k1, k2, "amount", "100万", "120万",
            "ZFCG-2026-001", "tender", "tender"
        )
        assert j.is_conflict
        assert not j.is_version_diff

    def test_no_project_identifier_no_judgment(self):
        """无项目编号 → 无法判断。"""
        k1 = compute_fact_assertion_key("amount", "100万")
        k2 = compute_fact_assertion_key("amount", "120万")
        j = judge_field_conflict(
            k1, k2, "amount", "100万", "120万",
            "", "tender", "tender"
        )
        assert not j.is_conflict
        assert not j.is_version_diff

    def test_correction_notice_is_version_diff(self):
        """更正公告 vs 招标公告 → 版本差异（更正是合法变更）。"""
        k1 = compute_fact_assertion_key("bid_deadline", "2026-08-01", "ZFCG-2026-001")
        k2 = compute_fact_assertion_key("bid_deadline", "2026-08-15", "ZFCG-2026-001")
        j = judge_field_conflict(
            k1, k2, "bid_deadline", "2026-08-01", "2026-08-15",
            "ZFCG-2026-001", "tender", "correction"
        )
        assert not j.is_conflict
        assert j.is_version_diff

    def test_repost_not_independent_validation(self):
        """同一公告转载 → fact_key 相同 → 非冲突（不视为独立验证）。

        W3 周验收要求：同一公告转载不会被误判为独立验证。
        """
        # 转载内容相同，fact_key 相同
        k = compute_fact_assertion_key("amount", "100万", "ZFCG-2026-001")
        j = judge_field_conflict(
            k, k, "amount", "100万", "100万",
            "ZFCG-2026-001", "tender", "tender"
        )
        assert not j.is_conflict
        assert not j.is_version_diff
        assert "同值" in j.reason
