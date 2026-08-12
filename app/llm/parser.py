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
from app.llm.provider import (
    build_chat_payload,
    chat_endpoint,
    extract_content_and_usage,
    parse_json_lenient,
    resolve_provider,
)
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
    """调用 LLM 意图解析（OpenAI 兼容协议，多 provider 可切换）。

    函数名保留 _call_deepseek 以兼容历史调用点，实际 provider 由
    LLM_PROVIDER 决定（deepseek/dashscope/zhipu/openai）。

    Raises:
        httpx.HTTPError: 网络或 API 错误
        ValueError: 响应解析失败
    """
    provider = resolve_provider("intent")
    payload = build_chat_payload(
        provider,
        INTENT_SYSTEM_PROMPT,
        build_intent_prompt(query),
        temperature=0.1,
        max_tokens=500,
    )
    headers = {
        "Authorization": f"Bearer {provider.api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT_SECONDS) as client:
        resp = await client.post(
            chat_endpoint(provider),
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        content, _ = extract_content_and_usage(data)
        return parse_json_lenient(content)


def _fix_topic(parsed_dict: dict, raw_query: str) -> dict:
    """代码兜底：如果 LLM 返回的 topic 过长或含空格，用拆词取核心词。

    LLM 有时会把整句当 topic（如"北京教育系统的中标公告 最近30天"），
    这里用 processor_agent._split_topic 拆词，取第一个有意义的关键词。
    """
    topic = parsed_dict.get("topic", "")
    if not topic:
        return parsed_dict
    # topic 过长（>6字）或含空格/时间词，需要拆
    needs_split = (
        len(topic) > 6
        or " " in topic
        or "最近" in topic
        or "天" in topic
        or "公告" in topic
        or "招标" in topic
    )
    if not needs_split:
        return parsed_dict
    try:
        from app.agents.processor_agent import _split_topic
        words = _split_topic(topic)
        if words:
            parsed_dict["topic"] = words[0]
            # 同时更新 keywords
            keywords = parsed_dict.get("keywords") or []
            for w in words:
                if w not in keywords:
                    keywords.append(w)
            parsed_dict["keywords"] = keywords
    except Exception:
        pass
    return parsed_dict


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

    # 机构名识别（医院/大学/公司/集团/局/委员会/院/中心→当 topic，不当 region）
    org_suffixes = ["医院", "大学", "学院", "公司", "集团", "局", "委员会",
                    "厅", "院", "中心", "研究所", "研究院"]
    for suffix in org_suffixes:
        if suffix in query:
            # 提取机构名（suffix 前 2-8 字符 + suffix）
            idx = query.find(suffix)
            start = max(0, idx - 6)
            org_name = query[start:idx + len(suffix)]
            topic = org_name
            keywords.append(org_name)
            break

    # 地区识别（仅纯地名，机构名已优先识别为 topic）
    # 修复：城市名优先于省份名（"台州"比"浙江"更精确），
    #       且匹配到省份时继续检查是否有城市，城市加入 keywords
    if topic is None:
        # 城市名优先（更精确的地域）
        _CITY_NAMES = [
            "台州", "杭州", "宁波", "温州", "嘉兴", "湖州",
            "绍兴", "金华", "衢州", "舟山", "丽水",
            "深圳", "青岛", "大连", "厦门", "苏州", "无锡", "南京",
        ]
        _PROVINCES = [
            "北京", "上海", "广东", "浙江", "江苏", "四川",
            "湖北", "山东", "河南", "福建", "安徽", "湖南", "江西",
            "辽宁", "吉林", "河北", "山西", "陕西", "甘肃", "云南",
            "贵州", "海南", "青海", "天津", "重庆",
        ]
        # 先匹配城市（精确优先）
        matched_city = None
        for c in _CITY_NAMES:
            if c in query:
                matched_city = c
                break
        # 再匹配省份
        matched_province = None
        for p in _PROVINCES:
            if p in query:
                matched_province = p
                break
        # region 优先用城市（更精确），没有城市才用省份
        if matched_city:
            region = matched_city
        elif matched_province:
            region = matched_province
        # 如果同时有省份和城市，把城市也加入 keywords（确保搜索时能匹配）
        if matched_city and matched_province and matched_city != matched_province:
            if matched_city not in keywords:
                keywords.append(matched_city)

    # 主题识别（命题示例：服务器/充电桩；机构名已识别则跳过）
    if topic is None:
        topics = {
            "服务器": ["服务器", "机架", "刀片"],
            "充电桩": ["充电桩", "充电站", "充电设备"],
            "IT设备": ["IT", "信息化", "计算机", "网络设备"],
            "医疗器械": ["医疗", "器械"],
            "建筑工程": ["建筑", "工程", "施工"],
            "政府采购": ["政府采购", "采购"],
            "教育": ["教育", "学校", "教学", "图书", "培训"],
            "环保": ["环保", "环境", "生态", "污水处理"],
            "安防": ["安防", "监控", "消防", "安保"],
            "保洁": ["保洁", "物业", "绿化", "环卫"],
        }
        for t, kws in topics.items():
            if any(k in query for k in kws):
                topic = t
                keywords.extend(k for k in kws if k in query)
                break

    # 时间识别（支持 1天/5天/7天/15天/30天/3个月）
    if ("最近" in query and "3月" in query) or "3个月" in query:
        time_range = "3m"
    elif ("最近" in query and "一月" in query) or "1个月" in query or "30天" in query:
        time_range = "30d"
    elif "15天" in query:
        time_range = "15d"
    elif "5天" in query:
        time_range = "5d"
    elif "本周" in query or "7天" in query:
        time_range = "7d"
    elif "今天" in query or "1天" in query:
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

    # 公告类型识别（映射为 DB 英文值：award/correction/tender）
    notice_types: list[str] = []
    if "中标" in query or "成交" in query:
        notice_types = ["award"]
    elif "更正" in query or "变更" in query:
        notice_types = ["correction"]
    elif "招标" in query or "采购" in query:
        notice_types = ["tender"]

    return ParsedFilters(
        topic=topic,
        region=region,
        time_range=time_range,
        frequency=frequency,
        trigger_type=trigger_type,
        keywords=keywords,
        notice_types=notice_types,
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

    # 2. 调 LLM（provider 未配置 key 时降级关键词解析）
    try:
        resolve_provider("intent")
    except RuntimeError:
        logger.warning("LLM provider API key not configured, fallback to keyword")
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
        # P0 修复：代码兜底，修正 LLM 返回的 topic 过长问题
        data = _fix_topic(data, query)
        parsed = ParsedFilters(raw_query=query, **data)
        # P3 修复：LLM 返回有效JSON但关键字段全空时走 fallback（偶发空响应问题）
        if not parsed.topic and not parsed.region and not parsed.keywords and not parsed.time_range:
            logger.warning("LLM returned all-empty fields, fallback to keyword parse")
            parsed = _fallback_keyword_parse(query)
            _save_to_cache(query, parsed)
            return parsed
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
