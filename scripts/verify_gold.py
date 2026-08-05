"""金标完整性校验脚本。

读取金标 JSON 文件, 校验所有约束, 输出统计报告。

校验项:
- 每个文档必须有 6 个核心字段
- 每个字段必须有 status
- present 状态的字段必须有 acceptable_evidence_spans
- span 切片校验: body[start:end] == text
- document_id 唯一
- file 字段对应的文件存在
- 公告类型/平台/split 分布统计
- 特殊场景覆盖: 联合体/多中标人/多分包
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

CORE_FIELDS = [
    "project_identifier",
    "purchaser_name",
    "winner_name",
    "amount",
    "publish_date",
    "bid_deadline",
]

VALID_STATUSES = {
    "present",
    "absent",
    "not_applicable",
    "ambiguous",
    "attachment_only",
    "unreadable",
}


def load_gold(gold_path: Path) -> list[dict]:
    data = json.loads(gold_path.read_text(encoding="utf-8"))
    # 格式1: {annotations: [{_is_meta}, {document_id, ...}, ...]}
    if isinstance(data, dict) and "annotations" in data:
        data = data["annotations"]
    # 格式2: 直接是 list 或单条 dict
    elif isinstance(data, dict):
        data = [data]
    return [d for d in data if isinstance(d, dict) and not d.get("_is_meta")]


def parse_raw_body(raw_path: Path) -> str:
    text = raw_path.read_text(encoding="utf-8")
    lines = text.split("\n", 4)
    body_full = lines[4] if len(lines) > 4 else ""
    idx = body_full.find("## ")
    return body_full[idx:] if idx >= 0 else body_full


def normalize_fields(doc: dict) -> dict[str, dict]:
    """统一两种格式: fields 为 list(旧) 或 dict(新) → dict[name, field]."""
    fields = doc.get("fields", {})
    if isinstance(fields, list):
        out: dict[str, dict] = {}
        for f in fields:
            if isinstance(f, dict) and "field_name" in f:
                out[f["field_name"]] = f
        return out
    if isinstance(fields, dict):
        return dict(fields)
    return {}


def get_status(field: dict) -> str:
    return field.get("status") or field.get("gold_status") or ""


def get_spans(field: dict) -> list[dict]:
    """兼容两种格式提取 acceptable_evidence_spans."""
    spans = field.get("acceptable_evidence_spans")
    if spans:
        return spans
    values = field.get("values", [])
    if isinstance(values, list):
        for v in values:
            if isinstance(v, dict) and v.get("acceptable_evidence_spans"):
                return v["acceptable_evidence_spans"]
    return []


def get_values(field: dict) -> list:
    values = field.get("values", [])
    if not isinstance(values, list):
        return []
    return values


def verify(gold_path: Path, raw_dir: Path, strict: bool) -> int:
    docs = load_gold(gold_path)
    errors = 0
    warnings = 0
    field_status_counter: Counter = Counter()
    type_counter: Counter = Counter()
    platform_counter: Counter = Counter()
    split_counter: Counter = Counter()
    span_pass = 0
    span_fail = 0
    doc_ids: set[str] = set()
    consortium = 0
    multi_winner = 0
    multi_lot = 0

    for doc in docs:
        doc_id = doc.get("document_id", "?")
        if doc_id in doc_ids:
            print(f"ERROR: document_id 重复 {doc_id}")
            errors += 1
        doc_ids.add(doc_id)

        fields = normalize_fields(doc)
        for fname in CORE_FIELDS:
            if fname not in fields:
                print(f"ERROR: {doc_id} 缺少字段 {fname}")
                errors += 1
                continue
            f = fields[fname]
            status = get_status(f)
            if not status:
                print(f"ERROR: {doc_id}.{fname} 缺少 status")
                errors += 1
                continue
            if status not in VALID_STATUSES:
                print(f"ERROR: {doc_id}.{fname} status 无效: {status}")
                errors += 1
                continue
            field_status_counter[status] += 1
            if status == "present" and not get_spans(f):
                print(f"WARN: {doc_id}.{fname} present 但无 evidence span")
                warnings += 1

        type_counter[doc.get("notice_type", "unknown")] += 1
        platform_counter[doc.get("source_platform", "unknown")] += 1
        split_counter[doc.get("split", "unknown")] += 1

        file_name = doc.get("file", "")
        raw_path = raw_dir / file_name if file_name else None
        if file_name and (raw_path is None or not raw_path.exists()):
            print(f"WARN: {doc_id} 文件不存在 {file_name}")
            warnings += 1

        # span 切片校验
        if raw_path is not None and raw_path.exists():
            body = parse_raw_body(raw_path)
            for fname in CORE_FIELDS:
                f = fields.get(fname, {})
                for sp in get_spans(f):
                    s, e, t = sp.get("start", 0), sp.get("end", 0), sp.get("text", "")
                    actual = body[s:e] if 0 <= s <= e <= len(body) else None
                    if actual == t:
                        span_pass += 1
                    else:
                        msg = (
                            f"span 不匹配 {doc_id}.{fname} [{s}:{e}] "
                            f"expected={t[:30]!r} actual={(actual or '')[:30]!r}"
                        )
                        if strict:
                            print(f"ERROR: {msg}")
                            errors += 1
                        else:
                            print(f"WARN: {msg}")
                            warnings += 1
                        span_fail += 1

        # 特殊场景
        winner_field = fields.get("winner_name", {})
        winner_values = get_values(winner_field)
        for v in winner_values:
            if isinstance(v, dict) and v.get("partners"):
                consortium += 1
                break
            if isinstance(v, str) and "联合体" in v:
                consortium += 1
                break
        if len(winner_values) > 1:
            multi_winner += 1

        amount_field = fields.get("amount", {})
        amount_values = get_values(amount_field)
        for v in amount_values:
            if isinstance(v, dict) and v.get("lot_id"):
                multi_lot += 1
                break
        if len(amount_values) > 1:
            multi_lot += 1

    # 报告
    print("=== 金标校验报告 ===")
    print(f"文件: {gold_path}")
    print(f"文档数: {len(docs)}")
    print(f"通过: {len(docs) - errors}")
    print(f"警告: {warnings}")
    print(f"错误: {errors}")
    print()
    print("--- 字段统计 ---")
    for s in sorted(VALID_STATUSES):
        print(f"{s}: {field_status_counter.get(s, 0)}")
    print()
    print("--- 类型分布 ---")
    for t, c in sorted(type_counter.items()):
        print(f"{t}: {c}")
    print()
    print("--- 平台分布 ---")
    for p, c in sorted(platform_counter.items()):
        line = f"{p}: {c}"
        if c == 0:
            line += " (警告: 0篇)"
        print(line)
    print()
    print("--- split 分布 ---")
    for s, c in sorted(split_counter.items()):
        print(f"{s}: {c}")
    print()
    print("--- span 切片校验 ---")
    print(f"通过: {span_pass}")
    print(f"不匹配: {span_fail}")
    print()
    print("--- 特殊场景覆盖 ---")
    print(f"联合体: {consortium}篇 (目标≥2)")
    print(f"多中标人: {multi_winner}篇 (目标≥2)")
    print(f"多分包: {multi_lot}篇 (目标≥3)")

    return 1 if errors > 0 else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="金标完整性校验脚本")
    parser.add_argument(
        "--gold",
        default="tests/fixtures/gold/gold_frozen_v1.json",
        help="金标JSON文件路径（默认tests/fixtures/gold/gold_frozen_v1.json）",
    )
    parser.add_argument(
        "--raw-dir", default="_w3_raw", help="原始文本目录（默认_w3_raw）"
    )
    parser.add_argument(
        "--strict", action="store_true", help="严格模式（span不匹配则报错而非警告）"
    )
    args = parser.parse_args()

    gold_path = Path(args.gold)
    if not gold_path.is_absolute():
        gold_path = ROOT / args.gold
    raw_dir = Path(args.raw_dir)
    if not raw_dir.is_absolute():
        raw_dir = ROOT / args.raw_dir

    if not gold_path.exists():
        print(f"ERROR: 金标文件不存在 {gold_path}")
        sys.exit(1)

    sys.exit(verify(gold_path, raw_dir, args.strict))


if __name__ == "__main__":
    main()
