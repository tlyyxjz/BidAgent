"""data_migration.py 补充测试：提升覆盖率 94% -> 95%+.

覆盖未覆盖行: 67, 74, 78

策略:
- _map_notice_type(None) / _map_notice_type("") -> "tender" (行 67)
- _map_platform_type(None) / _map_platform_type("") -> "unknown" (行 74)
- _map_platform_type("commercial.com") -> "commercial" (行 78)
"""
from __future__ import annotations

import pytest

from app.utils.data_migration import (
    _content_sha256,
    _map_notice_type,
    _map_platform_type,
    _NOTICE_TYPE_MAP,
)


# ============================================================
# 测试套件 1: _map_notice_type 边界 (行 67)
# ============================================================

class TestMapNoticeType:
    """覆盖 _map_notice_type 中 raw 为空的分支."""

    def test_none_returns_tender(self):
        """行 67: None 输入返回 'tender'."""
        assert _map_notice_type(None) == "tender"

    def test_empty_string_returns_tender(self):
        """行 67: 空字符串返回 'tender'."""
        assert _map_notice_type("") == "tender"

    def test_known_english_type(self):
        """已知英文类型正确映射."""
        assert _map_notice_type("tender") == "tender"
        assert _map_notice_type("award") == "award"
        assert _map_notice_type("correction") == "correction"
        assert _map_notice_type("clarification") == "clarification"
        assert _map_notice_type("cancellation") == "cancellation"
        assert _map_notice_type("contract") == "contract"

    def test_known_chinese_type(self):
        """已知中文类型正确映射."""
        assert _map_notice_type("招标") == "tender"
        assert _map_notice_type("中标") == "award"
        assert _map_notice_type("更正") == "correction"
        assert _map_notice_type("澄清") == "clarification"
        assert _map_notice_type("废标") == "cancellation"
        assert _map_notice_type("合同") == "contract"

    def test_unknown_type_returns_other(self):
        """未知类型返回 'other'."""
        assert _map_notice_type("unknown_type") == "other"
        assert _map_notice_type("预审") == "other"
        assert _map_notice_type("随机字符串") == "other"

    def test_all_notice_type_map_entries(self):
        """验证 _NOTICE_TYPE_MAP 中所有条目正确映射."""
        for raw, expected in _NOTICE_TYPE_MAP.items():
            assert _map_notice_type(raw) == expected


# ============================================================
# 测试套件 2: _map_platform_type 边界 (行 74, 78)
# ============================================================

class TestMapPlatformType:
    """覆盖 _map_platform_type 中空输入和商业平台分支."""

    def test_none_returns_unknown(self):
        """行 74: None 输入返回 'unknown'."""
        assert _map_platform_type(None) == "unknown"

    def test_empty_string_returns_unknown(self):
        """行 74: 空字符串返回 'unknown'."""
        assert _map_platform_type("") == "unknown"

    def test_government_ccgp(self):
        """ccgp 平台返回 'government'."""
        assert _map_platform_type("ccgp.gov.cn") == "government"

    def test_government_gov(self):
        """含 'gov' 的平台返回 'government'."""
        assert _map_platform_type("www.gov.cn") == "government"
        assert _map_platform_type("example.gov") == "government"

    def test_government_ggzy(self):
        """含 'ggzy' 的平台返回 'government'."""
        assert _map_platform_type("ggzy.example.com") == "government"

    def test_government_case_insensitive(self):
        """government 关键词检测大小写不敏感."""
        assert _map_platform_type("CCGP.GOV.CN") == "government"
        assert _map_platform_type("GOV") == "government"
        assert _map_platform_type("GGZY") == "government"

    def test_commercial_platform(self):
        """行 78: 不含政府关键词的平台返回 'commercial'."""
        assert _map_platform_type("chinabidding.com.cn") == "commercial"
        assert _map_platform_type("qianlima.com") == "commercial"
        assert _map_platform_type("example.com") == "commercial"

    def test_commercial_random_string(self):
        """行 78: 随机字符串返回 'commercial'."""
        assert _map_platform_type("some-random-platform") == "commercial"
        assert _map_platform_type("bidcenter") == "commercial"

    def test_commercial_with_partial_keyword(self):
        """行 78: 部分包含关键词但不是政府平台."""
        # 'ccgp' 在 'myccgp' 中也应匹配 (substring 检测)
        assert _map_platform_type("myccgp.com") == "government"
        # 不含任何关键词
        assert _map_platform_type("commercial-platform") == "commercial"


# ============================================================
# 测试套件 3: _content_sha256 辅助函数
# ============================================================

class TestContentSha256:
    """覆盖 _content_sha256 哈希函数."""

    def test_normal_text(self):
        """正常文本返回 64 字符 hex 摘要."""
        h = _content_sha256("test content")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_empty_string(self):
        """空字符串返回 SHA256("") 的摘要."""
        h = _content_sha256("")
        assert h == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    def test_none_returns_empty_hash(self):
        """None 返回 SHA256("") 的摘要."""
        h = _content_sha256(None)
        assert h == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    def test_deterministic(self):
        """相同输入产生相同摘要."""
        assert _content_sha256("hello") == _content_sha256("hello")

    def test_different_inputs_different_hashes(self):
        """不同输入产生不同摘要."""
        assert _content_sha256("hello") != _content_sha256("world")

    def test_unicode_text(self):
        """Unicode 文本正常哈希."""
        h = _content_sha256("中文内容")
        assert len(h) == 64
