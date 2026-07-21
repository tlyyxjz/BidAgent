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

# 强制导入 GLM 真实 Schema，不得使用本地副本产生假通过
# 使用 importlib 直接加载 backend/schemas.py 和 backend/enums.py，
# 绕过 backend/__init__.py 的其他模块导入（models/bootstrap/extractors 等可能依赖 app.config），
# 但仍使用真实 GLM Schema 定义，不创建本地副本。
import importlib
import importlib.util

REPO_ROOT = os.path.join(SCRIPT_DIR, '..')
BACKEND_DIR = os.path.join(REPO_ROOT, 'backend')
ENUMS_PATH = os.path.join(BACKEND_DIR, 'enums.py')
SCHEMAS_PATH = os.path.join(BACKEND_DIR, 'schemas.py')


def _load_real_schema():
    """直接从 backend/schemas.py 加载真实 AnnotationDocument。

    绕过 backend/__init__.py 是为了规避 models/bootstrap/extractors 等模块
    对 app.config 的依赖（标注工具校验只需 Schema 定义本身）。
    仍然加载真实 GLM 代码，绝不创建本地副本。
    """
    if not os.path.exists(ENUMS_PATH):
        print(f"错误：找不到 GLM 真实 enums.py: {ENUMS_PATH}", file=sys.stderr)
        print("       不得使用本地 Schema 副本产生假通过。", file=sys.stderr)
        print("       请在仓库根目录运行：python annotation_tool/validate_schema.py", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(SCHEMAS_PATH):
        print(f"错误：找不到 GLM 真实 schemas.py: {SCHEMAS_PATH}", file=sys.stderr)
        print("       不得使用本地 Schema 副本产生假通过。", file=sys.stderr)
        print("       请在仓库根目录运行：python annotation_tool/validate_schema.py", file=sys.stderr)
        sys.exit(1)

    # 先加载 backend.enums 模块（schemas.py 依赖它）
    # 用临时包名 _ba_enums 加载，避免触发 backend/__init__.py
    spec_enums = importlib.util.spec_from_file_location('_ba_enums', ENUMS_PATH)
    enums_mod = importlib.util.module_from_spec(spec_enums)
    sys.modules['_ba_enums'] = enums_mod
    try:
        spec_enums.loader.exec_module(enums_mod)
    except Exception as e:
        print(f"错误：加载 GLM 真实 enums.py 失败 ({e})", file=sys.stderr)
        print("       不得使用本地 Schema 副本产生假通过。", file=sys.stderr)
        sys.exit(1)

    # 让 schemas.py 的 `from backend.enums import ...` 解析到 _ba_enums
    # 创建一个假的 backend.enums 包模块指向真实 enums_mod
    import types
    fake_backend = types.ModuleType('backend')
    fake_backend.__path__ = [BACKEND_DIR]
    sys.modules['backend'] = fake_backend
    sys.modules['backend.enums'] = enums_mod

    # 加载 backend.schemas
    spec_schemas = importlib.util.spec_from_file_location('backend.schemas', SCHEMAS_PATH)
    schemas_mod = importlib.util.module_from_spec(spec_schemas)
    sys.modules['backend.schemas'] = schemas_mod
    try:
        spec_schemas.loader.exec_module(schemas_mod)
    except Exception as e:
        print(f"错误：加载 GLM 真实 schemas.py 失败 ({e})", file=sys.stderr)
        print("       不得使用本地 Schema 副本产生假通过。", file=sys.stderr)
        sys.exit(1)

    return schemas_mod.AnnotationDocument


try:
    AnnotationDocument = _load_real_schema()
except Exception as e:
    print(f"错误：无法导入 GLM 真实 Schema ({e})", file=sys.stderr)
    print("       不得使用本地 Schema 副本产生假通过。", file=sys.stderr)
    print("       请在仓库根目录运行：python annotation_tool/validate_schema.py", file=sys.stderr)
    sys.exit(1)


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
    print(f"\n使用 Schema: GLM 原始 backend/schemas.py（不得使用本地副本）")
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
