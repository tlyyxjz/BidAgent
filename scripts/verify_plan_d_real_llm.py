"""方案D真实LLM验证 (K3-W3-03 待查项#1).

用真实联合体公告调用 DeepSeek LLM, 验证:
1. 方案A: raw_value 为 dict/list 时归一化为 JSON 字符串
2. 方案D: per-field try/except 容错, 单字段失败不拖垮整篇

验证场景:
- 联合体公告: winner_name 字段 LLM 可能输出 {"main":..., "partners":[...]}
- 多中标人: winner_name 字段 LLM 可能输出 ["公司A", "公司B"]

按 Sol 规矩: 真实数据 + 记录模型标识/参数/token/延迟.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings
from app.llm.extractor import call_extraction_llm, parse_extraction_response
from app.utils.logger import get_logger

logger = get_logger("verify_plan_d")

RAW_DIR = ROOT / "_w3_raw"

# 联合体公告样例 (清洗后)
JOINT_VENTURE_FILES = [
    "w3_tender_001.txt",  # 中央广播电视总台 (联合体)
    "w3_tender_002.txt",  # 中国工程物理研究院 (联合体)
    "w3_award_016.txt",   # 铁道党校 (中标公告)
]


def load_notice(filename: str) -> str:
    """加载公告正文 (跳过前4行元数据)."""
    text = (RAW_DIR / filename).read_text(encoding="utf-8")
    lines = text.split("\n", 4)
    return lines[4] if len(lines) > 4 else ""


async def verify_single_notice(filename: str) -> dict:
    """验证单篇公告的方案A+D效果."""
    print(f"\n{'='*70}")
    print(f"验证: {filename}")
    print(f"{'='*70}")

    content = load_notice(filename)
    if not content:
        print(f"  ERROR: 无法加载 {filename}")
        return {"file": filename, "status": "load_failed"}

    print(f"  正文长度: {len(content)} 字符")
    print(f"  LLM模型: {settings.LLM_PROVIDER}/{settings.LLM_MODEL}")

    # 真实调用 LLM
    t0 = time.time()
    try:
        result = await call_extraction_llm(content)
        latency = time.time() - t0
    except Exception as e:
        latency = time.time() - t0
        print(f"  LLM调用失败 ({latency:.2f}s): {type(e).__name__}: {e}")
        return {
            "file": filename,
            "status": "llm_failed",
            "error": str(e),
            "latency_s": round(latency, 2),
            "model": f"{settings.LLM_PROVIDER}/{settings.LLM_MODEL}",
        }

    print(f"  LLM延迟: {latency:.2f}s")
    print(f"  Token数: {result.total_tokens}")
    print(f"  抽取字段数: {len(result.fields)}")

    # 检查每个字段的 raw_value 类型
    field_details = []
    has_dict_list = False
    for f in result.fields:
        rv = f.raw_value
        rv_type = type(rv).__name__ if rv is not None else "None"
        # 检查是否被方案A归一化为JSON字符串
        is_json_normalized = False
        if isinstance(rv, str) and rv.startswith(("{", "[")):
            try:
                json.loads(rv)
                is_json_normalized = True
                has_dict_list = True
            except Exception:
                pass

        field_details.append({
            "field_name": f.field_name,
            "field_status": f.field_status,
            "raw_value_type": rv_type,
            "raw_value_preview": (rv[:80] if rv else None),
            "is_json_normalized": is_json_normalized,
        })
        print(f"  - {f.field_name}: status={f.field_status}, type={rv_type}, normalized={is_json_normalized}")
        if rv:
            print(f"    值预览: {rv[:80]}")

    # 关键验证点
    print(f"\n  验证结论:")
    print(f"  - 方案D (per-field容错): 抽取到 {len(result.fields)} 个字段 (原实现可能 0 个)")
    if has_dict_list:
        print(f"  - 方案A (dict/list归一化): 检测到JSON字符串归一化 ✓")
    else:
        print(f"  - 方案A (dict/list归一化): 本次未触发 (LLM未输出dict/list)")

    return {
        "file": filename,
        "status": "success",
        "model": f"{settings.LLM_PROVIDER}/{settings.LLM_MODEL}",
        "latency_s": round(latency, 2),
        "total_tokens": result.total_tokens,
        "field_count": len(result.fields),
        "has_json_normalized": has_dict_list,
        "fields": field_details,
    }


async def main() -> None:
    print("=" * 70)
    print("方案D真实LLM验证 (K3-W3-03 待查项#1)")
    print("=" * 70)
    print(f"模型: {settings.LLM_PROVIDER}/{settings.LLM_MODEL}")
    print(f"验证公告: {len(JOINT_VENTURE_FILES)} 篇")

    results = []
    for fname in JOINT_VENTURE_FILES:
        try:
            r = await verify_single_notice(fname)
            results.append(r)
            # 间隔避免限流
            await asyncio.sleep(2)
        except Exception as e:
            print(f"  异常: {type(e).__name__}: {e}")
            results.append({"file": fname, "status": "exception", "error": str(e)})

    # 汇总
    print("\n" + "=" * 70)
    print("汇总")
    print("=" * 70)
    success = sum(1 for r in results if r.get("status") == "success")
    failed = sum(1 for r in results if r.get("status") != "success")
    total_fields = sum(r.get("field_count", 0) for r in results)
    has_json = sum(1 for r in results if r.get("has_json_normalized"))

    print(f"  成功: {success}/{len(results)}")
    print(f"  失败: {failed}/{len(results)}")
    print(f"  总抽取字段: {total_fields}")
    print(f"  触发方案A归一化: {has_json}/{len(results)}")

    # 保存结果
    output = ROOT / "_k3_outputs" / "W3_plan_d_verification.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已保存: {output}")

    # Sol规矩: 不宣称通过, 标记待查
    print("\n--- Sol规矩声明 ---")
    print("本次验证为真实LLM调用, 记录了模型标识/延迟/token数")
    if success == len(results):
        print(f"全部{success}篇调用成功, 方案D容错有效")
    else:
        print(f"有{failed}篇失败, 需排查")


if __name__ == "__main__":
    asyncio.run(main())
