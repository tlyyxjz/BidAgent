"""BidAgent W1-09 仲裁辅助脚本。

对比 A/B 两位标注员对同一公告的 JSON 标注，逐字段输出差异清单，
辅助仲裁员快速定位分歧并裁决。

用法：
    python scripts/arbitration_helper.py <annotator_a.json> <annotator_b.json>

输出：
    1. 控制台打印分歧清单
    2. 同目录生成 *_arbitration.md 报告
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# 六类核心字段
CORE_FIELDS = [
    "project_identifier",
    "purchaser_name",
    "winner_name",
    "amount",
    "publish_date",
    "bid_deadline",
]


def load_annotation(path: str | Path) -> dict[str, Any]:
    """加载 JSON 标注文件。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"标注文件不存在: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def extract_field_map(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """把 fields 列表转为 {field_name: field_dict} 映射。"""
    return {f["field_name"]: f for f in doc.get("fields", [])}


def normalize_values(field: dict[str, Any]) -> set[str]:
    """提取字段值的归一化集合（用于对比）。"""
    values = field.get("values", [])
    result = set()
    for v in values:
        # 优先用 normalized_value，其次 raw_value
        val = v.get("normalized_value") or v.get("raw_value") or ""
        if val:
            result.add(val.strip())
    return result


def compare_field(
    fname: str,
    field_a: dict[str, Any] | None,
    field_b: dict[str, Any] | None,
) -> dict[str, Any]:
    """对比单个字段的 A/B 标注差异。"""
    result = {
        "field_name": fname,
        "has_diff": False,
        "status_a": None,
        "status_b": None,
        "status_diff": False,
        "value_diff": False,
        "only_in_a": [],
        "only_in_b": [],
        "evidence_count_a": 0,
        "evidence_count_b": 0,
        "evidence_diff": False,
    }

    if field_a is None and field_b is None:
        return result
    if field_a is None:
        result["has_diff"] = True
        result["status_b"] = field_b.get("gold_status")
        result["status_diff"] = True
        return result
    if field_b is None:
        result["has_diff"] = True
        result["status_a"] = field_a.get("gold_status")
        result["status_diff"] = True
        return result

    status_a = field_a.get("gold_status")
    status_b = field_b.get("gold_status")
    result["status_a"] = status_a
    result["status_b"] = status_b

    if status_a != status_b:
        result["has_diff"] = True
        result["status_diff"] = True

    # 值集合对比
    values_a = normalize_values(field_a)
    values_b = normalize_values(field_b)
    only_a = sorted(values_a - values_b)
    only_b = sorted(values_b - values_a)
    result["only_in_a"] = only_a
    result["only_in_b"] = only_b
    if only_a or only_b:
        result["has_diff"] = True
        result["value_diff"] = True

    # 证据数量对比
    ev_a = sum(len(v.get("acceptable_evidence_spans", [])) for v in field_a.get("values", []))
    ev_b = sum(len(v.get("acceptable_evidence_spans", [])) for v in field_b.get("values", []))
    result["evidence_count_a"] = ev_a
    result["evidence_count_b"] = ev_b
    if ev_a != ev_b:
        result["has_diff"] = True
        result["evidence_diff"] = True

    return result


def compare_documents(doc_a: dict[str, Any], doc_b: dict[str, Any]) -> list[dict[str, Any]]:
    """对比两份标注文档，返回六类字段的差异列表。"""
    map_a = extract_field_map(doc_a)
    map_b = extract_field_map(doc_b)

    id_a = doc_a.get("document_id", "")
    id_b = doc_b.get("document_id", "")
    if id_a != id_b:
        print(f"⚠️  警告: document_id 不一致 A={id_a!r} B={id_b!r}")

    diffs = []
    for fname in CORE_FIELDS:
        field_a = map_a.get(fname)
        field_b = map_b.get(fname)
        diff = compare_field(fname, field_a, field_b)
        diffs.append(diff)
    return diffs


def format_diff_report(diffs: list[dict[str, Any]], doc_a_id: str, doc_b_id: str) -> str:
    """格式化差异报告为 Markdown。"""
    lines = []
    lines.append("# W1-09 仲裁辅助报告")
    lines.append("")
    lines.append(f"- 标注员 A: {doc_a_id}")
    lines.append(f"- 标注员 B: {doc_b_id}")
    lines.append("")

    total_diff = sum(1 for d in diffs if d["has_diff"])
    lines.append(f"## 概览: {total_diff}/{len(diffs)} 字段有分歧")
    lines.append("")

    if total_diff == 0:
        lines.append("✅ A/B 标注完全一致，无需仲裁。")
        return "\n".join(lines)

    lines.append("## 分歧详情")
    lines.append("")

    for d in diffs:
        if not d["has_diff"]:
            continue
        fname = d["field_name"]
        lines.append(f"### {fname}")
        lines.append("")

        if d["status_diff"]:
            lines.append(f"- **状态分歧**: A={d['status_a']} vs B={d['status_b']}")
        else:
            lines.append(f"- 状态一致: {d['status_a']}")

        if d["value_diff"]:
            if d["only_in_a"]:
                lines.append(f"- **A 独有值**: {d['only_in_a']}")
            if d["only_in_b"]:
                lines.append(f"- **B 独有值**: {d['only_in_b']}")

        if d["evidence_diff"]:
            lines.append(f"- **证据数分歧**: A={d['evidence_count_a']} vs B={d['evidence_count_b']}")

        lines.append("")
        lines.append("> 仲裁决定: ___________（填 A / B / 合并 / 其他）")
        lines.append("")

    consistent = [d["field_name"] for d in diffs if not d["has_diff"]]
    if consistent:
        lines.append("## 一致字段")
        lines.append("")
        for f in consistent:
            lines.append(f"- ✅ {f}")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) != 3:
        print("用法: python scripts/arbitration_helper.py <annotator_a.json> <annotator_b.json>")
        return 1

    path_a = sys.argv[1]
    path_b = sys.argv[2]

    doc_a = load_annotation(path_a)
    doc_b = load_annotation(path_b)

    diffs = compare_documents(doc_a, doc_b)

    print("=" * 60)
    print("W1-09 仲裁辅助 - A/B 标注差异对比")
    print("=" * 60)
    print(f"文档 A: {doc_a.get('document_id', '?')} (标注员: {doc_a.get('annotator_id', '?')})")
    print(f"文档 B: {doc_b.get('document_id', '?')} (标注员: {doc_b.get('annotator_id', '?')})")
    print()

    total_diff = sum(1 for d in diffs if d["has_diff"])
    print(f"分歧字段: {total_diff}/{len(diffs)}")
    print()

    for d in diffs:
        flag = "❌" if d["has_diff"] else "✅"
        fname = d["field_name"]
        detail = ""
        if d["status_diff"]:
            detail = f" 状态: A={d['status_a']} vs B={d['status_b']}"
        elif d["value_diff"]:
            detail = f" 值差异: A独有={d['only_in_a']} B独有={d['only_in_b']}"
        elif d["evidence_diff"]:
            detail = f" 证据数: A={d['evidence_count_a']} vs B={d['evidence_count_b']}"
        print(f"  {flag} {fname}{detail}")

    report = format_diff_report(diffs, doc_a.get("annotator_id", "A"), doc_b.get("annotator_id", "B"))
    out_path = Path(path_a).parent / f"{Path(path_a).stem}_vs_{Path(path_b).stem}_arbitration.md"
    out_path.write_text(report, encoding="utf-8")
    print()
    print(f"报告已生成: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
