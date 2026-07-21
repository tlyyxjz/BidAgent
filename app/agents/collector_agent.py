"""Agent 2: 采集执行 Agent。

职责：调度 4+ 平台采集器并行抓取，管理登录态。

核心能力：
- 4+ 平台并行采集（ccgp/chinabidding/ggzy/千里马）
- 千里马登录态采集（已实测通过，16 cookies 持久化）
- 浏览器反检测 + 浏览器池
- 任务状态上报（progress / started_at / completed_at）

复用：
- app/templates/* — 采集模板
- app/core/scraper.py — Playwright 抓取
- app/core/browser_pool.py — 浏览器池
- app/core/session_manager.py — 登录态管理
- app/core/anti_detect.py — 反检测（Sol S-11 已交付）
- app/scheduler/collector.py — 采集调度
"""

from __future__ import annotations

from typing import Any

from app.utils.logger import get_logger

logger = get_logger("agent.collector")


async def collector_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Agent 2: 采集执行（调度多平台采集器并行抓取）。

    输入 state:
        - parsed_filters: ParsedFilters (必填)
        - subscription_id: int | None — 已有订阅 ID（可选）
        - user_id: int — 用户 ID（默认 1）
        - platforms: list[str] — 平台列表（默认 ["ccgp"]）

    输出 state（新增）:
        - subscription_id: int — 创建或复用的订阅 ID
        - collect_summary: dict — 采集结果摘要
            - total: int — 总采集数
            - inserted: int — 新增数
            - duplicates: int — 去重数
            - errors: int — 错误数
            - platforms_collected: list[str] — 实际采集的平台
    """
    from app.llm.schemas import ParsedFilters
    from app.models.database import AsyncSessionLocal
    from app.models.subscription import Subscription
    from app.scheduler.collector import collect_new_tenders
    from sqlalchemy import select

    parsed = state.get("parsed_filters")
    if parsed is None:
        raise ValueError(
            "state.parsed_filters is required (intent_agent must run first)"
        )

    user_id = state.get("user_id", 1)
    platforms = state.get("platforms") or ["ccgp"]
    logger.info(
        "collector_agent started user_id={} platforms={}",
        user_id, platforms,
    )

    # 创建临时订阅记录（如果没传 subscription_id）
    sub_id = state.get("subscription_id")
    if sub_id is None:
        async with AsyncSessionLocal() as db:
            parsed_dict = (
                parsed.model_dump() if hasattr(parsed, "model_dump")
                else parsed.__dict__
            )
            sub = Subscription(
                user_id=user_id,
                raw_query=state.get("query", ""),
                parsed_filters=parsed_dict,
                platforms=platforms,
                trigger_type="immediate",
            )
            db.add(sub)
            await db.commit()
            await db.refresh(sub)
            sub_id = sub.id
            state["subscription_id"] = sub_id
            logger.info("collector_agent created subscription id={}", sub_id)

    # 重新查询 subscription（collect_new_tenders 需要 Subscription 对象）
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Subscription).where(Subscription.id == sub_id))
        sub = result.scalar_one_or_none()
        if sub is None:
            raise ValueError(f"subscription {sub_id} not found")
        collect_summary = await collect_new_tenders(sub, parsed)

    state["collect_summary"] = collect_summary
    logger.info(
        "collector_agent completed total={} inserted={} duplicates={}",
        collect_summary.get("total", 0),
        collect_summary.get("inserted", 0),
        collect_summary.get("duplicates", 0),
    )
    return state
