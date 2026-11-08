"""实时打标器 —— 给一条公告打结构化标签（行业 / 地区 / 公告类型）。

核心思想（对应《实时打标设计 v1.0》）：
- 抓全 → 实时打标 → 按标签筛，替代"关键词模糊匹配"；
- 标签只从公告文本里判断，判断不出就标"其他/未知"，不硬塞（沿用"可验证、不编造"）。

复用 app.llm.provider（多模型可切换），不另写 LLM 调用。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from app.llm.provider import (
    build_chat_payload,
    chat_endpoint,
    extract_content_and_usage,
    parse_json_lenient,
    resolve_provider,
)
from app.utils.logger import get_logger

logger = get_logger("tagger")

# 标签体系（对应设计文档第二节）
INDUSTRIES = ["IT", "医疗", "建筑", "教育", "能源", "交通", "其他"]
NOTICE_TYPES = ["招标", "中标", "更正", "其他"]

# 每个行业的"提示词说明"（仅作 LLM 判断辅助，不是枚举本身）
_INDUSTRY_HINTS = {
    "IT": "软件、硬件、服务器、信息化、系统集成、云、网络、计算机",
    "医疗": "医疗器械、药品、医院、卫生",
    "建筑": "工程、施工、装修、市政",
    "教育": "学校、教学设备、图书、培训",
    "能源": "电力、光伏、新能源、充电桩",
    "交通": "道路、桥梁、地铁、交通设施",
}


def _build_system_prompt() -> str:
    """从枚举动态生成 SYSTEM_PROMPT（消除"枚举↔prompt 两份手工维护"）。

    枚举变更时，prompt 自动跟随，不会漂移。
    """
    industry_lines = "\n".join(
        f"   - {name}：{_INDUSTRY_HINTS.get(name, '')}".rstrip()
        for name in INDUSTRIES
    )
    notice_types = " / ".join(NOTICE_TYPES)
    return f"""你是一个招投标公告的标签分类助手。请从公告文本中判断以下三个标签，只从文本判断，不要臆测：

1. industry（行业）：{" / ".join(INDUSTRIES)}
{industry_lines}

2. region（地区）：**只写省级行政区**（如"北京"、"山东"、"湖南"），不要带市/区，判断不出标"未知"
   - 只取文本中明确出现的采购方/项目所在地区，判断不出标"未知"

3. notice_type（公告类型）：{notice_types}

只输出一个 JSON 对象，格式：
{{"industry": "IT", "region": "北京", "notice_type": "中标"}}
"""


SYSTEM_PROMPT = _build_system_prompt()


def _build_user_prompt(title: str, content: str) -> str:
    """把公告标题+摘要拼成 user prompt（摘要过长则截断）。"""
    snippet = (content or "")[:800]
    return f"公告标题：{title}\n公告摘要：{snippet}"


async def _tag_one(title: str, content: str) -> dict[str, str]:
    """给一条公告打标签，返回 {"industry","region","notice_type"}。"""
    provider = resolve_provider("intent")
    payload = build_chat_payload(
        provider,
        SYSTEM_PROMPT,
        _build_user_prompt(title, content),
        temperature=0.1,
        max_tokens=500,
    )
    headers = {
        "Authorization": f"Bearer {provider.api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(chat_endpoint(provider), headers=headers, json=payload)
        resp.raise_for_status()
        content_text, _ = extract_content_and_usage(resp.json())
    data = parse_json_lenient(content_text)
    return _sanitize_tags(data)


def _sanitize_tags(data: Any) -> dict[str, str]:
    """把 LLM 输出规范化 + 枚举校验（可测纯函数）。

    关键：LLM 返回枚举外的值（幻觉标签）时，归一化到兜底值，防止脏标签写库。
    """
    if not isinstance(data, dict):
        data = {}
    industry = data.get("industry", "其他")
    notice_type = data.get("notice_type", "其他")
    region = data.get("region", "未知")
    if industry not in INDUSTRIES:
        industry = "其他"
    if notice_type not in NOTICE_TYPES:
        notice_type = "其他"
    # 地区归一化到省级：模型常写"湖南·长沙"/"山东-青岛"，统一截成省份
    if isinstance(region, str) and region.strip():
        region = region.strip().split("·")[0].split("-")[0].split("/")[0]
        region = region or "未知"
    else:
        region = "未知"
    return {
        "industry": industry,
        "region": region,
        "notice_type": notice_type,
    }


async def _tag_via_gateway(model: str, title: str, content: str, timeout: float = 120.0) -> dict[str, str]:
    """通过 new-api 网关调用指定模型打标（本地 Ollama / 云端 GLM 共用此通道）。

    网关统一记账；model 名决定路由（qwen2.5:7b → Ollama 渠道，glm-4-flash → GLM 渠道）。
    """
    import httpx

    from app.config import settings

    gateway = (getattr(settings, "LLM_BASE_URL", "") or "http://localhost:3001/v1").rstrip("/")
    key = getattr(settings, "LLM_API_KEY", "") or ""
    url = f"{gateway}/chat/completions"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers={"Authorization": f"Bearer {key}"}, json={
            "model": model,
            "messages": [
                {"role": "system", "content": _build_system_prompt()},
                {"role": "user", "content": _build_user_prompt(title, content)},
            ],
            "temperature": 0,
        })
        resp.raise_for_status()
        content_text = resp.json()["choices"][0]["message"]["content"]
        return _sanitize_tags(json.loads(content_text))


def _is_fallback_tags(tags: dict[str, str]) -> bool:
    """三项全是兜底值 = 该次打标失败（可触发降级）。"""
    return (tags.get("industry") == "其他"
            and tags.get("region") == "未知"
            and tags.get("notice_type") == "其他")


async def tag_notice(title: str, content: str = "") -> dict[str, str]:
    """给一条公告打标签（本地优先、云端兜底、绝不崩主链路）。

    顺序：Ollama(qwen2.5:7b，0元) → 失败/全兜底 → GLM(glm-4-flash，免费) → 兜底值。
    """
    try:
        tags = await _tag_via_gateway("qwen2.5:7b", title, content)
        if not _is_fallback_tags(tags):
            return tags
        logger.info("ollama tag all-fallback, falling back to glm title=%s", (title or "")[:40])
    except Exception as exc:  # noqa: BLE001 —— 本地模型不可控，降级云端
        logger.warning("ollama tag failed, fallback to glm: %s", type(exc).__name__)
    try:
        return await _tag_via_gateway("glm-4-flash", title, content)
    except Exception as exc:  # noqa: BLE001 —— LLM 不可控，兜底
        logger.warning("tag failed title=%s err=%s", (title or "")[:40], exc)
        return {"industry": "其他", "region": "未知", "notice_type": "其他"}


def _norm_item(n: Any) -> dict[str, Any] | None:
    """把一条公告规范化成 dict（含 title/content 的提取）；非 dict 返回 None。"""
    if not isinstance(n, dict):
        logger.warning("skip non-dict notice: {}", type(n).__name__)
        return None
    return n


def _title_of(n: dict[str, Any]) -> str:
    return n.get("title") or n.get("project_name") or ""


def _content_of(n: dict[str, Any]) -> str:
    return n.get("content") or n.get("core_content") or ""


async def _tag_item(n: dict[str, Any]) -> dict[str, Any]:
    """给一条规范化后的公告打标签并返回带标签的新 dict。"""
    tags = await tag_notice(_title_of(n), _content_of(n))
    item = dict(n)
    item.update(tags)
    return item


async def tag_many(notices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """批量打标（串行）：每条公告加 industry/region/notice_type，跳过非 dict 项。"""
    results: list[dict[str, Any]] = []
    for n in notices:
        item = _norm_item(n)
        if item is None:
            continue
        results.append(await _tag_item(item))
    return results


async def tag_many_concurrent(
    notices: list[dict[str, Any]], concurrency: int = 5
) -> list[dict[str, Any]]:
    """批量打标（并发版，控制并发数避免打爆 API）。"""
    sem = asyncio.Semaphore(concurrency)

    async def _run(n: Any) -> dict[str, Any] | None:
        item = _norm_item(n)
        if item is None:
            return None
        async with sem:
            return await _tag_item(item)

    results = await asyncio.gather(*(_run(n) for n in notices))
    return [r for r in results if r is not None]
