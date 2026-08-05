"""K3 标注结果 → 金标 JSON 拼装脚本

v4.1 §10.4 开发集/校准集可用 K3 预标注。
本脚本调用 LLM 对原始公告进行标注，输出金标 JSON。

流程:
  原始文本 → call_extraction_llm → 提取字段+证据 → 搜索span位置 → 拼装JSON

注意: 测试集按 v4.1 §10.4 必须由人类标注员标注。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CORE_FIELDS = [
    "project_identifier",
    "purchaser_name",
    "winner_name",
    "amount",
    "publish_date",
    "bid_deadline",
]

VALID_GOLD_STATUSES = {
    "present",
    "absent",
    "not_applicable",
    "ambiguous",
    "attachment_only",
    "unreadable",
}


def parse_raw(text: str) -> tuple[dict, str]:
    """解析原始文本: 前4行元数据, 正文以第一个 ## 标记开始."""
    lines = text.split("\n", 4)
    meta: dict[str, str] = {}
    if len(lines) >= 1:
        meta["title"] = lines[0].lstrip("# ").strip()
    if len(lines) >= 2:
        meta["source_url"] = lines[1].replace("# URL:", "").strip()
    if len(lines) >= 3:
        meta["notice_type"] = lines[2].replace("# Type:", "").strip()
    if len(lines) >= 4:
        meta["fetched"] = lines[3].replace("# Fetched:", "").strip()
    body_full = lines[4] if len(lines) > 4 else ""
    idx = body_full.find("## ")
    body = body_full[idx:] if idx >= 0 else body_full
    return meta, body


def infer_platform(url: str) -> str:
    if "ccgp.gov.cn" in url:
        return "ccgp"
    if "ggzy.gov.cn" in url:
        return "ggzy"
    if "qianlima" in url:
        return "qianlima"
    if "chinabidding" in url:
        return "chinabidding"
    return "unknown"


def find_span(body: str, evidence: str) -> Optional[tuple[int, int]]:
    """在正文中搜索证据文本位置, 返回 (start, end)."""
    if not evidence:
        return None
    pos = body.find(evidence)
    if pos >= 0:
        return pos, pos + len(evidence)
    stripped = evidence.strip()
    if stripped and stripped != evidence:
        pos = body.find(stripped)
        if pos >= 0:
            return pos, pos + len(stripped)
    return None


def map_status(llm_status: str) -> str:
    """LLM field_status → 金标 status."""
    if llm_status == "multi_value":
        return "present"
    if llm_status in VALID_GOLD_STATUSES:
        return llm_status
    return "absent"


def build_field(fe: Any, body: str) -> dict:
    """将 FieldExtraction 转为金标字段格式."""
    status = map_status(fe.field_status)
    field_obj: dict[str, Any] = {"status": status, "values": [], "acceptable_evidence_spans": []}

    if status == "present":
        values: list[str] = []
        if fe.raw_value:
            values.append(fe.raw_value)
        field_obj["values"] = values

        if fe.candidate_evidences:
            ev_text = fe.candidate_evidences[0].evidence_text
            span = find_span(body, ev_text)
            if span:
                s, e = span
                actual = body[s:e]
                if actual == ev_text:
                    field_obj["acceptable_evidence_spans"] = [
                        {"start": s, "end": e, "text": ev_text}
                    ]
                else:
                    # 切片不一致, 用实际切片文本
                    field_obj["acceptable_evidence_spans"] = [
                        {"start": s, "end": e, "text": actual}
                    ]
                    field_obj["_span_mismatch"] = True
            else:
                # 找不到证据位置, 标记 unsupported
                field_obj["_unsupported_evidence"] = ev_text[:80]

    if fe.amount_type and fe.field_name == "amount":
        field_obj["amount_type"] = fe.amount_type

    return field_obj


async def annotate_one(raw_path: Path, split: str) -> Optional[dict]:
    """标注单篇公告."""
    from app.llm.extractor import call_extraction_llm

    text = raw_path.read_text(encoding="utf-8")
    meta, body = parse_raw(text)
    doc_id = raw_path.stem

    result = await call_extraction_llm(text)
    if result.error:
        print(f"  WARN: {doc_id} LLM错误: {result.error[:80]}")

    fields_map: dict[str, Any] = {}
    for fe in result.fields:
        if fe.field_name in CORE_FIELDS:
            fields_map[fe.field_name] = fe

    gold_fields: dict[str, dict] = {}
    for fname in CORE_FIELDS:
        fe = fields_map.get(fname)
        if fe is None:
            gold_fields[fname] = {
                "status": "absent",
                "values": [],
                "acceptable_evidence_spans": [],
            }
        else:
            gold_fields[fname] = build_field(fe, body)

    return {
        "document_id": doc_id,
        "file": raw_path.name,
        "title": meta.get("title", ""),
        "notice_type": meta.get("notice_type", "other"),
        "source_url": meta.get("source_url", ""),
        "source_platform": infer_platform(meta.get("source_url", "")),
        "split": split,
        "annotator": "K3",
        "annotation_version": "1.0",
        "fields": gold_fields,
    }


def load_existing(output: Path) -> dict[str, dict]:
    """读取已有标注结果（断点续标），返回 {document_id: doc}."""
    if not output.exists():
        return {}
    try:
        data = json.loads(output.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {d["document_id"]: d for d in data if isinstance(d, dict) and "document_id" in d}
        if isinstance(data, dict) and "annotations" in data:
            return {d["document_id"]: d for d in data["annotations"] if isinstance(d, dict) and "document_id" in d}
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def save_results(output: Path, results: list[dict]) -> None:
    """保存标注结果到 JSON 文件（原子覆盖，每次写入完整列表）."""
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(".tmp")
    tmp.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(output)


async def run(args: argparse.Namespace) -> None:
    input_dir = Path(args.input_dir)
    if not input_dir.is_absolute():
        input_dir = ROOT / args.input_dir
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / args.output

    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("WARN: DEEPSEEK_API_KEY 未设置，跳过 LLM 标注（不报错）")
        return

    if not input_dir.exists():
        print(f"ERROR: 输入目录不存在 {input_dir}")
        return

    files = sorted(input_dir.glob("*.txt"))
    total = len(files)

    # 断点续标: 读取已标注文档, 跳过已完成的
    existing = load_existing(output) if args.resume else {}
    if existing:
        print(f"断点续标: 已有 {len(existing)} 篇标注, 将跳过这些文档")

    todo_files = [fp for fp in files if fp.stem not in existing]
    if args.max_docs > 0:
        todo_files = todo_files[: args.max_docs]

    print(f"输入目录: {input_dir}")
    print(f"输出文件: {output}")
    print(f"目录总计: {total} 篇, 已标注: {len(existing)} 篇, 本次待标注: {len(todo_files)} 篇")

    if not todo_files:
        print("无待标注文档, 退出")
        return

    # 以已有结果为基础追加
    results: list[dict] = list(existing.values())
    success = 0
    failed = 0
    save_interval = args.save_interval

    for i, fp in enumerate(todo_files):
        print(f"[{i + 1}/{len(todo_files)}] {fp.name} ...", end=" ", flush=True)
        try:
            doc = await annotate_one(fp, args.split)
            if doc:
                results.append(doc)
                success += 1
                print("OK")
            else:
                failed += 1
                print("SKIP")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR: {exc}")

        # 增量保存: 每完成 save_interval 篇或最后一篇时落盘
        if save_interval > 0 and ((i + 1) % save_interval == 0 or i == len(todo_files) - 1):
            save_results(output, results)
            print(f"  [保存] 已落盘 {len(results)} 篇 (新增 {success}, 失败 {failed})", flush=True)

        if args.delay > 0 and i < len(todo_files) - 1:
            time.sleep(args.delay)

    save_results(output, results)
    print(f"\n完成: 新增 {success} 篇, 失败 {failed} 篇, 总计 {len(results)} 篇 → {output}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="K3 标注结果 → 金标 JSON 拼装脚本"
    )
    parser.add_argument(
        "--input-dir", default="_w4_raw", help="原始文本目录（默认_w4_raw）"
    )
    parser.add_argument(
        "--output",
        default="tests/fixtures/gold/k3_annotations_w4.json",
        help="输出金标JSON路径",
    )
    parser.add_argument(
        "--split", default="dev", help="数据集划分标记（dev/calib/test，默认dev）"
    )
    parser.add_argument(
        "--start-from", type=int, default=0, help="(已废弃,改用--resume)从第N篇开始"
    )
    parser.add_argument(
        "--max-docs", type=int, default=0, help="最多标注N篇（0=全部，默认0）"
    )
    parser.add_argument(
        "--delay", type=int, default=2, help="LLM调用间隔秒（默认2）"
    )
    parser.add_argument(
        "--resume", action="store_true", default=True, help="断点续标（默认启用，跳过已标注文档）"
    )
    parser.add_argument(
        "--no-resume", dest="resume", action="store_false", help="禁用断点续标"
    )
    parser.add_argument(
        "--save-interval", type=int, default=5, help="每N篇增量保存一次（默认5，0=仅最终保存）"
    )
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
