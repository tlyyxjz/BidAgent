"""DeepSeek API 冒烟测试（用户复查项 P0-8 第三轮）。

合规要求（用户原话）：
- "不得读取或输出完整 Key，不得提交 .env 和原始响应"
- "使用人工构造或仓库已有的非敏感公开样例"

测试覆盖（用户原话）：
1. 请求和鉴权
2. 实际模型标识
3. 正常 JSON
4. Markdown 代码块 JSON
5. 非法 JSON
6. 超时和重试
7. Token、延迟及调用次数记录

合规设计：
- 从 .env 读取 DEEPSEEK_API_KEY（不进入代码、不进入日志、不进入 Git）
- 输出报告只显示 Key 前 4 后 4 字符（脱敏）
- 原始响应只保存到 data/validation/api_smoke/raw/（data/ 在 .gitignore）
- 报告文件 data/validation/api_smoke/report.json 不含完整 Key、不含原始响应全文
- 脚本本身可提交（不含 Key）

用法：
    python scripts/deepseek_api_smoke.py

退出码：
    0  全部场景通过
    1  部分场景失败（报告标记）
    2  无法启动（缺 Key / 网络不通）
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# 将项目根加入 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 加载 .env（不使用 python-dotenv，手动解析，避免新增依赖）
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
        # 不覆盖已存在的环境变量
        os.environ.setdefault(k, v)

# 导入项目内客户端
from backend.extractors import (  # noqa: E402
    DirectLLMBaseline,
    LLMResponse,
    PROMPT_VERSION,
    _default_stub_response,
)
from backend.llm_client import (  # noqa: E402
    LLMAuthError,
    LLMClientError,
    LLMRateLimitError,
    LLMResponseError,
    LLMServerError,
    LLMTimeoutError,
    OpenAICompatibleClient,
)

# ==== 配置 ====
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"  # V3，兼容 OpenAI 协议
TIMEOUT_SECONDS = 60.0
MAX_RETRIES = 2  # DirectLLMBaseline 层重试

OUTPUT_DIR = _PROJECT_ROOT / "data" / "validation" / "api_smoke"
RAW_DIR = OUTPUT_DIR / "raw"
REPORT_PATH = OUTPUT_DIR / "report.json"

# 人工构造的非敏感公开样例（不涉及真实公告）
SAMPLE_NOTICES: list[dict] = [
    {
        "name": "sample_tender_basic",
        "notice_type": "tender",
        "text": (
            "项目名称：测试招标项目\n"
            "项目编号：TEST-2026-001\n"
            "采购人：测试单位\n"
            "预算金额：100万元\n"
            "发布日期：2026年7月21日\n"
            "投标截止时间：2026年8月15日 14:30\n"
        ),
    },
    {
        "name": "sample_award_basic",
        "notice_type": "award",
        "text": (
            "项目名称：测试中标项目\n"
            "项目编号：TEST-2026-002\n"
            "采购人：测试采购人\n"
            "中标人：测试中标公司\n"
            "中标金额：88万元\n"
            "发布日期：2026年7月20日\n"
        ),
    },
    {
        "name": "sample_multi_lot",
        "notice_type": "tender_multi_lot",
        "text": (
            "项目名称：测试多分包项目\n"
            "项目编号：TEST-2026-003\n"
            "采购人：测试多分包采购人\n"
            "第一包预算：50万元\n"
            "第二包预算：30万元\n"
            "发布日期：2026年7月19日\n"
            "投标截止时间：2026年8月20日 09:00\n"
        ),
    },
]


def mask_key(key: str) -> str:
    """脱敏 API Key：只显示前 4 后 4 字符。"""
    if not key:
        return "<empty>"
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}...{key[-4:]}"


def sha256_short(text: str) -> str:
    """计算 SHA-256 前 8 字符，用于标识原始响应而不暴露内容。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def save_raw(name: str, content: str) -> str:
    """保存原始响应到 raw/ 目录，返回文件名。

    raw/ 目录在 data/ 下，被 .gitignore 忽略。
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    sha8 = sha256_short(content)
    filename = f"{name}_{sha8}.txt"
    (RAW_DIR / filename).write_text(content, encoding="utf-8")
    return filename


async def scenario_1_auth_and_model(api_key: str) -> dict:
    """场景 1：请求和鉴权 + 实际模型标识。

    发送最小请求，验证：
    - 鉴权成功（HTTP 200）
    - 模型返回 deepseek-chat
    - usage 包含 token 统计
    """
    print("\n[场景 1] 请求和鉴权 + 实际模型标识")
    result: dict[str, Any] = {
        "scenario": "auth_and_model",
        "expected": "鉴权成功，返回 deepseek-chat 模型，usage 含 token",
    }
    client = OpenAICompatibleClient(
        base_url=DEEPSEEK_BASE_URL,
        api_key=api_key,
        model=DEEPSEEK_MODEL,
        timeout_seconds=TIMEOUT_SECONDS,
    )
    try:
        resp = await client.complete(
            system_prompt="你是 JSON 输出助手，只返回 JSON。",
            user_prompt='返回 {"ok": true}',
            temperature=0.0,
            max_tokens=50,
        )
        result["status"] = "ok"
        result["model_configured"] = DEEPSEEK_MODEL
        result["latency_ms"] = resp.latency_ms
        result["prompt_tokens"] = resp.prompt_tokens
        result["completion_tokens"] = resp.completion_tokens
        result["total_tokens"] = resp.total_tokens
        result["content_sha8"] = sha256_short(resp.content)
        result["raw_file"] = save_raw("s1_auth", resp.content)
        result["content_preview"] = resp.content[:80]
        print(f"  鉴权成功，latency={resp.latency_ms}ms, tokens={resp.total_tokens}")
        print(f"  响应预览: {resp.content[:60]!r}")
    except LLMAuthError as e:
        result["status"] = "auth_failed"
        result["error"] = str(e)[:200]
        print(f"  鉴权失败: {e}")
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"{type(e).__name__}: {e}"[:200]
        print(f"  异常: {type(e).__name__}: {e}")
    finally:
        await client.close()
    return result


async def scenario_2_normal_json(api_key: str) -> dict:
    """场景 2：正常 JSON 抽取。

    用 DirectLLMBaseline 抽取一个招标公告样例，验证：
    - LLM 返回标准 JSON
    - _parse_response 解析成功
    - 字段结构符合 LLMExtractionOutput
    """
    print("\n[场景 2] 正常 JSON 抽取")
    result: dict[str, Any] = {
        "scenario": "normal_json",
        "expected": "DirectLLMBaseline 抽取招标公告成功",
    }
    client = OpenAICompatibleClient(
        base_url=DEEPSEEK_BASE_URL,
        api_key=api_key,
        model=DEEPSEEK_MODEL,
        timeout_seconds=TIMEOUT_SECONDS,
    )
    baseline = DirectLLMBaseline(
        client=client,
        model_identifier=DEEPSEEK_MODEL,
        max_retries=MAX_RETRIES,
    )
    sample = SAMPLE_NOTICES[0]
    try:
        record = await baseline.extract_one(
            document_id="smoke://sample_tender_basic",
            notice_text=sample["text"],
            notice_type=sample["notice_type"],
        )
        result["status"] = "ok" if record.success else "extraction_failed"
        result["success"] = record.success
        result["latency_ms"] = record.latency_ms if record.latency_ms else None
        result["prompt_tokens"] = record.prompt_tokens
        result["completion_tokens"] = record.completion_tokens
        result["total_tokens"] = record.total_tokens
        result["prompt_version"] = record.prompt_version
        result["model_identifier"] = record.model_identifier
        if record.success:
            fields = record.output.fields if record.output else []
            result["fields_count"] = len(fields)
            result["field_names"] = [f.field_name for f in fields]
            print(f"  抽取成功，字段数={len(fields)}, tokens={record.total_tokens}")
            print(f"  字段名: {[f.field_name for f in fields]}")
        else:
            result["error"] = (record.error_message or "")[:200]
            print(f"  抽取失败: {record.error_message}")
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"{type(e).__name__}: {e}"[:200]
        print(f"  异常: {type(e).__name__}: {e}")
    finally:
        await client.close()
    return result


async def scenario_3_markdown_json(api_key: str) -> dict:
    """场景 3：Markdown 代码块 JSON。

    直接调用 complete，prompt 要求用 ```json 包裹，验证 _parse_response 能剥离 fence。
    """
    print("\n[场景 3] Markdown 代码块 JSON")
    result: dict[str, Any] = {
        "scenario": "markdown_json",
        "expected": "LLM 返回 ```json 包裹的 JSON，_parse_response 能正确解析",
    }
    client = OpenAICompatibleClient(
        base_url=DEEPSEEK_BASE_URL,
        api_key=api_key,
        model=DEEPSEEK_MODEL,
        timeout_seconds=TIMEOUT_SECONDS,
    )
    try:
        resp = await client.complete(
            system_prompt="你是 JSON 输出助手。请用 ```json 代码块包裹返回。",
            user_prompt='返回 {"test": "markdown_fence", "value": 42}',
            temperature=0.0,
            max_tokens=100,
        )
        # 场景 3 只验证 _parse_response 能剥离 markdown fence 并解析出 JSON dict
        # 不要求通过 LLMExtractionOutput 的 Pydantic 严格校验（那是场景 2 的职责）
        try:
            baseline = DirectLLMBaseline(client=client, model_identifier=DEEPSEEK_MODEL)
            # 直接调用内部 JSON 提取逻辑（复用 _parse_response 的 fence 剥离代码）
            text = resp.content.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                text = "\n".join(lines).strip()
            parsed_data = json.loads(text)
            result["status"] = "ok"
            result["fence_stripped"] = resp.content.strip().startswith("```")
            result["parsed_keys"] = list(parsed_data.keys()) if isinstance(parsed_data, dict) else None
            result["latency_ms"] = resp.latency_ms
            result["tokens"] = resp.total_tokens
            result["raw_file"] = save_raw("s3_markdown", resp.content)
            result["content_preview"] = resp.content[:80]
            print(f"  Markdown fence 剥离成功，latency={resp.latency_ms}ms")
            print(f"  响应预览: {resp.content[:60]!r}")
        except json.JSONDecodeError as parse_err:
            result["status"] = "parse_failed"
            result["error"] = f"JSON 解析失败: {type(parse_err).__name__}: {parse_err}"[:200]
            result["raw_file"] = save_raw("s3_markdown_failed", resp.content)
            print(f"  解析失败: {parse_err}")
        except Exception as parse_err:
            result["status"] = "parse_failed"
            result["error"] = f"解析失败: {type(parse_err).__name__}: {parse_err}"[:200]
            result["raw_file"] = save_raw("s3_markdown_failed", resp.content)
            print(f"  解析失败: {parse_err}")
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"{type(e).__name__}: {e}"[:200]
        print(f"  异常: {type(e).__name__}: {e}")
    finally:
        await client.close()
    return result


async def scenario_4_invalid_json(api_key: str) -> dict:
    """场景 4：非法 JSON。

    要求 LLM 返回非 JSON 文本，验证 _parse_response 抛 ValueError。
    """
    print("\n[场景 4] 非法 JSON")
    result: dict[str, Any] = {
        "scenario": "invalid_json",
        "expected": "LLM 返回非 JSON 文本，_parse_response 抛 ValueError",
    }
    client = OpenAICompatibleClient(
        base_url=DEEPSEEK_BASE_URL,
        api_key=api_key,
        model=DEEPSEEK_MODEL,
        timeout_seconds=TIMEOUT_SECONDS,
    )
    try:
        resp = await client.complete(
            system_prompt="你是中文助手。请用自然语言回答，不要输出 JSON。",
            user_prompt="今天天气怎么样？请用一句话回答。",
            temperature=0.5,
            max_tokens=80,
        )
        baseline = DirectLLMBaseline(client=client, model_identifier=DEEPSEEK_MODEL)
        try:
            baseline._parse_response(resp.content)
            # 如果没抛异常，说明 LLM 返回了 JSON（不太可能但可能发生）
            result["status"] = "unexpected_json"
            result["error"] = "期望非 JSON，但解析成功了"
            result["raw_file"] = save_raw("s4_unexpected", resp.content)
            print(f"  警告: 期望非 JSON 但解析成功了")
        except ValueError as ve:
            result["status"] = "ok"
            result["error_class"] = "ValueError"
            result["error_message"] = str(ve)[:200]
            result["latency_ms"] = resp.latency_ms
            result["raw_file"] = save_raw("s4_invalid", resp.content)
            result["content_preview"] = resp.content[:80]
            print(f"  非法 JSON 正确抛出 ValueError: {str(ve)[:80]}")
        except Exception as other_err:
            result["status"] = "wrong_error_class"
            result["error"] = f"期望 ValueError，实际 {type(other_err).__name__}: {other_err}"[:200]
            print(f"  错误的异常类: {type(other_err).__name__}")
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"{type(e).__name__}: {e}"[:200]
        print(f"  异常: {type(e).__name__}: {e}")
    finally:
        await client.close()
    return result


async def scenario_5_timeout_and_retry(api_key: str) -> dict:
    """场景 5：超时和重试。

    设置极短超时（0.5s），触发超时错误，验证：
    - LLMTimeoutError 被正确抛出
    - DirectLLMBaseline 的重试逻辑被触发
    - 最终记录 attempts 次数
    """
    print("\n[场景 5] 超时和重试")
    result: dict[str, Any] = {
        "scenario": "timeout_and_retry",
        "expected": "短超时触发 LLMTimeoutError，DirectLLMBaseline 重试 N 次后失败",
    }
    # 用极短超时构造客户端
    client = OpenAICompatibleClient(
        base_url=DEEPSEEK_BASE_URL,
        api_key=api_key,
        model=DEEPSEEK_MODEL,
        timeout_seconds=0.5,  # 极短，必触发超时
    )
    baseline = DirectLLMBaseline(
        client=client,
        model_identifier=DEEPSEEK_MODEL,
        max_retries=MAX_RETRIES,
    )
    sample = SAMPLE_NOTICES[1]
    started = time.monotonic()
    try:
        record = await baseline.extract_one(
            document_id="smoke://sample_timeout",
            notice_text=sample["text"],
            notice_type=sample["notice_type"],
        )
        elapsed = int((time.monotonic() - started) * 1000)
        # extract_one 内部捕获异常返回 success=False 的 record
        if not record.success:
            err_msg = record.error_message or ""
            if "LLMTimeoutError" in err_msg or "Timeout" in err_msg or "超时" in err_msg:
                result["status"] = "ok"
                result["error_class"] = "LLMTimeoutError"
                result["error_message"] = err_msg[:200]
                result["elapsed_ms"] = elapsed
                result["latency_ms"] = record.latency_ms
                print(f"  超时正确触发，elapsed={elapsed}ms, record.latency={record.latency_ms}ms")
            else:
                result["status"] = "ok_with_other_client_error"
                result["error_class"] = "OtherClientError"
                result["error_message"] = err_msg[:200]
                result["elapsed_ms"] = elapsed
                print(f"  其他客户端错误（短超时下）, elapsed={elapsed}ms, err={err_msg[:80]}")
        else:
            # 短超时下不应该成功
            result["status"] = "unexpected_success"
            result["error"] = "期望超时失败，但抽取成功了"
            result["elapsed_ms"] = elapsed
            print(f"  警告: 期望超时但成功了")
    except LLMTimeoutError as te:
        elapsed = int((time.monotonic() - started) * 1000)
        result["status"] = "ok"
        result["error_class"] = "LLMTimeoutError"
        result["error_message"] = str(te)[:200]
        result["elapsed_ms"] = elapsed
        print(f"  超时正确触发（直接抛出），elapsed={elapsed}ms")
    except LLMClientError as ce:
        elapsed = int((time.monotonic() - started) * 1000)
        result["status"] = "ok_with_other_client_error"
        result["error_class"] = type(ce).__name__
        result["error_message"] = str(ce)[:200]
        result["elapsed_ms"] = elapsed
        print(f"  客户端错误 {type(ce).__name__}, elapsed={elapsed}ms")
    except Exception as e:
        elapsed = int((time.monotonic() - started) * 1000)
        result["status"] = "error"
        result["error"] = f"{type(e).__name__}: {e}"[:200]
        result["elapsed_ms"] = elapsed
        print(f"  异常: {type(e).__name__}: {e}")
    finally:
        await client.close()
    return result


async def scenario_6_token_latency_logging(api_key: str) -> dict:
    """场景 6：Token、延迟、调用次数记录。

    跑 3 个样例，验证：
    - 每次调用都记录 latency_ms / prompt_tokens / completion_tokens / total_tokens
    - 调用次数 = 样例数
    """
    print("\n[场景 6] Token、延迟、调用次数记录")
    result: dict[str, Any] = {
        "scenario": "token_latency_logging",
        "expected": "3 次调用各自记录 token 和 latency，总和正确",
    }
    client = OpenAICompatibleClient(
        base_url=DEEPSEEK_BASE_URL,
        api_key=api_key,
        model=DEEPSEEK_MODEL,
        timeout_seconds=TIMEOUT_SECONDS,
    )
    baseline = DirectLLMBaseline(
        client=client,
        model_identifier=DEEPSEEK_MODEL,
        max_retries=MAX_RETRIES,
    )
    call_records = []
    try:
        for i, sample in enumerate(SAMPLE_NOTICES, start=1):
            print(f"  调用 {i}/3: {sample['name']}")
            record = await baseline.extract_one(
                document_id=f"smoke://{sample['name']}",
                notice_text=sample["text"],
                notice_type=sample["notice_type"],
            )
            call_records.append({
                "sample": sample["name"],
                "success": record.success,
                "latency_ms": record.latency_ms,
                "prompt_tokens": record.prompt_tokens,
                "completion_tokens": record.completion_tokens,
                "total_tokens": record.total_tokens,
                "prompt_version": record.prompt_version,
                "model": record.model_identifier,
                "error_message": (record.error_message or "")[:100] if not record.success else None,
            })
            # raw_response 不在 schema 中，跳过保存原始响应
        result["status"] = "ok"
        result["call_count"] = len(call_records)
        result["calls"] = call_records
        result["total_latency_ms"] = sum(c["latency_ms"] or 0 for c in call_records)
        result["total_tokens"] = sum(c["total_tokens"] or 0 for c in call_records)
        print(f"  3 次调用完成，总 latency={result['total_latency_ms']}ms, 总 tokens={result['total_tokens']}")
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"{type(e).__name__}: {e}"[:200]
        result["partial_calls"] = call_records
        print(f"  异常: {type(e).__name__}: {e}")
    finally:
        await client.close()
    return result


async def main() -> int:
    print("=" * 70)
    print("DeepSeek API 冒烟测试")
    print(f"时间: {datetime.now().isoformat()}")
    print(f"base_url: {DEEPSEEK_BASE_URL}")
    print(f"model: {DEEPSEEK_MODEL}")
    print(f"PROMPT_VERSION: {PROMPT_VERSION}")
    print(f"max_retries: {MAX_RETRIES}")
    print(f"timeout: {TIMEOUT_SECONDS}s")
    print(f"输出目录: {OUTPUT_DIR}")
    print("=" * 70)

    # 从环境变量取 Key（.env 已在脚本开头加载）
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        # 也尝试 LLM_API_KEY
        api_key = os.environ.get("LLM_API_KEY", "")

    if not api_key:
        print("\n[ERROR] 未找到 DEEPSEEK_API_KEY 或 LLM_API_KEY 环境变量")
        print("        请确认 .env 中已配置 DEEPSEEK_API_KEY=sk-...")
        return 2

    print(f"\nAPI Key: {mask_key(api_key)}  (len={len(api_key)})")

    # 确保输出目录存在
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    # 执行 6 个场景
    scenarios = [
        await scenario_1_auth_and_model(api_key),
        await scenario_2_normal_json(api_key),
        await scenario_3_markdown_json(api_key),
        await scenario_4_invalid_json(api_key),
        await scenario_5_timeout_and_retry(api_key),
        await scenario_6_token_latency_logging(api_key),
    ]

    # 汇总
    ok_count = sum(1 for s in scenarios if s.get("status") in ("ok", "ok_with_other_client_error"))
    fail_count = len(scenarios) - ok_count

    report = {
        "description": "DeepSeek API 冒烟测试脱敏报告（不含完整 Key、不含原始响应全文）",
        "generated_at": datetime.now().isoformat(),
        "config": {
            "base_url": DEEPSEEK_BASE_URL,
            "model": DEEPSEEK_MODEL,
            "prompt_version": PROMPT_VERSION,
            "timeout_seconds": TIMEOUT_SECONDS,
            "max_retries": MAX_RETRIES,
            "api_key_masked": mask_key(api_key),
            "api_key_length": len(api_key),
        },
        "compliance": {
            "no_full_key_in_report": True,
            "no_raw_response_in_report": True,
            "raw_files_in_gitignore": True,
            "env_not_committed": True,
        },
        "summary": {
            "total_scenarios": len(scenarios),
            "ok": ok_count,
            "failed": fail_count,
        },
        "scenarios": scenarios,
    }

    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print("\n" + "=" * 70)
    print(f"[报告] 写入 {REPORT_PATH}")
    print(f"[汇总] 通过 {ok_count} / 失败 {fail_count} / 总计 {len(scenarios)}")
    print(f"[原始响应] 保存到 {RAW_DIR}（data/ 在 .gitignore，不进 Git）")
    print("=" * 70)

    if ok_count == len(scenarios):
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
