"""Scheduler 循环入口（Docker 容器启动点）。

APP_ROLE=scheduler 时运行此模块，定时扫描订阅并触发推送。
独立于 web 服务，避免重复推送。

工程规范：
- 单一职责（只做定时调度）
- 优雅退出（SIGTERM/SIGINT）
- 失败重试 + 日志
- 默认每 60 秒扫描一次到期的订阅
"""
# pragma: no cover

from __future__ import annotations

import asyncio
import signal

from app.config import settings
from app.scheduler.subscription import run_scheduled_subscriptions
from app.utils.logger import get_logger, setup_logging

logger = get_logger("scheduler_loop")

# 默认扫描间隔（秒）
SCAN_INTERVAL_SECONDS = 60


async def main() -> None:
    """Scheduler 主循环。"""
    setup_logging()
    logger.info("scheduler_loop starting APP_ROLE={}", settings.APP_ROLE)

    if settings.APP_ROLE != "scheduler":
        logger.warning(
            "APP_ROLE != scheduler (got {}), exit. "
            "Set APP_ROLE=scheduler to run this container.",
            settings.APP_ROLE,
        )
        return

    # 优雅退出
    stop_event = asyncio.Event()

    def _on_signal(signum: int, _frame) -> None:
        logger.info("received signal {}, shutting down...", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    # 主循环
    logger.info("scheduler_loop running, scan interval={}s", SCAN_INTERVAL_SECONDS)
    while not stop_event.is_set():
        try:
            triggered = await run_scheduled_subscriptions()
            if triggered > 0:
                logger.info("scheduler_loop triggered {} subscriptions", triggered)
        except Exception:
            logger.exception("scheduler_loop iteration failed")

        # 等待下一次扫描（或被 stop_event 中断）
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=SCAN_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass  # 正常超时，继续下一轮

    logger.info("scheduler_loop stopped")


if __name__ == "__main__":
    asyncio.run(main())
