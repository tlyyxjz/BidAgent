"""
标注工具导出 JSON 的 Pydantic Schema 校验脚本
增强版：
- 从 fixtures/sample-001.txt 读取唯一原文
- SHA256 哈希校验（原文版本不一致直接报错）
- UTF-16 code unit 切片验证（与 JavaScript String.slice 行为一致）
- 换行符规范化（LF）
"""
import sys
import os
import json

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FIXTURES_DIR = os.path.join(SCRIPT_DIR, 'fixtures')

# 导入公共工具
sys.path.insert(0, FIXTURES_DIR)
from common import (
    read_text_file_lf, compute_sha256,
    utf16_slice, utf16_len, normalize_newlines
)

# 尝试导入 GLM 真实 Schema
sys.path.insert(0, os.path.join(SCRIPT_DIR, '..'))
try:
    from backend.schemas import AnnotationDocument
    HAS_GLM_SCHEMA = True
except ImportError as e:
    print(f"警告：无法导入 GLM Schema，使用本地副本 ({e})")
    HAS_GLM_SCHEMA = False

    from pydantic import BaseModel, ConfigDict, Field, model_validator
    from typing import Literal
    from datetime import datetime

    class EvidenceSpan(BaseModel):
        model_config = ConfigDict(extra="forbid")
        role: Literal["primary", "context", "qualifier", "derivation_input", "contradiction"]
        start: int = Field(..., ge=0)
        end: int = Field(..., ge=1)
        text: str = Field(..., min_length=1)

        @model_validator(mode="after")
        def _check_range(self):
            if self.end <= self.start:
                raise ValueError(f"end({self.end}) 必须大于 start({self.start})")
            return self

    class FieldValue(BaseModel):
        model_config = ConfigDict(extra="forbid")
        raw_value: str = Field(..., min_length=1)
        normalized_value: str | None = None
        amount_type: Literal["budget", "ceiling", "award", "contract", "unit_price", "unknown"] | None = None
        currency: str | None = Field(default=None, max_length=10)
        original_unit: str | None = Field(default=None, max_length=20)
        tax_status: Literal["included", "excluded", "unknown"] | None = None
        lot_id: str | None = Field(default=None, max_length=100)
        acceptable_evidence_spans: list[EvidenceSpan] = Field(default_factory=list)

        @model_validator(mode="after")
        def _check_primary_evidence(self):
            if self.acceptable_evidence_spans:
                has_primary = any(s.role == "primary" for s in self.acceptable_evidence_spans)
                if not has_primary:
                    raise ValueError("非空证据列表必须至少包含一个 primary 证据")
            return self

    class AnnotatedField(BaseModel):
        model_config = ConfigDict(extra="forbid")
        field_name: Literal["project_identifier", "purchaser_name", "winner_name", "amount", "publish_date", "bid_deadline"]
        gold_status: Literal["present", "absent", "not_applicable", "ambiguous", "attachment_only", "unreadable"]
        values: list[FieldValue] = Field(default_factory=list)
        note: str = Field(default="")

        @model_validator(mode="after")
        def _check_values_consistency(self):
            if self.gold_status == "present":
                if not self.values:
                    raise ValueError(f"gold_status=present 时 values 不能为空 (field={self.field_name})")
            elif self.gold_status in ("absent", "not_applicable", "attachment_only", "unreadable"):
                if self.values:
                    raise ValueError(f"gold_status={self.gold_status} 时 values 必须为空 (field={self.field_name})")
            return self

    class AnnotationDocument(BaseModel):
        model_config = ConfigDict(extra="forbid")
        document_id: str = Field(..., min_length=1)
        annotator_id: str = Field(..., min_length=1)
        annotation_version: str = Field(..., min_length=1)
        annotation_time: datetime | None = None
        fields: list[AnnotatedField] = Field(..., min_length=1)

        @model_validator(mode="after")
        def _check_unique_field_names(self):
            seen = set()
            for f in self.fields:
                if f.field_name in seen:
                    raise ValueError(f"field_name 重复出现: {f.field_name}")
                seen.add(f.field_name)
            return self


def validate_evidence_offsets(doc, raw_text):
    """逐条验证证据偏移量：utf16_slice(raw_text, start, end) == evidence.text"""
    failures = []
    total = 0
    passed = 0
    out_of_bounds = 0

    text_len_utf16 = utf16_len(raw_text)

    for field in doc.fields:
        for vi, value in enumerate(field.values):
            for ei, ev in enumerate(value.acceptable_evidence_spans):
                total += 1

                # 越界检查（UTF-16 单位）
                if ev.start < 0 or ev.end > text_len_utf16:
                    out_of_bounds += 1
                    failures.append({
                        'field': field.field_name,
                        'value_index': vi,
                        'evidence_index': ei,
                        'start': ev.start,
                        'end': ev.end,
                        'expected': ev.text,
                        'actual': '<越界>',
                        'reason': f'偏移量越界: 原文长度={text_len_utf16}'
                    })
                    continue

                actual = utf16_slice(raw_text, ev.start, ev.end)
                if actual == ev.text:
                    passed += 1
                else:
                    failures.append({
                        'field': field.field_name,
                        'value_index': vi,
                        'evidence_index': ei,
                        'start': ev.start,
                        'end': ev.end,
                        'expected': ev.text,
                        'actual': actual,
                        'reason': '文本不匹配'
                    })

    return total, passed, failures, out_of_bounds


def main():
    raw_text_path = os.path.join(FIXTURES_DIR, 'sample-001.txt')
    json_path = os.path.join(SCRIPT_DIR, '_test_sample.json')

    print("=" * 60)
    print("BidAgent 标注工具 - 完整校验报告")
    print("=" * 60)
    print(f"\n使用 Schema: {'GLM 原始 backend/schemas.py' if HAS_GLM_SCHEMA else '本地副本（结构一致）'}")
    print(f"Pydantic 版本: {__import__('pydantic').__version__}")
    print(f"原文文件: {raw_text_path}")
    print(f"标注文件: {json_path}")
    print()

    # ========== 第1步：读取原文并规范化 ==========
    print("【1/4】读取原文并规范化换行")
    if not os.path.exists(raw_text_path):
        print(f"  ❌ 原文文件不存在: {raw_text_path}")
        return 1

    raw_text = read_text_file_lf(raw_text_path)
    actual_sha256 = compute_sha256(raw_text)
    print(f"  ✅ 原文读取成功")
    print(f"     长度 (UTF-16 code units): {utf16_len(raw_text)}")
    print(f"     SHA256: {actual_sha256}")
    print()

    # ========== 第2步：读取 JSON 并校验哈希 ==========
    print("【2/4】原文哈希校验")
    if not os.path.exists(json_path):
        print(f"  ❌ 标注文件不存在: {json_path}")
        return 1

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    expected_sha256 = data.get('clean_raw_text_sha256')
    if not expected_sha256:
        print("  ⚠️  标注文件中未找到 clean_raw_text_sha256，跳过哈希校验")
        hash_ok = True
    elif expected_sha256 != actual_sha256:
        print(f"  ❌ 哈希不匹配！")
        print(f"     期望: {expected_sha256}")
        print(f"     实际: {actual_sha256}")
        print(f"     原因: 原文版本与标注数据不一致，偏移量必然错误")
        print(f"     停止继续验证偏移量")
        hash_ok = False
    else:
        print(f"  ✅ 哈希匹配，原文版本一致")
        hash_ok = True
    print()

    # ========== 第3步：Pydantic Schema 结构校验 ==========
    print("【3/4】Pydantic Schema 结构校验")

    # 去掉测试元数据字段再做 Schema 校验
    schema_data = {k: v for k, v in data.items() if k != 'clean_raw_text_sha256'}

    try:
        doc = AnnotationDocument.model_validate(schema_data)
        print("  ✅ Schema 结构校验通过")
        print(f"     - document_id: {doc.document_id}")
        print(f"     - 字段数量: {len(doc.fields)}")

        status_counts = {}
        total_evidence = 0
        for field in doc.fields:
            status_counts[field.gold_status] = status_counts.get(field.gold_status, 0) + 1
            for v in field.values:
                total_evidence += len(v.acceptable_evidence_spans)

        print(f"     - 状态分布: {status_counts}")
        print(f"     - 证据片段总数: {total_evidence}")
        schema_ok = True
    except Exception as e:
        print(f"  ❌ Schema 校验失败")
        print(f"     错误: {e}")
        schema_ok = False
        doc = None
    print()

    # ========== 第4步：证据偏移量切片验证 ==========
    print("【4/4】证据偏移量 UTF-16 切片验证")

    if not hash_ok or not schema_ok or doc is None:
        print("  ⏭️  跳过（哈希或 Schema 校验未通过）")
        evidence_ok = False
    else:
        total, passed, failures, out_of_bounds = validate_evidence_offsets(doc, raw_text)
        print(f"  证据总数: {total}")
        print(f"  通过: {passed}")
        print(f"  失败: {len(failures)}")
        print(f"  越界: {out_of_bounds}")

        if failures:
            for f in failures:
                print(f"\n  ❌ {f['field']}[{f['value_index']}].evidence[{f['evidence_index']}]")
                print(f"     start={f['start']}, end={f['end']}")
                print(f"     期望: {repr(f['expected'])}")
                print(f"     实际: {repr(f['actual'])}")
                print(f"     原因: {f['reason']}")
            evidence_ok = False
        else:
            print("  ✅ 全部证据偏移量切片验证通过")
            evidence_ok = True
    print()

    # ========== 总结 ==========
    print("=" * 60)
    print("校验结论")
    print("=" * 60)
    print(f"  原文哈希校验:   {'✅ 通过' if hash_ok else '❌ 失败'}")
    print(f"  Schema 结构校验: {'✅ 通过' if schema_ok else '❌ 失败'}")
    print(f"  证据切片验证:   {'✅ 全部通过' if evidence_ok else '❌ 存在失败'}")

    all_ok = hash_ok and schema_ok and evidence_ok
    if all_ok:
        print("\n  总体结论：✅ 全部校验通过")
        exit_code = 0
    else:
        print("\n  总体结论：❌ 校验失败，需要修正")
        exit_code = 1

    print("=" * 60)
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
