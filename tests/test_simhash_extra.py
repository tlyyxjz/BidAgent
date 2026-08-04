"""simhash.py 补充测试：提升覆盖率 78% → 95%+。

覆盖未覆盖行：35-39, 48, 55, 57-62, 93, 141, 144

策略：
- 直接调用内部函数 _char_ngrams / _tokenize 覆盖各分支
- 通过 monkeypatch 模拟 jieba 分词异常，覆盖退化路径
- find_duplicate_in_iter 边界条件（target=0, cand_hash=0）
"""
from __future__ import annotations

import pytest

from app.processors import simhash as simhash_mod
from app.processors.simhash import (
    MASK64,
    _char_ngrams,
    _hash64,
    _tokenize,
    compute_simhash,
    find_duplicate_in_iter,
    hamming_distance,
    is_similar,
)


# ============================================================
# 测试套件 1：_char_ngrams 边界（行 48）
# ============================================================

class TestCharNgramsEdge:
    """覆盖 _char_ngrams 中 len(cleaned) < n 的分支。"""

    def test_text_shorter_than_n_returns_single(self):
        """行 48：文本长度 < n 时返回 [cleaned]。"""
        result = _char_ngrams("a", n=2)
        assert result == ["a"]

    def test_single_char_returns_list_with_char(self):
        """单字符返回包含该字符的列表。"""
        result = _char_ngrams("x", n=2)
        assert result == ["x"]

    def test_empty_text_returns_empty(self):
        """行 48（else 分支）：空文本返回空列表。"""
        result = _char_ngrams("", n=2)
        assert result == []

    def test_whitespace_only_returns_empty(self):
        """纯空白（被 re.sub 清空）返回空列表。"""
        result = _char_ngrams("   \t\n  ", n=2)
        assert result == []

    def test_exact_n_length_returns_one_gram(self):
        """文本长度恰好等于 n 时返回 1 个 gram。"""
        result = _char_ngrams("ab", n=2)
        assert result == ["ab"]

    def test_longer_text_returns_multiple_grams(self):
        """正常多 gram 情况。"""
        result = _char_ngrams("abcde", n=2)
        assert result == ["ab", "bc", "cd", "de"]

    def test_n_equals_3(self):
        """3-gram 测试。"""
        result = _char_ngrams("abcd", n=3)
        assert result == ["abc", "bcd"]


# ============================================================
# 测试套件 2：_tokenize 空文本与异常退化（行 55, 57-62）
# ============================================================

class TestTokenizeBranches:
    """覆盖 _tokenize 中空文本和 jieba 异常退化分支。"""

    def test_tokenize_empty_text_returns_empty(self):
        """行 55：空文本返回空列表。"""
        assert _tokenize("") == []

    def test_tokenize_whitespace_only_returns_empty(self):
        """行 55：纯空白文本返回空列表。"""
        assert _tokenize("   ") == []
        assert _tokenize("\t\n") == []

    def test_tokenize_jieba_exception_falls_back_to_ngram(self, monkeypatch):
        """行 57-62：jieba 分词异常时退化到字符 2-gram。"""
        # 模拟 _tokenizer 抛异常
        def _broken_tokenizer(text):
            raise RuntimeError("jieba crash")

        monkeypatch.setattr(simhash_mod, "_tokenizer", _broken_tokenizer)

        result = _tokenize("测试文本")
        # 应退化到字符 2-gram
        assert len(result) > 0
        assert "测试" in result or "试文" in result

    def test_tokenize_jieba_returns_empty_falls_back_to_ngram(self, monkeypatch):
        """行 59-60：jieba 返回空列表时退化到字符 2-gram。"""
        def _empty_tokenizer(text):
            return []

        monkeypatch.setattr(simhash_mod, "_tokenizer", _empty_tokenizer)

        result = _tokenize("测试文本")
        assert len(result) > 0

    def test_tokenize_jieba_returns_whitespace_only_falls_back(self, monkeypatch):
        """jieba 返回纯空白 token 时退化到 n-gram。"""
        def _ws_tokenizer(text):
            return ["  ", "\t"]

        monkeypatch.setattr(simhash_mod, "_tokenizer", _ws_tokenizer)

        result = _tokenize("测试文本")
        assert len(result) > 0


# ============================================================
# 测试套件 3：compute_simhash 空 token 分支（行 93）
# ============================================================

class TestComputeSimhashEmptyTokens:
    """覆盖 compute_simhash 中 tokens 为空时返回 0 的分支。"""

    def test_compute_simhash_empty_text_returns_zero(self):
        """空文本返回 0。"""
        assert compute_simhash("") == 0

    def test_compute_simhash_whitespace_only_returns_zero(self):
        """纯空白文本返回 0。"""
        assert compute_simhash("   ") == 0
        assert compute_simhash("\t\n  ") == 0

    def test_compute_simhash_text_becomes_empty_tokens(self, monkeypatch):
        """行 93：文本经分词后 tokens 为空时返回 0。"""
        # 模拟 _tokenize 返回空列表
        monkeypatch.setattr(simhash_mod, "_tokenize", lambda text: [])
        assert compute_simhash("任意文本") == 0


# ============================================================
# 测试套件 4：find_duplicate_in_iter 边界（行 141, 144）
# ============================================================

class TestFindDuplicateEdgeCases:
    """覆盖 find_duplicate_in_iter 中 target=0 和 cand_hash=0 分支。"""

    def test_target_zero_returns_none(self):
        """行 141：target == 0 时直接返回 None。"""
        candidates = [(1, 12345), (2, 67890)]
        result = find_duplicate_in_iter(0, candidates, threshold=3)
        assert result is None

    def test_candidate_hash_zero_skipped(self):
        """行 144：cand_hash == 0 的候选项被跳过。"""
        target = compute_simhash("测试文本")
        candidates = [
            (1, 0),  # hash=0，应被跳过
            (2, target),  # 完全匹配
        ]
        result = find_duplicate_in_iter(target, candidates, threshold=3)
        assert result is not None
        assert result[0] == 2  # 跳过了 id=1，匹配了 id=2

    def test_all_candidates_hash_zero_returns_none(self):
        """所有候选 hash 都是 0 时返回 None。"""
        target = compute_simhash("测试文本")
        candidates = [(1, 0), (2, 0), (3, 0)]
        result = find_duplicate_in_iter(target, candidates, threshold=3)
        assert result is None

    def test_empty_candidates_returns_none(self):
        """空候选集返回 None。"""
        result = find_duplicate_in_iter(12345, [], threshold=3)
        assert result is None

    def test_target_zero_ignores_candidates(self):
        """target=0 时即使有匹配候选也返回 None。"""
        result = find_duplicate_in_iter(0, [(1, 0)], threshold=3)
        assert result is None


# ============================================================
# 测试套件 5：_hash64 基本验证
# ============================================================

class TestHash64:
    """_hash64 哈希函数验证。"""

    def test_hash64_returns_int(self):
        """返回 64 位整数。"""
        h = _hash64("test")
        assert isinstance(h, int)
        assert 0 <= h <= MASK64

    def test_hash64_deterministic(self):
        """相同输入产生相同哈希。"""
        assert _hash64("hello") == _hash64("hello")

    def test_hash64_different_inputs_different_hashes(self):
        """不同输入产生不同哈希。"""
        assert _hash64("hello") != _hash64("world")

    def test_hash64_empty_string(self):
        """空字符串也能哈希。"""
        h = _hash64("")
        assert isinstance(h, int)


# ============================================================
# 测试套件 6：jieba tokenizer 验证（行 35-39）
# ============================================================

class TestJiebaTokenizer:
    """覆盖 jieba 分词器路径（行 35-39 在模块导入时已执行）。"""

    def test_tokenizer_is_set_when_jieba_available(self):
        """jieba 安装时 _tokenizer 应被设置为 _jieba_tokenizer。"""
        # 如果 jieba 已安装，_tokenizer 不为 None
        if simhash_mod._tokenizer is not None:
            # 直接调用 _tokenizer 验证它能工作
            tokens = simhash_mod._tokenizer("上海市招标公告")
            assert isinstance(tokens, list)
            assert len(tokens) > 0

    def test_jieba_tokenizer_strips_whitespace(self):
        """jieba 分词结果应去除空白 token。"""
        if simhash_mod._tokenizer is not None:
            tokens = simhash_mod._tokenizer("上海 招标")
            # 不应包含纯空白 token
            for t in tokens:
                assert t.strip() != ""

    def test_tokenize_chinese_text_with_jieba(self):
        """中文文本经 jieba 分词后能正确计算 simhash。"""
        h = compute_simhash("上海市浦东新区招标公告项目")
        assert isinstance(h, int)
        assert h != 0

    def test_tokenize_mixed_text(self):
        """中英文混合文本能正确分词。"""
        tokens = _tokenize("上海市ABC123招标")
        assert len(tokens) > 0
        h = compute_simhash("上海市ABC123招标")
        assert h != 0

    def test_char_ngram_fallback_produces_valid_simhash(self):
        """字符 2-gram 退化方案也能产生有效 simhash。"""
        # 用 monkeypatch 强制走 n-gram 路径
        original_tokenizer = simhash_mod._tokenizer
        try:
            simhash_mod._tokenizer = None
            h = compute_simhash("测试文本内容")
            assert h != 0
        finally:
            simhash_mod._tokenizer = original_tokenizer


# ============================================================
# 测试套件 7：hamming_distance / is_similar 补充
# ============================================================

class TestHammingDistanceExtra:
    """补充汉明距离和相似度测试。"""

    def test_hamming_distance_mask_applied(self):
        """验证 MASK64 被正确应用（超出 64 位的位不计入）。"""
        # 构造超出 64 位的值
        a = (1 << 64) | 0b1010  # 第 65 位 + 4 个低位
        b = 0b1010
        # MASK64 后 a 和 b 应该相同，汉明距离为 0
        assert hamming_distance(a, b) == 0

    def test_is_similar_default_threshold(self):
        """默认阈值 3。"""
        h = compute_simhash("测试文本")
        assert is_similar(h, h) is True

    def test_is_similar_custom_threshold_zero(self):
        """阈值为 0 时只有完全相同才相似。"""
        assert is_similar(0b1000, 0b1000, threshold=0) is True
        assert is_similar(0b1000, 0b1001, threshold=0) is False

    def test_find_duplicate_first_match_returned(self):
        """返回第一个匹配的候选。"""
        target = 0b1000
        candidates = [
            (10, 0b1010),  # 差 1 位，匹配
            (20, 0b1000),  # 完全匹配
        ]
        result = find_duplicate_in_iter(target, candidates, threshold=3)
        assert result is not None
        assert result[0] == 10  # 第一个匹配
