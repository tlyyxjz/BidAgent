"""Agent 1: 意图解析 Agent。

职责：用户说"找上海最近7天的IT采购项目" → 拆解为 5 槽位（关键词/地区/预算/时间/品类）+ 多轮追问。

核心能力：
- 调用 DeepSeek V3 LLM 做意图理解
- 关键词降级兜底（LLM 不可用时走规则匹配）
- 多轮追问（slot 缺失时反问用户）

复用：app/llm/parser.py + app/llm/prompts.py + app/llm/schemas.py
"""

from __future__ import annotations

from typing import Any

from app.utils.logger import get_logger

logger = get_logger("agent.intent")


async def intent_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Agent 1: 意图解析（解析用户查询的 5 槽位）。

    输入 state:
        - query: str (必填) — 用户自然语言查询

    输出 state（新增）:
        - parsed_filters: ParsedFilters — 解析后的 5 槽位
        - topic: str — 主题
        - region: str — 地区
        - trigger_type: str — 触发类型
        - missing_slots: list[str] — 缺失的槽位（用于多轮追问）
    """
    from app.llm.parser import parse_query

    query = state.get("query", "")
    if not query:
        raise ValueError("state.query is required")

    logger.info("intent_agent started query={!r}", query)

    parsed = await parse_query(query)
    state["parsed_filters"] = parsed
    state["topic"] = parsed.topic
    state["region"] = parsed.region
    state["trigger_type"] = parsed.trigger_type

    # 检测缺失槽位（用于多轮追问，MVP 阶段只记录不阻塞）
    missing_slots = _detect_missing_slots(parsed)
    state["missing_slots"] = missing_slots
    if missing_slots:
        logger.info(
            "intent_agent missing slots: {} (will not block pipeline)",
            missing_slots,
        )

    # 兜底：topic 仍为 None 时用 raw_query 当 topic，避免查询无主题
    if not parsed.topic and parsed.raw_query:
        parsed.topic = parsed.raw_query
        # P2 修复：fallback 后重新同步 state["topic"]，避免与 parsed.topic 不一致
        # （原实现 state["topic"] 在 fallback 前已写入，仍是 None，下游读 state["topic"] 会拿到空值）
        state["topic"] = parsed.topic
        logger.info("intent_agent topic fallback to raw_query={}", parsed.topic[:30])

    logger.info(
        "intent_agent completed topic={} region={} trigger_type={}",
        parsed.topic, parsed.region, parsed.trigger_type,
    )
    return state


def _detect_missing_slots(parsed: Any) -> list[str]:
    """检测缺失的槽位。

    Args:
        parsed: ParsedFilters 对象

    Returns:
        缺失的槽位名列表（如 ["budget", "time_window"]）
    """
    missing: list[str] = []
    # 5 槽位：query / region / budget / time_window / category
    if not getattr(parsed, "topic", None):
        missing.append("topic")
    if not getattr(parsed, "region", None):
        missing.append("region")
    # budget / time_window / category 在 ParsedFilters 中是可选的，暂不强制
    return missing
