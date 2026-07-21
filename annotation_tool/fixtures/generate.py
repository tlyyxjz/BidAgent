"""
生成测试 fixture 和标注数据
从唯一原文 sample-001.txt 动态计算所有证据偏移量
确保偏移量 100% 正确：utf16_slice(text, start, end) == evidence_text

安全策略（W1-05 复查要求）：
- sample-001.txt 是权威原文，由人工创建，禁止脚本覆盖
- 脚本启动时若 sample-001.txt 不存在，立即报错退出
- 脚本只读取 sample-001.txt，重新生成派生文件：
  * _test_sample.json
  * fixtures/manifest.json
  * sample_data.js
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from common import (
    normalize_newlines, compute_sha256,
    utf16_slice, utf16_len, find_evidence_offset
)

FIXTURES_DIR = os.path.dirname(os.path.abspath(__file__))
TOOL_DIR = os.path.dirname(FIXTURES_DIR)

# ========== 原文文件路径（权威来源，只读） ==========
SAMPLE_TEXT_PATH = os.path.join(FIXTURES_DIR, 'sample-001.txt')


def read_sample_text():
    """读取唯一原文文件 sample-001.txt。

    安全策略：
    - 文件必须存在（由人工创建），不存在时报错退出
    - 文件内容只读取，不覆盖、不修改
    - 读取后做 LF 换行归一化（仅用于内存中计算偏移量）
    - 返回归一化后的文本
    """
    if not os.path.exists(SAMPLE_TEXT_PATH):
        print(f"ERROR: 原文文件不存在: {SAMPLE_TEXT_PATH}", file=sys.stderr)
        print(f"       sample-001.txt 是权威原文，必须由人工创建。", file=sys.stderr)
        print(f"       脚本不会自动创建或覆盖该文件。", file=sys.stderr)
        sys.exit(1)

    with open(SAMPLE_TEXT_PATH, 'r', encoding='utf-8') as f:
        raw_text = f.read()

    # 内存中做 LF 归一化（不写回文件）
    normalized = normalize_newlines(raw_text)

    print(f"✅ 读取原文: {SAMPLE_TEXT_PATH}")
    print(f"   长度 (UTF-16 code units): {utf16_len(normalized)}")
    print(f"   SHA256: {compute_sha256(normalized)}")
    return normalized


def generate_annotation(raw_text):
    """动态生成标注数据，所有偏移量从原文查找"""

    evidence_map = {
        'project_identifier': {
            'raw_value': 'ZFCG-2024-0315',
            'normalized_value': 'ZFCG-2024-0315',
            'evidence_texts': ['ZFCG-2024-0315'],
        },
        'purchaser_name': {
            'raw_value': '某市大数据管理局',
            'normalized_value': '某市大数据管理局',
            'evidence_texts': ['某市大数据管理局'],
        },
        'winner_name': {
            'raw_value': '上海智汇科技有限公司',
            'normalized_value': '上海智汇科技有限公司',
            'evidence_texts': ['上海智汇科技有限公司'],
        },
        'amount': {
            'raw_value': '1285.60万元',
            'normalized_value': '12856000.00',
            'amount_type': 'award',
            'currency': 'CNY',
            'original_unit': '万元',
            'tax_status': 'unknown',
            'evidence_texts': ['1285.60万元', '中标（成交）金额'],
            'evidence_roles': ['primary', 'qualifier'],
            'note': '中标金额，人民币'
        },
        'publish_date': {
            'raw_value': '2024年3月15日',
            'normalized_value': '2024-03-15',
            'evidence_texts': ['2024年3月15日'],
        },
        'bid_deadline': {
            'raw_value': '2024年3月10日',
            'normalized_value': '2024-03-10',
            'evidence_texts': ['2024年3月10日'],
        },
    }

    field_order = [
        'project_identifier', 'purchaser_name', 'winner_name',
        'amount', 'publish_date', 'bid_deadline'
    ]

    fields = []
    total_evidence = 0

    for field_name in field_order:
        info = evidence_map[field_name]
        evidence_spans = []

        for i, ev_text in enumerate(info['evidence_texts']):
            offset = find_evidence_offset(raw_text, ev_text)
            if offset is None:
                raise ValueError(f"找不到证据: {field_name} -> {repr(ev_text)}")

            start, end = offset
            role = info.get('evidence_roles', ['primary'])[i] if i < len(info.get('evidence_roles', [])) else 'primary'

            # 强制验证
            verify = utf16_slice(raw_text, start, end)
            assert verify == ev_text, f"偏移验证失败: {field_name}[{i}] 期望 {repr(ev_text)} 实际 {repr(verify)}"

            evidence_spans.append({
                'role': role,
                'start': start,
                'end': end,
                'text': ev_text
            })
            total_evidence += 1

        field_data = {
            'field_name': field_name,
            'gold_status': 'present',
            'values': [{
                'raw_value': info['raw_value'],
                'normalized_value': info.get('normalized_value'),
                'amount_type': info.get('amount_type'),
                'currency': info.get('currency'),
                'original_unit': info.get('original_unit'),
                'tax_status': info.get('tax_status'),
                'lot_id': info.get('lot_id'),
                'acceptable_evidence_spans': evidence_spans
            }],
            'note': info.get('note', '')
        }
        fields.append(field_data)

    annotation = {
        'document_id': 'sample-001',
        'annotator_id': 'A',
        'annotation_version': '1.0',
        'annotation_time': '2024-03-16T10:30:00+08:00',
        'clean_raw_text_sha256': compute_sha256(raw_text),
        'fields': fields
    }

    print(f"\n✅ 生成标注数据")
    print(f"   字段数: {len(fields)}")
    print(f"   证据总数: {total_evidence}")
    print(f"   全部偏移量验证通过")

    return annotation


def main():
    print("=" * 60)
    print("生成测试 Fixture 和标注数据")
    print("=" * 60)

    # 1. 读取原文（只读，不覆盖）
    raw_text = read_sample_text()

    # 2. 生成标注
    annotation = generate_annotation(raw_text)

    # 3. 写入 _test_sample.json（标注工具根目录）
    json_path = os.path.join(TOOL_DIR, '_test_sample.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(annotation, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 写入 JSON: {json_path}")

    # 4. 写入 fixtures/manifest.json
    manifest = {
        'sample_id': 'sample-001',
        'raw_text_file': 'sample-001.txt',
        'annotation_file': '../_test_sample.json',
        'raw_text_sha256': compute_sha256(raw_text),
        'raw_text_length_utf16': utf16_len(raw_text),
        'evidence_count': 7,
        'field_count': 6
    }
    manifest_path = os.path.join(FIXTURES_DIR, 'manifest.json')
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"✅ 写入 manifest: {manifest_path}")

    # 5. 生成 sample_data.js（前端使用，内容与 fixture 完全一致）
    sample_js_path = os.path.join(TOOL_DIR, 'sample_data.js')
    annotation_no_hash = {k: v for k, v in annotation.items() if k != 'clean_raw_text_sha256'}
    sample_js_content = f"""/**
 * Mock 示例数据（自动生成，请勿手动修改）
 * 权威来源：fixtures/sample-001.txt
 * 生成脚本：fixtures/generate.py
 * 仅用于演示和测试，不包含任何真实公告内容
 */

// ========== 示例原文（与 fixtures/sample-001.txt 完全一致） ==========
const SAMPLE_RAW_TEXT = {json.dumps(raw_text, ensure_ascii=False)};

// ========== 示例标注数据（完整的 AnnotationDocument） ==========
const SAMPLE_ANNOTATION = {json.dumps(annotation_no_hash, ensure_ascii=False, indent=4)};

// 导出到 window
if (typeof window !== 'undefined') {{
    window.SampleData = {{
        SAMPLE_RAW_TEXT,
        SAMPLE_ANNOTATION
    }};
}}
"""
    with open(sample_js_path, 'w', encoding='utf-8', newline='') as f:
        f.write(sample_js_content)
    print(f"✅ 生成 sample_data.js: {sample_js_path}")

    print("\n" + "=" * 60)
    print("全部完成！")
    print(f"原文长度 (UTF-16): {utf16_len(raw_text)} code units")
    print(f"SHA256: {compute_sha256(raw_text)}")
    print("=" * 60)


if __name__ == '__main__':
    main()
