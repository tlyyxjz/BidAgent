"""LLM 意图解析主逻辑。

优先级：内存缓存 → DeepSeek API → 关键词降级。

工程规范：
- 所有 LLM 调用 async/await（httpx.AsyncClient）
- 失败时降级到关键词+正则兜底
- 日志带 request_id 上下文
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

import httpx

from app.config import settings
from app.llm.prompts import INTENT_SYSTEM_PROMPT, build_intent_prompt
from app.llm.schemas import ParsedFilters
from app.utils.logger import get_logger

logger = get_logger("llm_parser")

# M-6 修复：缓存加 TTL 和容量限制
_CACHE_MAX_SIZE = 1000  # 最大缓存条目
_CACHE_TTL_SECONDS = 3600  # 默认 1 小时过期
# 缓存结构：{key: (ParsedFilters, created_at)}
_semantic_cache: dict[str, tuple[ParsedFilters, float]] = {}


def _cache_key(query: str) -> str:
    """生成缓存 key（normalize + SHA256）。"""
    normalized = re.sub(r"\s+", "", query.strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _get_from_cache(query: str) -> ParsedFilters | None:
    """从内存缓存读取（M-6：检查 TTL）。"""
    key = _cache_key(query)
    if key in _semantic_cache:
        parsed, created_at = _semantic_cache[key]
        # 检查 TTL
        if time.time() - created_at > _CACHE_TTL_SECONDS:
            del _semantic_cache[key]
            return None
        logger.info("LLM cache hit query={}", query[:50])
        return parsed
    return None


def _save_to_cache(query: str, parsed: ParsedFilters) -> None:
    """写入内存缓存（M-6：LRU 风格淘汰 + 容量限制）。"""
    # 容量超限时删除最早的条目（简易 LRU）
    if len(_semantic_cache) >= _CACHE_MAX_SIZE:
        oldest_key = min(_semantic_cache.keys(), key=lambda k: _semantic_cache[k][1])
        del _semantic_cache[oldest_key]
    key = _cache_key(query)
    _semantic_cache[key] = (parsed, time.time())


async def _call_deepseek(query: str) -> dict[str, Any]:
    """调用 DeepSeek API（OpenAI 兼容协议）。

    Raises:
        httpx.HTTPError: 网络或 API 错误
        KeyError / ValueError: 响应解析失败
    """
    headers = {
        "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.LLM_MODEL,
        "messages": [
            {"role": "system", "content": INTENT_SYSTEM_PROMPT},
            {"role": "user", "content": build_intent_prompt(query)},
        ],
        "temperature": 0.1,
        "max_tokens": 500,
        "response_format": {"type": "json_object"},
    }
    async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT_SECONDS) as client:
        resp = await client.post(
            f"{settings.DEEPSEEK_BASE_URL}/v1/chat/completions",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)


def _fallback_keyword_parse(query: str) -> ParsedFilters:
    """LLM 不可用时的降级：基于关键词的简单解析。

    命题硬要求：意图解析准确率。此降级仅作为兜底，GPT-5.6 Sol 后续优化。
    """
    topic: str | None = None
    region: str | None = None
    time_range = "30d"
    frequency: str | None = None
    trigger_type = "immediate"
    keywords: list[str] = []

    # 地区识别
    regions = ["北京", "上海", "广东", "深圳", "浙江", "江苏", "四川",
               "湖北", "山东", "河南", "福建", "安徽"]
    for r in regions:
        if r in query:
            region = r
            break

    # 主题识别（命题示例：服务器/充电桩）
    topics = {
        "服务器": ["服务器", "机架", "刀片"],
        "充电桩": ["充电桩", "充电站", "充电设备"],
        "IT设备": ["IT", "信息化", "计算机", "网络设备"],
        "医疗器械": ["医疗", "器械"],
        "建筑工程": ["建筑", "工程", "施工"],
    }
    for t, kws in topics.items():
        if any(k in query for k in kws):
            topic = t
            keywords.extend(k for k in kws if k in query)
            break

    # 时间识别（M-3 修复：加括号明确语义）
    if ("最近" in query and "一月" in query) or "1个月" in query:
        time_range = "1m"
    elif ("最近" in query and "3月" in query) or "3个月" in query:
        time_range = "3m"
    elif "本周" in query:
        time_range = "7d"
    elif "今天" in query:
        time_range = "1d"

    # 频率识别（命题示例：每天9:00 / 今天9:00）
    if "每天" in query:
        frequency = "0 9 * * *"  # 默认每天 9 点
        trigger_type = "scheduled"
    elif "今天" in query and "9:00" in query:
        frequency = "once:09:00"
        trigger_type = "scheduled"
    elif "每周一" in query:
        frequency = "0 9 * * 1"
        trigger_type = "scheduled"

    return ParsedFilters(
        topic=topic,
        region=region,
        time_range=time_range,
        frequency=frequency,
        trigger_type=trigger_type,
        keywords=keywords,
        raw_query=query,
    )


async def parse_query(query: str) -> ParsedFilters:
    """解析用户自然语言查询 → 结构化过滤条件。

    优先级：缓存 → DeepSeek LLM → 关键词降级。

    Args:
        query: 用户自然语言查询字符串

    Returns:
        ParsedFilters 结构化过滤条件
    """
    # 1. 查缓存
    cached = _get_from_cache(query)
    if cached:
        return cached

    # 2. 调 LLM
    if not settings.DEEPSEEK_API_KEY:
        logger.warning("DEEPSEEK_API_KEY not configured, fallback to keyword")
        parsed = _fallback_keyword_parse(query)
        _save_to_cache(query, parsed)
        return parsed

    try:
        data = await _call_deepseek(query)
        # 兼容旧字段：date_range → time_range
        if "date_range" in data and "time_range" not in data:
            data["time_range"] = data["date_range"]
        # M-4 修复：防止 LLM 返回的 raw_query 覆盖用户原始查询
        data.pop("raw_query", None)
        parsed = ParsedFilters(raw_query=query, **data)
        _save_to_cache(query, parsed)
        logger.info(
            "LLM parsed query={} → topic={} region={} time_range={} freq={}",
            query[:50], parsed.topic, parsed.region,
            parsed.time_range, parsed.frequency,
        )
        return parsed
    except Exception as exc:
        logger.warning("LLM parse failed, fallback to keyword: {}", str(exc))
        parsed = _fallback_keyword_parse(query)
        _save_to_cache(query, parsed)
        return parsed
