"""反幻觉校验 + SimHash + HostnameLRUCache 单元测试（第七轮补充）。

覆盖豆包第七轮报告 m-3 缺口：
- hallucination_checker.py 的金额/日期归一化、事实比对逻辑
- simhash.py 的汉明距离计算、相似度判断、find_duplicate_in_iter
- hostname_cache.py 的 LRU 缓存淘汰、move-to-end、容量限制

工程规范：
- 纯函数测试，不依赖数据库/网络
- 断言精确，覆盖核心场景
"""

from __future__ import annotations

import pytest

from app.processors.hallucination_checker import (
    Fact,
    _normalize_amount,
    _normalize_date,
    extract_facts,
    _fact_in_source,
    check_content,
)
from app.processors.simhash import (
    MASK64,
    compute_simhash,
    hamming_distance,
    is_similar,
    find_duplicate_in_iter,
)

# SimHash 位数（64 位）
SIMHASH_BITS = 64
from app.utils.hostname_cache import HostnameLRUCache


# ============================================================
# 测试套件 1：反幻觉校验 - 金额/日期归一化
# ============================================================

class TestNormalizeAmount:
    """验证 hallucination_checker._normalize_amount 金额归一化。"""

    def test_normalize_amount_wan(self):
        """'100万元' 归一化为 '1000000'。"""
        assert _normalize_amount("100万元") == "1000000"

    def test_normalize_amount_yi(self):
        """'1.5亿元' 归一化为 '150000000'。"""
        assert _normalize_amount("1.5亿元") == "150000000"

    def test_normalize_amount_yuan(self):
        """'5000元' 归一化为 '5000'。"""
        assert _normalize_amount("5000元") == "5000"

    def test_normalize_amount_decimal(self):
        """'1.5万元' 归一化为 '15000'（保留小数位）。"""
        result = _normalize_amount("1.5万元")
        assert result is not None
        assert float(result) == 15000.0

    def test_normalize_amount_pure_number_returns_none(self):
        """新-3 修复：纯数字无单位返回 None（防误匹配年份/编号）。"""
        assert _normalize_amount("2024") is None
        assert _normalize_amount("100") is None

    def test_normalize_amount_no_unit_returns_none(self):
        """无单位返回 None。"""
        assert _normalize_amount("预算100") is None

    def test_normalize_amount_empty_returns_none(self):
        """空字符串返回 None。"""
        assert _normalize_amount("") is None
        assert _normalize_amount("   ") is None


class TestNormalizeDate:
    """验证 hallucination_checker._normalize_date 日期归一化。"""

    def test_normalize_date_dash(self):
        """'2024-05-01' → '2024-05-01'。"""
        assert _normalize_date("2024-05-01") == "2024-05-01"

    def test_normalize_date_slash(self):
        """'2024/05/01' → '2024-05-01'。"""
        assert _normalize_date("2024/05/01") == "2024-05-01"

    def test_normalize_date_dot(self):
        """新-6 修复：'2024.05.01' → '2024-05-01'。"""
        assert _normalize_date("2024.05.01") == "2024-05-01"

    def test_normalize_date_chinese(self):
        """'2024年5月1日' → '2024-05-01'。"""
        assert _normalize_date("2024年5月1日") == "2024-05-01"

    def test_normalize_date_invalid_returns_none(self):
        """无效日期返回 None。"""
        assert _normalize_date("2024-13-45") is None  # 月日越界
        assert _normalize_date("not a date") is None
        assert _normalize_date("") is None

    def test_normalize_date_single_digit_month_day(self):
        """单位数月日也能解析。"""
        assert _normalize_date("2024-5-1") == "2024-05-01"


# ============================================================
# 测试套件 2：反幻觉校验 - 事实提取与比对
# ============================================================

class TestExtractFacts:
    """验证 hallucination_checker.extract_facts 提取关键事实。"""

    def test_extract_amount_fact(self):
        """从文本中提取金额事实。"""
        text = "本项目预算100万元，欢迎参与。"
        facts = extract_facts(text)
        amount_facts = [f for f in facts if f.category == "金额"]
        assert len(amount_facts) > 0, "应该提取到金额事实"
        assert "100" in amount_facts[0].value

    def test_extract_date_fact(self):
        """从文本中提取日期事实。"""
        text = "投标截止时间为2024-05-01。"
        facts = extract_facts(text)
        date_facts = [f for f in facts if f.category == "日期"]
        assert len(date_facts) > 0, "应该提取到日期事实"

    def test_extract_facts_empty_text(self):
        """空文本返回空列表。"""
        assert extract_facts("") == []
        assert extract_facts("   ") == []


class TestFactInSource:
    """验证 hallucination_checker._fact_in_source 事实比对。"""

    def test_amount_fact_in_source_match(self):
        """金额事实在原文中存在（带单位）。"""
        fact = Fact(category="金额", value="100万元")
        source = "本项目预算100万元"
        assert _fact_in_source(fact, source) is True

    def test_amount_fact_not_in_source(self):
        """金额事实在原文中不存在。"""
        fact = Fact(category="金额", value="200万元")
        source = "本项目预算100万元"
        assert _fact_in_source(fact, source) is False

    def test_date_fact_in_source_match(self):
        """日期事实在原文中存在（归一化匹配）。"""
        fact = Fact(category="日期", value="2024-05-01")
        source = "截止时间2024年5月1日"
        # 归一化后应能匹配
        assert _fact_in_source(fact, source) is True

    def test_amount_equivalent_match(self):
        """'100万元' 与 '1000000元' 归一化后等价应匹配。"""
        fact = Fact(category="金额", value="100万元")
        source = "预算为1000000元"
        # 两者归一化后都为 '1000000'，应匹配
        assert _fact_in_source(fact, source) is True


class TestCheckContent:
    """验证 hallucination_checker.check_content 整体反幻觉校验。"""

    def test_check_content_all_facts_supported(self):
        """所有事实都有原文支撑，无幻觉。"""
        content = "项目预算100万元，截止日期2024-05-01。"
        source = "本项目预算100万元，投标截止日期2024-05-01，欢迎参与。"
        report = check_content(content, source)
        assert report.hallucinated_facts == 0
        assert report.passed is True

    def test_check_content_detects_hallucination(self):
        """检测到幻觉（事实无原文支撑）。"""
        content = "项目预算200万元，截止日期2024-05-01。"
        source = "本项目预算100万元，投标截止日期2024-05-01。"
        report = check_content(content, source)
        assert report.hallucinated_facts > 0
        assert report.passed is False

    def test_check_content_empty_source_skips_check(self):
        """原文为空时跳过校验（hallucination_checker 的设计：source 为空时不报幻觉）。"""
        content = "项目预算100万元。"
        source = ""
        report = check_content(content, source)
        # 空原文时 check_content 直接 skip，没有事实提取，passed=True
        # 这是 hallucination_checker 的设计选择（避免无原文时误报）
        assert report.total_facts == 0
        assert report.passed is True


# ============================================================
# 测试套件 3：SimHash 算法
# ============================================================

class TestComputeSimhash:
    """验证 simhash.compute_simhash 计算。"""

    def test_compute_simhash_returns_int(self):
        """返回整数。"""
        result = compute_simhash("测试文本内容")
        assert isinstance(result, int)

    def test_compute_simhash_empty_text_returns_zero(self):
        """空文本返回 0。"""
        assert compute_simhash("") == 0
        assert compute_simhash("   ") == 0

    def test_compute_simhash_deterministic(self):
        """相同文本产生相同 simhash（确定性）。"""
        text = "上海市浦东新区招标公告"
        assert compute_simhash(text) == compute_simhash(text)

    def test_compute_simhash_similar_text_close_hash(self):
        """相似文本的 simhash 汉明距离小。"""
        text1 = "上海市浦东新区招标公告项目"
        text2 = "上海市浦东新区招标公告项目编号"
        h1 = compute_simhash(text1)
        h2 = compute_simhash(text2)
        # 相似文本汉明距离应 ≤ 10（宽松阈值）
        assert hamming_distance(h1, h2) <= 10

    def test_compute_simhash_different_text_far_hash(self):
        """完全不同文本的 simhash 汉明距离较大。"""
        text1 = "上海市浦东新区招标公告"
        text2 = "北京市海淀区政府采购电脑设备"
        h1 = compute_simhash(text1)
        h2 = compute_simhash(text2)
        # 不同文本汉明距离应 > 5
        assert hamming_distance(h1, h2) > 5


class TestHammingDistance:
    """验证 simhash.hamming_distance 计算。"""

    def test_hamming_distance_same_value_zero(self):
        """相同值的汉明距离为 0。"""
        assert hamming_distance(12345, 12345) == 0

    def test_hamming_distance_one_bit_diff(self):
        """差 1 位的汉明距离为 1。"""
        assert hamming_distance(0b1000, 0b1001) == 1

    def test_hamming_distance_all_bits_diff(self):
        """所有位都不同的汉明距离为 SIMHASH_BITS。"""
        # 64 位全 0 vs 全 1
        all_zero = 0
        all_one = (1 << SIMHASH_BITS) - 1
        assert hamming_distance(all_zero, all_one) == SIMHASH_BITS


class TestIsSimilar:
    """验证 simhash.is_similar 相似度判断。"""

    def test_is_similar_same_hash(self):
        """相同 simhash 判定为相似。"""
        h = compute_simhash("测试文本")
        assert is_similar(h, h) is True

    def test_is_similar_within_threshold(self):
        """汉明距离 ≤ 阈值判定为相似。"""
        h1 = 0b1000
        h2 = 0b1001  # 差 1 位
        assert is_similar(h1, h2, threshold=3) is True

    def test_is_similar_beyond_threshold(self):
        """汉明距离 > 阈值判定为不相似。"""
        h1 = 0b0000
        h2 = 0b1111  # 差 4 位
        assert is_similar(h1, h2, threshold=3) is False


class TestFindDuplicateInIter:
    """验证 simhash.find_duplicate_in_iter 找重复。"""

    def test_find_duplicate_exists(self):
        """候选集中存在重复。"""
        target = compute_simhash("上海市招标公告")
        candidates = [
            (1, compute_simhash("完全不同内容")),
            (2, compute_simhash("上海市招标公告")),  # 重复
            (3, compute_simhash("北京市采购信息")),
        ]
        result = find_duplicate_in_iter(target, candidates, threshold=3)
        assert result is not None
        assert result[0] == 2  # 返回重复条目的 id

    def test_find_duplicate_not_exists(self):
        """候选集中无重复返回 None。"""
        target = compute_simhash("上海市招标公告")
        candidates = [
            (1, compute_simhash("北京市采购信息")),
            (2, compute_simhash("广州市政府公告")),
        ]
        result = find_duplicate_in_iter(target, candidates, threshold=3)
        assert result is None

    def test_find_duplicate_empty_candidates(self):
        """空候选集返回 None。"""
        result = find_duplicate_in_iter(12345, [], threshold=3)
        assert result is None

    def test_find_duplicate_similar_within_threshold(self):
        """相似但非完全相同（≤ 阈值）也算重复。"""
        target = 0b1000
        candidates = [(1, 0b1010)]  # 差 1 位
        result = find_duplicate_in_iter(target, candidates, threshold=3)
        assert result is not None
        assert result[0] == 1


# ============================================================
# 测试套件 4：HostnameLRUCache
# ============================================================

class TestHostnameLRUCache:
    """验证 HostnameLRUCache 缓存行为。"""

    def test_get_miss_returns_none(self):
        """未命中返回 None。"""
        cache = HostnameLRUCache(capacity=64)
        assert cache.get("example.com") is None

    def test_set_then_get_hit(self):
        """写入后查询命中。"""
        cache = HostnameLRUCache(capacity=64)
        cache.set("example.com", (True, ""))
        result = cache.get("example.com")
        assert result == (True, "")

    def test_set_overwrite_existing(self):
        """覆盖已存在的值。"""
        cache = HostnameLRUCache(capacity=64)
        cache.set("example.com", (True, ""))
        cache.set("example.com", (False, "blocked"))
        result = cache.get("example.com")
        assert result == (False, "blocked")

    def test_lru_eviction_when_capacity_exceeded(self):
        """m-2 验证：超容量时淘汰最旧条目。"""
        cache = HostnameLRUCache(capacity=3)
        cache.set("a.com", (True, ""))
        cache.set("b.com", (True, ""))
        cache.set("c.com", (True, ""))
        # 写入第 4 个，应淘汰 a.com（最旧）
        cache.set("d.com", (True, ""))
        assert cache.get("a.com") is None, "a.com 应被淘汰"
        assert cache.get("b.com") is not None
        assert cache.get("c.com") is not None
        assert cache.get("d.com") is not None
        assert cache.size == 3

    def test_lru_move_to_end_on_get(self):
        """m-2 验证：get 命中后移动到末尾（不被淘汰）。"""
        cache = HostnameLRUCache(capacity=3)
        cache.set("a.com", (True, ""))
        cache.set("b.com", (True, ""))
        cache.set("c.com", (True, ""))
        # 访问 a.com，使其成为最新
        cache.get("a.com")
        # 写入第 4 个，应淘汰 b.com（最旧，a.com 已 move-to-end）
        cache.set("d.com", (True, ""))
        assert cache.get("a.com") is not None, "a.com 不应被淘汰（已 move-to-end）"
        assert cache.get("b.com") is None, "b.com 应被淘汰"
        assert cache.size == 3

    def test_lru_move_to_end_on_set_existing(self):
        """m-2 验证：set 已存在 key 时也 move-to-end。"""
        cache = HostnameLRUCache(capacity=3)
        cache.set("a.com", (True, ""))
        cache.set("b.com", (True, ""))
        cache.set("c.com", (True, ""))
        # 重新 set a.com，使其成为最新
        cache.set("a.com", (True, ""))
        # 写入第 4 个，应淘汰 b.com（最旧）
        cache.set("d.com", (True, ""))
        assert cache.get("a.com") is not None, "a.com 不应被淘汰"
        assert cache.get("b.com") is None, "b.com 应被淘汰"

    def test_capacity_zero_raises(self):
        """容量为 0 抛 ValueError。"""
        with pytest.raises(ValueError):
            HostnameLRUCache(capacity=0)

    def test_capacity_negative_raises(self):
        """容量为负抛 ValueError。"""
        with pytest.raises(ValueError):
            HostnameLRUCache(capacity=-1)

    def test_clear(self):
        """清空缓存。"""
        cache = HostnameLRUCache(capacity=64)
        cache.set("a.com", (True, ""))
        cache.set("b.com", (True, ""))
        assert cache.size == 2
        cache.clear()
        assert cache.size == 0
        assert cache.get("a.com") is None

    def test_contains(self):
        """__contains__ 支持 in 操作符。"""
        cache = HostnameLRUCache(capacity=64)
        cache.set("a.com", (True, ""))
        assert "a.com" in cache
        assert "b.com" not in cache

    def test_len(self):
        """__len__ 返回当前大小。"""
        cache = HostnameLRUCache(capacity=64)
        assert len(cache) == 0
        cache.set("a.com", (True, ""))
        assert len(cache) == 1
        cache.set("b.com", (True, ""))
        assert len(cache) == 2
