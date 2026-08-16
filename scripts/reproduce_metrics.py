"""一键复现脚本 —— 打印 BidAgent 金标全量复测的关键指标。

用途：让评委/用户跑一条命令，就能复现材料里的核心数字：
    D 组 field_precision 96.44% / unjustified_rate 0% / evidence_precision 100% / null_fp 2.15%

两种模式：
    python scripts/reproduce_metrics.py            # 快速：读已存档的复测结果（不调 LLM，秒出）
    python scripts/reproduce_metrics.py --rerun    # 完整：重新调 LLM 跑 598 篇四组消融（耗时、花 API）

默认快速模式，读 _w3_outputs/gold598_retest.json（初赛实测产物，含完整元数据）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULT_PATH = ROOT / "_w3_outputs" / "gold598_retest.json"

KEY_METRICS = [
    ("field_precision", "字段精确率"),
    ("unjustified_rate", "无依据输出率"),
    ("evidence_precision", "证据精确率"),
    ("null_false_positive_rate", "空值误报率"),
    ("multi_value_f1_avg", "多值字段F1"),
]


def print_metrics(data: dict) -> None:
    """打印 A/B/C/D 四组关键指标。"""
    summaries = data.get("summaries", {})
    group_names = {"A": "直接LLM(无验证)", "B": "LLM+候选证据", "C": "LLM+程序验证", "D": "完整系统"}

    print("=" * 70)
    print("BidAgent 金标全量复测（598 篇）关键指标")
    print(f"数据: {data.get('docs', '?')} 篇金标 | 复测产物: {RESULT_PATH.name}")
    print("=" * 70)
    print()

    header = f"{'组':<4}{'方案':<16}{'精确率':>10}{'无依据':>10}{'证据精':>10}{'空误报':>10}"
    print(header)
    print("-" * 70)
    for g in "ABCD":
        s = summaries.get(g)
        if not s:
            continue
        print(
            f"{g:<4}{group_names[g]:<16}"
            f"{s['field_precision']:>9.2%}"
            f"{s['unjustified_rate']:>10.2%}"
            f"{s['evidence_precision']:>10.2%}"
            f"{s['null_false_positive_rate']:>10.2%}"
        )
    print()

    # 重点突出 D 组（完整系统 = 核心卖点）
    d = summaries.get("D", {})
    print("▶ 核心结论（D 组 = 完整系统）：")
    print(f"   字段精确率 {d['field_precision']:.2%} · 无依据输出率 {d['unjustified_rate']:.2%} "
          f"· 证据精确率 {d['evidence_precision']:.2%} · 空值误报率 {d['null_false_positive_rate']:.2%}")
    print()

    # 按来源分组（D 组）
    by_source = data.get("by_source", {})
    if by_source:
        print("▶ D 组按来源分组（字段精确率）：")
        for src in ("frozen93", "w3", "w4", "w5"):
            if src in by_source and "D" in by_source[src]:
                fp = by_source[src]["D"].get("field_precision", 0)
                print(f"   {src:<10} {fp:.2%}")
    print()

    # 元数据（证明可复现的关键：模型/温度/commit）
    meta_keys = ("model_id", "prompt_hash", "code_commit", "temperature")
    if d:
        print("▶ 复测元数据（可追溯）：")
        for k in meta_keys:
            if k in d and d[k] not in (None, ""):
                print(f"   {k}: {d[k]}")
    print("=" * 70)


def main() -> None:
    parser = argparse.ArgumentParser(description="BidAgent 一键复现指标")
    parser.add_argument(
        "--rerun", action="store_true",
        help="重新调 LLM 跑 598 篇四组消融（耗时/花API）；默认读已存档结果",
    )
    args = parser.parse_args()

    if args.rerun:
        import subprocess
        cmd = [sys.executable, str(ROOT / "scripts" / "eval_gold598_retest.py")]
        print("重新跑全量复测（会调 LLM，耗时较长）...")
        subprocess.run(cmd, check=False)
        return

    if not RESULT_PATH.exists():
        print(f"❌ 未找到复测结果文件: {RESULT_PATH}")
        print("   请先运行: python scripts/eval_gold598_retest.py")
        print("   或使用: python scripts/reproduce_metrics.py --rerun")
        sys.exit(1)

    data = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    print_metrics(data)


if __name__ == "__main__":
    main()
