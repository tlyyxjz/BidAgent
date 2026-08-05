"""W2-01 LLM 字段抽取 prompt + 调用逻辑。

对应总规划 v4.1 第六章 6.1 工作流程「LLM 输出字段值和候选证据列表」。

需求：
1. 修改 LLM 抽取 prompt，要求模型同时输出：
   - 六类核心字段值
   - 每个字段值对应的候选证据文本片段（1～3 段）
   - 证据角色标注（primary / context / qualifier）
2. 输出格式为标准 JSON，符合 backend/schemas.py 中的 Schema
3. 候选证据文本必须是原文中的连续片段，不得改写
4. 记录模型标识、参数、token、延迟

约束：
- 不修改六类字段定义
- 不修改字段状态枚举
- prompt 变更需记录 prompt_hash
- 失败时记录错误，不静默丢弃
"""

from __future__ import annotations

import json
import time

import httpx

from app.config import settings
from app.llm.extraction_schemas import ExtractionResult
from app.llm.extractor_prompts import (
    EXTRACTION_FEWSHOT_EXAMPLES,
    EXTRACTION_FEWSHOT_EXAMPLES_NO_EVIDENCE,
    EXTRACTION_SYSTEM_PROMPT,
    EXTRACTION_SYSTEM_PROMPT_NO_EVIDENCE,
)
from app.llm.extractor_prompt_builder import (
    build_extraction_prompt,
    build_extraction_prompt_no_evidence,
    compute_prompt_hash,
)
from app.llm.extractor_parser import (
    _populate_display_grades,
    parse_extraction_response,
)
from app.utils.logger import get_logger

logger = get_logger("llm_extractor")


async def _post_with_retry(client, url, headers, json, max_retries=4):
    """httpx POST 请求，带指数退避重试（抗瞬时网络抖动）。

    - 网络类异常（连接中断/超时/空响应）会重试
    - 非网络类异常（如 HTTP 4xx 校验错误）不重试，直接抛出
    """
    import asyncio

    delay = 2.0
    last_exc = None
    for attempt in range(max_retries):
        try:
            resp = await client.post(url, headers=headers, json=json)
            resp.raise_for_status()
            return resp
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            # HTTP 4xx 业务错误不重试（如 400/401/429 之外的）
            status = None
            if hasattr(exc, "response") and exc.response is not None:
                status = exc.response.status_code
            if status is not None and 400 <= status < 500 and status != 429:
                raise
            if attempt < max_retries - 1:
                await asyncio.sleep(delay)
                delay *= 2
    raise last_exc

# ========== LLM 调用 ==========


async def call_extraction_llm(raw_text: str) -> ExtractionResult:
    """调用 LLM 抽取字段 + 候选证据。

    优先级：LLM API → 抛异常（W2-01 不做关键词降级，由调用方处理）

    Args:
        raw_text: 公告原文

    Returns:
        ExtractionResult

    Raises:
        RuntimeError: LLM 调用失败
        ValueError: 响应解析失败
    """
    if not settings.DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY not configured")

    start_time = time.perf_counter()
    model_id = settings.LLM_MODEL

    headers = {
        "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.LLM_MODEL,
        "messages": [
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": build_extraction_prompt(raw_text)},
        ],
        "temperature": 0.1,
        "max_tokens": 8000,
        "response_format": {"type": "json_object"},
    }

    try:
        async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT_SECONDS) as client:
            resp = await _post_with_retry(
                client,
                f"{settings.DEEPSEEK_BASE_URL}/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            data = resp.json()

        content = data["choices"][0]["message"]["content"]
        parsed_json = json.loads(content)
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        total_tokens = data.get("usage", {}).get("total_tokens", 0)

        result = parse_extraction_response(parsed_json, model_id, latency_ms, total_tokens)

        logger.info(
            "LLM extraction success model={} fields={} tokens={} latency={}ms",
            model_id,
            len(result.fields),
            result.total_tokens,
            result.latency_ms,
        )
        # W3-07: 为每个字段计算 display_grade（默认 source_role=official_original，交叉验证在多源合并阶段赋值）
        _populate_display_grades(result, source_role="official_original")
        return result

    except Exception as exc:
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        logger.error(
            "LLM extraction failed model={} latency={}ms error={}",
            model_id,
            latency_ms,
            str(exc),
        )
        # Sol 要求：失败时记录错误，不静默丢弃
        return ExtractionResult(
            fields=[],
            model_id=model_id,
            prompt_hash=compute_prompt_hash(),
            total_tokens=0,
            latency_ms=latency_ms,
            error=str(exc),
        )


async def call_extraction_llm_no_evidence(raw_text: str) -> ExtractionResult:
    """调用 LLM 抽取字段（无证据版本，A 组消融实验专用）。

    与 call_extraction_llm 的区别：
    - 使用 EXTRACTION_SYSTEM_PROMPT_NO_EVIDENCE（不要求 candidate_evidences）
    - 使用 EXTRACTION_FEWSHOT_EXAMPLES_NO_EVIDENCE
    - prompt_hash 不同（区分 A/B/C 三组）

    Args:
        raw_text: 公告原文

    Returns:
        ExtractionResult（candidate_evidences 为空列表）
    """
    if not settings.DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY not configured")

    start_time = time.perf_counter()
    model_id = settings.LLM_MODEL
    no_evidence_hash = compute_prompt_hash(
        EXTRACTION_SYSTEM_PROMPT_NO_EVIDENCE,
        EXTRACTION_FEWSHOT_EXAMPLES_NO_EVIDENCE,
    )

    headers = {
        "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.LLM_MODEL,
        "messages": [
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT_NO_EVIDENCE},
            {"role": "user", "content": build_extraction_prompt_no_evidence(raw_text)},
        ],
        "temperature": 0.1,
        "max_tokens": 8000,
        "response_format": {"type": "json_object"},
    }

    try:
        async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT_SECONDS) as client:
            response = await _post_with_retry(
                client,
                f"{settings.DEEPSEEK_BASE_URL}/v1/chat/completions",
                headers=headers,
                json=payload,
            )

        data = response.json()
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        total_tokens = data.get("usage", {}).get("total_tokens", 0)

        parsed_json = data["choices"][0]["message"]["content"]
        parsed_json = json.loads(parsed_json)

        result = parse_extraction_response(
            parsed_json, model_id, latency_ms, total_tokens
        )
        # 覆盖 prompt_hash 为无证据版本
        result.prompt_hash = no_evidence_hash

        logger.info(
            "LLM no-evidence extraction OK model={} fields={} tokens={} latency={}ms",
            model_id,
            len(result.fields),
            total_tokens,
            latency_ms,
        )
        # W3-07: 同样填充 display_grade
        _populate_display_grades(result, source_role="official_original")
        return result

    except Exception as exc:
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        logger.error(
            "LLM no-evidence extraction failed model={} latency={}ms error={}",
            model_id,
            latency_ms,
            str(exc),
        )
        return ExtractionResult(
            fields=[],
            model_id=model_id,
            prompt_hash=no_evidence_hash,
            total_tokens=0,
            latency_ms=latency_ms,
            error=str(exc),
        )
