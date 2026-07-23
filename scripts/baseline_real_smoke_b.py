"""W1-06 B 组 Direct LLM Baseline 真实数据冒烟测试。

与 baseline_real_smoke.py（A 组）的区别：
- A 组（v1.1）：不要求 evidence_text，公平对比 LLM 抽取能力
- B 组（v2.0）：要求每个值输出 evidence_text，用于验证证据引用能力

目标：
- 验证 B 组提示词能否让 LLM 输出 evidence_text
- 对比 A/B 组的无依据输出率（A 组预期 100%，B 组应显著下降）
- 为 W1-07 v2 评测指标提供有区分度的数据

输出：
- data/validation/baseline_real_b/records.jsonl   逐篇抽取记录
- data/validation/baseline_real_b/report.json     汇总报告（含 evidence_text 统计）

用法：
    python scripts/baseline_real_smoke_b.py
    python scripts/baseline_real_smoke_b.py --concurrency 2
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# 项目根
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 加载 .env
ENV_PATH = _PROJECT_ROOT / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)

from backend.extractors import (  # noqa: E402
    DirectLLMBaseline,
    PROMPT_VERSION_B,
    save_records_jsonl,
)
from backend.llm_client import OpenAICompatibleClient  # noqa: E402

# ==== 配置 ====
SEED_DIR = Path(r"C:\Users\Lenovo\Desktop\标注_对比\BidAgent_金标种子公告")

OUTPUT_DIR = _PROJECT_ROOT / "data" / "validation" / "baseline_real_b"
RECORDS_PATH = OUTPUT_DIR / "records.jsonl"
REPORT_PATH = OUTPUT_DIR / "report.json"

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")
TIMEOUT_SECONDS = float(os.environ.get("LLM_TIMEOUT_SECONDS", "60"))
MAX_RETRIES = 2


def load_seed_notices() -> list[tuple[str, str, str | None]]:
    """加载桌面 BidAgent_金标种子公告/ 下的所有真实公告 TXT。"""
    if not SEED_DIR.exists():
        print(f"[ERROR] 种子公告目录不存在: {SEED_DIR}")
        return []

    notices: list[tuple[str, str, str | None]] = []
    for txt_file in sorted(SEED_DIR.glob("*.txt")):
        if txt_file.name.startswith("00_"):
            continue

        content = txt_file.read_text(encoding="utf-8").strip()
        if not content:
            print(f"  [跳过] {txt_file.name} 内容为空")
            continue

        doc_id = txt_file.stem
        name_lower = txt_file.name.lower()
        if "tender" in name_lower:
            notice_type = "tender"
        elif "award" in name_lower:
            notice_type = "award"
        elif "correction" in name_lower:
            notice_type = "correction"
        else:
            notice_type = None

        notices.append((doc_id, content, notice_type))

    return notices


async def run_baseline(
    notices: list[tuple[str, str, str | None]],
    concurrency: int,
) -> tuple[list[Any], dict[str, Any]]:
    """用真实 DeepSeek API 跑 B 组 Baseline 抽取。"""
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("LLM_API_KEY", "")
    if not api_key:
        print("[ERROR] 未找到 DEEPSEEK_API_KEY 或 LLM_API_KEY")
        return [], {"error": "missing_api_key"}

    print(f"\nAPI Key: <redacted, len={len(api_key)}>")
    print(f"base_url: {DEEPSEEK_BASE_URL}")
    print(f"model: {DEEPSEEK_MODEL}")
    print(f"PROMPT_VERSION: {PROMPT_VERSION_B} (B 组，要求 evidence_text)")
    print(f"max_retries: {MAX_RETRIES}")
    print(f"timeout: {TIMEOUT_SECONDS}s")
    print(f"concurrency: {concurrency}")
    print(f"公告数: {len(notices)}")

    client = OpenAICompatibleClient(
        base_url=DEEPSEEK_BASE_URL,
        api_key=api_key,
        model=DEEPSEEK_MODEL,
        timeout_seconds=TIMEOUT_SECONDS,
    )

    baseline = DirectLLMBaseline(
        client=client,
        model_identifier=DEEPSEEK_MODEL,
        prompt_version=PROMPT_VERSION_B,
        max_retries=MAX_RETRIES,
    )

    started_total = time.monotonic()
    records = await baseline.extract_batch(notices, concurrency=concurrency)
    elapsed_total = int((time.monotonic() - started_total) * 1000)

    await client.close()

    meta = {
        "model_identifier": DEEPSEEK_MODEL,
        "prompt_version": PROMPT_VERSION_B,
        "base_url": DEEPSEEK_BASE_URL,
        "timeout_seconds": TIMEOUT_SECONDS,
        "max_retries": MAX_RETRIES,
        "concurrency": concurrency,
        "total_elapsed_ms": elapsed_total,
        "notice_count": len(notices),
    }
    return records, meta


def build_report(
    records: list[Any],
    meta: dict[str, Any],
) -> dict[str, Any]:
    """构建汇总报告（含 evidence_text 统计）。"""

    per_doc: list[dict[str, Any]] = []
    success_count = 0
    fail_count = 0
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_tokens = 0
    total_latency_ms = 0
    error_cases: list[dict[str, Any]] = []

    # B 组特有：evidence_text 统计
    total_values = 0
    values_with_evidence = 0
    values_without_evidence = 0

    for r in records:
        is_success = bool(r.success)
        if is_success:
            success_count += 1
        else:
            fail_count += 1

        total_prompt_tokens += r.prompt_tokens or 0
        total_completion_tokens += r.completion_tokens or 0
        total_tokens += r.total_tokens or 0
        total_latency_ms += r.latency_ms or 0

        doc_result: dict[str, Any] = {
            "document_id": r.document_id,
            "success": is_success,
            "latency_ms": r.latency_ms,
            "prompt_tokens": r.prompt_tokens,
            "completion_tokens": r.completion_tokens,
            "total_tokens": r.total_tokens,
            "model_identifier": r.model_identifier,
            "prompt_version": r.prompt_version,
        }

        if is_success and r.output:
            field_details = []
            for f in r.output.fields:
                values_summary = []
                for v in f.values:
                    total_values += 1
                    has_ev = bool((v.evidence_text or "").strip())
                    if has_ev:
                        values_with_evidence += 1
                    else:
                        values_without_evidence += 1
                    values_summary.append({
                        "raw_value": v.raw_value,
                        "normalized_value": v.normalized_value,
                        "amount_type": v.amount_type,
                        "evidence_text": v.evidence_text,
                        "has_evidence": has_ev,
                    })
                field_details.append({
                    "field_name": f.field_name,
                    "support_level": f.support_level,
                    "value_count": len(f.values),
                    "values": values_summary,
                })
            doc_result["field_count"] = len(r.output.fields)
            doc_result["fields"] = field_details
        else:
            doc_result["error_message"] = r.error_message
            error_cases.append({
                "document_id": r.document_id,
                "error_message": r.error_message,
                "latency_ms": r.latency_ms,
            })

        per_doc.append(doc_result)

    report = {
        "description": "W1-06 B 组 Direct LLM Baseline 真实数据冒烟测试报告",
        "prompt_group": "B (v2.0, 要求 evidence_text)",
        "generated_at": datetime.now().isoformat(),
        "compliance": {
            "no_full_key_in_report": True,
            "no_raw_response_in_report": True,
            "env_not_committed": True,
        },
        "config": {
            "model_identifier": meta.get("model_identifier"),
            "prompt_version": meta.get("prompt_version"),
            "base_url": meta.get("base_url"),
            "timeout_seconds": meta.get("timeout_seconds"),
            "max_retries": meta.get("max_retries"),
            "concurrency": meta.get("concurrency"),
        },
        "summary": {
            "notice_count": meta.get("notice_count", 0),
            "success_count": success_count,
            "fail_count": fail_count,
            "total_elapsed_ms": meta.get("total_elapsed_ms", 0),
            "total_latency_ms": total_latency_ms,
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "total_tokens": total_tokens,
            "avg_latency_ms": round(total_latency_ms / max(len(records), 1), 1),
            "avg_tokens_per_doc": round(total_tokens / max(len(records), 1), 1),
            # B 组特有：evidence_text 统计
            "total_values": total_values,
            "values_with_evidence": values_with_evidence,
            "values_without_evidence": values_without_evidence,
            "evidence_coverage_rate": round(
                values_with_evidence / max(total_values, 1), 4
            ),
        },
        "per_document": per_doc,
        "error_cases": error_cases,
    }
    return report


async def main() -> int:
    parser = argparse.ArgumentParser(description="W1-06 B 组真实数据冒烟测试")
    parser.add_argument(
        "--concurrency", type=int, default=2,
        help="并发度（默认 2，避免限流）",
    )
    args = parser.parse_args()

    print("=" * 70)
    print("W1-06 B 组 Direct LLM Baseline 真实数据冒烟测试")
    print(f"时间: {datetime.now().isoformat()}")
    print(f"提示词: B 组 v2.0（要求 evidence_text）")
    print(f"种子公告目录: {SEED_DIR}")
    print(f"输出目录: {OUTPUT_DIR}")
    print("=" * 70)

    notices = load_seed_notices()
    if not notices:
        print("\n[ERROR] 未找到任何真实公告文件")
        return 2

    print(f"\n加载了 {len(notices)} 篇真实公告:")
    for doc_id, _, ntype in notices:
        print(f"  - {doc_id} (type={ntype})")

    records, meta = await run_baseline(notices, concurrency=args.concurrency)
    if not records:
        print("\n[ERROR] Baseline 运行失败，无记录")
        return 2

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    saved_count = save_records_jsonl(records, RECORDS_PATH)
    print(f"\n[JSONL] 保存 {saved_count} 条记录到 {RECORDS_PATH}")

    report = build_report(records, meta)
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    print("\n" + "=" * 70)
    print("[汇总]")
    s = report["summary"]
    print(f"  公告数:     {s['notice_count']}")
    print(f"  成功:       {s['success_count']}")
    print(f"  失败:       {s['fail_count']}")
    print(f"  总耗时:     {s['total_elapsed_ms']}ms")
    print(f"  总 token:   {s['total_tokens']} (prompt={s['total_prompt_tokens']}, completion={s['total_completion_tokens']})")
    print(f"  平均延迟:   {s['avg_latency_ms']}ms")
    print(f"  平均 token: {s['avg_tokens_per_doc']}/篇")
    print(f"\n[B 组 evidence_text 统计]")
    print(f"  总值数:           {s['total_values']}")
    print(f"  有 evidence_text: {s['values_with_evidence']}")
    print(f"  无 evidence_text: {s['values_without_evidence']}")
    print(f"  证据覆盖率:       {s['evidence_coverage_rate']:.2%}")

    if report["error_cases"]:
        print(f"\n[失败案例] ({len(report['error_cases'])} 篇)")
        for ec in report["error_cases"]:
            print(f"  - {ec['document_id']}: {ec['error_message'][:100] if ec['error_message'] else '(空)'}")

    print(f"\n[报告] {REPORT_PATH}")
    print(f"[JSONL] {RECORDS_PATH}")
    print("=" * 70)

    if s["fail_count"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
