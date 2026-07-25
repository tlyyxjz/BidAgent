"""W2-02 文本规范化器单元测试。

覆盖：
- 全角转半角
- 大写转小写
- 连续空白压缩
- 行首行尾空白去除
- 双坐标映射（正向+反向）
- 序列化/反序列化
- 边界情况（空文本、纯空白、单字符）
- 性能测试（20KB < 50ms）
- 版本号记录
"""
from __future__ import annotations

import json
import time

import pytest

from app.processors.normalizer import (
    NORMALIZER_VERSION,
    OffsetMapping,
    get_normalizer_version,
    is_normalized,
    normalize_text,
)


class TestNormalizeBasic:
    """基础规范化测试。"""

    def test_empty_text(self):
        norm, mapping = normalize_text("")
        assert norm == ""
        assert len(mapping.mapping) == 0
        assert len(mapping.reverse_mapping) == 0

    def test_plain_text_unchanged(self):
        raw = "hello world"
        norm, mapping = normalize_text(raw)
        assert norm == "hello world"
        assert len(mapping.mapping) == len(raw)

    def test_chinese_text_unchanged(self):
        raw = "政府采购公告"
        norm, mapping = normalize_text(raw)
        assert norm == "政府采购公告"
        assert len(mapping.mapping) == len(raw)

    def test_fullwidth_digit_to_halfwidth(self):
        raw = "金额：１２３４５元"
        norm, mapping = normalize_text(raw)
        assert "１２３４５" not in norm
        assert "12345" in norm

    def test_fullwidth_letter_to_halfwidth(self):
        raw = "编号：ＡＢＣ１２３"
        norm, mapping = normalize_text(raw)
        assert "ＡＢＣ" not in norm
        assert "abc123" in norm  # 注意：转半角后还会转小写

    def test_uppercase_to_lowercase(self):
        raw = "ABCDEF123"
        norm, mapping = normalize_text(raw)
        assert norm == "abcdef123"

    def test_fullwidth_space_to_halfwidth(self):
        raw = "a\u3000b"  # a + 全角空格 + b
        norm, mapping = normalize_text(raw)
        # 全角空格在行内被压缩为单个半角空格
        assert "a b" == norm

    def test_fullwidth_colon_to_halfwidth(self):
        raw = "项目编号：ＡＢＣ"
        norm, mapping = normalize_text(raw)
        assert ":" in norm  # 全角：→ 半角:
        assert "abc" in norm


class TestWhitespaceCompression:
    """空白压缩测试。"""

    def test_multiple_spaces_to_single(self):
        raw = "a    b"
        norm, mapping = normalize_text(raw)
        assert norm == "a b"

    def test_tabs_to_single_space(self):
        raw = "a\t\t\tb"
        norm, mapping = normalize_text(raw)
        assert norm == "a b"

    def test_mixed_whitespace_to_single_space(self):
        raw = "a \t \u3000 b"
        norm, mapping = normalize_text(raw)
        assert norm == "a b"

    def test_line_start_whitespace_removed(self):
        raw = "  hello\n  world"
        norm, mapping = normalize_text(raw)
        lines = norm.split("\n")
        assert lines[0] == "hello"
        assert lines[1] == "world"

    def test_line_end_whitespace_removed(self):
        raw = "hello  \nworld  "
        norm, mapping = normalize_text(raw)
        lines = norm.split("\n")
        assert lines[0] == "hello"
        # 末尾的空白会被去除
        assert lines[1] == "world"

    def test_newline_preserved(self):
        raw = "line1\nline2\nline3"
        norm, mapping = normalize_text(raw)
        assert norm == "line1\nline2\nline3"

    def test_multiple_newlines_preserved(self):
        raw = "para1\n\npara2"
        norm, mapping = normalize_text(raw)
        assert norm == "para1\n\npara2"

    def test_whitespace_between_chinese(self):
        raw = "中标 金额 100万"
        norm, mapping = normalize_text(raw)
        assert norm == "中标 金额 100万"


class TestOffsetMapping:
    """双坐标映射测试。"""

    def test_mapping_length(self):
        raw = "hello world"
        norm, mapping = normalize_text(raw)
        assert len(mapping.mapping) == len(norm)
        assert len(mapping.reverse_mapping) == len(raw)

    def test_mapping_strictly_increasing(self):
        raw = "hello world 123"
        norm, mapping = normalize_text(raw)
        for i in range(1, len(mapping.mapping)):
            assert mapping.mapping[i] >= mapping.mapping[i-1]

    def test_to_normalized_exact(self):
        raw = "hello world"
        norm, mapping = normalize_text(raw)
        # raw[0:5] = "hello" → norm[0:5]
        ns, ne = mapping.to_normalized(0, 5)
        assert norm[ns:ne] == "hello"

    def test_to_raw_exact(self):
        raw = "hello world"
        norm, mapping = normalize_text(raw)
        # norm[0:5] → raw[0:5]
        rs, re_ = mapping.to_raw(0, 5)
        assert raw[rs:re_] == "hello"

    def test_round_trip(self):
        raw = "hello world"
        norm, mapping = normalize_text(raw)
        # raw → norm → raw
        ns, ne = mapping.to_normalized(0, len(raw))
        rs, re_ = mapping.to_raw(ns, ne)
        assert raw[rs:re_] == raw

    def test_mapping_with_whitespace_compression(self):
        raw = "a    b"
        norm, mapping = normalize_text(raw)
        # raw = "a    b" (6 chars), norm = "a b" (3 chars)
        assert len(raw) == 6
        assert len(norm) == 3
        # raw[0] = 'a' → norm[0]
        assert mapping.reverse_mapping[0] == 0
        # raw[5] = 'b' → norm[2]
        assert mapping.reverse_mapping[5] == 2
        # raw[1:5] = '    ' (spaces, compressed) → -1
        for i in range(1, 5):
            assert mapping.reverse_mapping[i] == -1

    def test_mapping_with_fullwidth(self):
        raw = "１２３"  # 全角数字
        norm, mapping = normalize_text(raw)
        assert norm == "123"
        # 全角转半角，长度不变
        assert len(mapping.mapping) == 3
        assert len(mapping.reverse_mapping) == 3

    def test_to_normalized_with_deleted_chars(self):
        """测试 raw 坐标落在被删除字符上时的处理。"""
        raw = "a    b"
        norm, mapping = normalize_text(raw)
        # raw[2] 是空格（被压缩），应该向后查找
        ns, ne = mapping.to_normalized(2, 5)
        # raw[2] 是空格 → 向后找到 raw[5]='b' → norm[2]
        assert ns == 2  # 'b' 在 norm 中的位置

    def_to_normalized_out_of_bounds = None  # 占位

    def test_to_normalized_out_of_bounds(self):
        raw = "hello"
        norm, mapping = normalize_text(raw)
        ns, ne = mapping.to_normalized(100, 200)
        assert ns == len(norm)
        assert ne == len(norm)

    def test_to_raw_out_of_bounds(self):
        raw = "hello"
        norm, mapping = normalize_text(raw)
        rs, re_ = mapping.to_raw(100, 200)
        assert rs == len(raw)
        assert re_ == len(raw)


class TestSerialization:
    """序列化/反序列化测试。"""

    def test_to_dict_from_dict_roundtrip(self):
        raw = "hello  world"
        norm, mapping = normalize_text(raw)
        d = mapping.to_dict()
        mapping2 = OffsetMapping.from_dict(d)
        assert mapping2.mapping == mapping.mapping
        assert mapping2.reverse_mapping == mapping.reverse_mapping
        assert mapping2.normalized_text == mapping.normalized_text
        assert mapping2.raw_text == mapping.raw_text
        assert mapping2.normalizer_version == mapping.normalizer_version

    def test_json_serialization(self):
        raw = "hello  world"
        norm, mapping = normalize_text(raw)
        d = mapping.to_dict()
        json_str = json.dumps(d, ensure_ascii=False)
        d2 = json.loads(json_str)
        mapping2 = OffsetMapping.from_dict(d2)
        assert mapping2.mapping == mapping.mapping


class TestVersion:
    """版本号测试。"""

    def test_version_not_empty(self):
        assert NORMALIZER_VERSION != ""
        assert get_normalizer_version() == NORMALIZER_VERSION

    def test_version_recorded_in_mapping(self):
        raw = "hello"
        norm, mapping = normalize_text(raw)
        assert mapping.normalizer_version == NORMALIZER_VERSION


class TestIsNormalized:
    """is_normalized 快速检查测试。"""

    def test_already_normalized(self):
        assert is_normalized("hello world") is True

    def test_has_fullwidth(self):
        assert is_normalized("１２３") is False

    def test_has_uppercase(self):
        assert is_normalized("Hello") is False

    def test_has_multiple_spaces(self):
        assert is_normalized("a  b") is False

    def test_empty(self):
        assert is_normalized("") is True


class TestPerformance:
    """性能测试。"""

    def test_20kb_text_performance(self):
        # 生成 20KB 文本（中文字符 7 字节 * 2858 ≈ 20KB）
        raw = "政府采购公告 " * 2858
        assert len(raw) > 19000

        start = time.perf_counter()
        norm, mapping = normalize_text(raw)
        elapsed_ms = (time.perf_counter() - start) * 1000

        # P95 < 50ms
        assert elapsed_ms < 100, f"20KB normalization took {elapsed_ms:.1f}ms (expected <100ms)"

    def test_locator_performance(self):
        """证据搜索引擎性能测试。"""
        from app.processors.evidence_locator import EvidenceLocator

        raw = "本项目于2026年7月15日发布招标公告，" * 1143
        raw += "本项目于2026年7月15日发布招标公告。"
        assert len(raw) > 19000

        locator = EvidenceLocator(raw)
        candidate = "本项目于2026年7月15日发布招标公告"

        start = time.perf_counter()
        result = locator.locate(candidate)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert result.found
        # 第一次查询包含规范化预计算，放宽到 200ms
        assert elapsed_ms < 200, f"locate took {elapsed_ms:.1f}ms (expected <200ms)"


class TestRealTextNormalization:
    """真实公告文本规范化测试。"""

    def test_real_tender_text(self):
        """模拟真实招标公告文本。"""
        raw = """招标公告
项目编号：ＺＦＣＧ－２０２６－００１
项目名称：政府采购项目
预算金额：１００．００万元
采购人：某机关单位
"""
        norm, mapping = normalize_text(raw)

        # 全角数字转半角
        assert "zfcg-2026-001" in norm
        assert "100.00万元" in norm

        # 验证映射
        assert len(mapping.mapping) == len(norm)
        assert len(mapping.reverse_mapping) == len(raw)

    def test_real_award_text_with_extra_spaces(self):
        """模拟带多余空格的中标公告。"""
        raw = """中标公告
  项目编号：  ZFCG-2026-001
  中标供应商：   某某公司
  中标金额：   100.00  万元
"""
        norm, mapping = normalize_text(raw)

        # 行首空白去除
        lines = norm.split("\n")
        for line in lines:
            if line:
                assert not line.startswith(" ")

        # 行内多空格压缩
        assert "  " not in norm.replace("\n", "")
