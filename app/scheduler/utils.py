"""调度模块公共工具函数。

C-4 修复：从 subscription.py 拆分出来，让 subscription.py 控制在 300 行以内。
"""

from __future__ import annotations

from datetime import datetime, timezone

from croniter import croniter

from app.utils.logger import get_logger

logger = get_logger("scheduler.utils")


def utc_now() -> datetime:
    """当前 UTC 时间（带 tzinfo）。"""
    return datetime.now(timezone.utc)


def escape_like(value: str) -> str:
    r"""转义 LIKE 查询的通配符（M-7 修复）。

    Args:
        value: 待转义字符串

    Returns:
        转义后字符串（\\\\ → \\\\\\\\，% → \\\\%，_ → \\\_）

    注意（M-1 修复）：调用方使用 .like() / .contains() 时必须传 escape="\\\\"，
                      否则转义无效。推荐用 safe_like() / safe_contains()。
    """
    if not value:
        return value
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# M-1 修复：LIKE_ESCAPE 字符常量，配套 escape_like 使用
LIKE_ESCAPE = "\\"


def safe_like(column, value: str):
    """构造 LIKE 'value' 查询，自动转义 + 指定 escape 字符。

    用法：stmt = stmt.where(safe_like(Tender.project_name, "上海%"))
    """
    return column.like(escape_like(value), escape=LIKE_ESCAPE)


def safe_contains(column, value: str):
    """构造 LIKE '%value%' 查询，自动转义 + 指定 escape 字符。

    用法：stmt = stmt.where(safe_contains(Tender.project_name, "上海"))
    """
    return column.contains(escape_like(value), escape=LIKE_ESCAPE)


def is_cron_due(
    cron_expr: str,
    last_run: datetime | None,
    now: datetime,
) -> bool:
    """判断 cron 表达式自上次运行后是否到了触发时间。

    C-3 修复：不再每次扫描都触发，必须 cron 到期才推送。
    S-6 修复：last_run=None 时返回 False，避免订阅刚创建就立即触发。

    **None 语义**：last_run=None 表示"从未推送过"，本函数返回 False。
    调用方若希望新订阅立即触发，应在调用前用 `sub.created_at` 兜底；
    若希望"立即触发一次"，调用方应直接调 trigger_subscription(force=True)。

    Args:
        cron_expr: cron 表达式（5 字段，如 "0 9 * * *"）
        last_run: 上次推送时间。None → 返回 False（不触发）
        now: 当前时间

    Returns:
        True 表示应该推送，False 表示未到时间
    """
    if not cron_expr:
        return False

    # 一次性触发（"once:09:00" 格式，由调用方决定是否删除）
    if cron_expr.startswith("once:"):
        return True

    # S-6 修复：last_run=None 时返回 False，等下一个 cron 触发点
    if last_run is None:
        return False

    try:
        base = last_run
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
        itr = croniter(cron_expr, base)
        next_run = itr.get_next(datetime)
        return next_run <= now
    except Exception as exc:  # noqa: BLE001
        logger.warning("invalid cron expr={} err={}", cron_expr, exc)
        return False
