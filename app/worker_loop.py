"""Worker 循环入口（Docker 容器启动点）。

APP_ROLE=worker 时运行此模块，从 Redis 队列消费采集任务。
独立于 web 服务，避免重复抓取。

工程规范：
- 单一职责（只做任务消费）
- 优雅退出（SIGTERM/SIGINT）
- 失败重试 + 日志

注意：此 worker 使用 RQ 库的标准 worker 模式（run_scrape_job_sync 入口）。
"""
# pragma: no cover

from __future__ import annotations

from rq import Connection, Queue, Worker

from app.config import settings
from app.core.queue import _get_redis_connection
from app.utils.logger import get_logger, setup_logging

logger = get_logger("worker_loop")

# RQ 队列名称
QUEUE_NAME = "scrapeflow"


def main() -> None:
    """Worker 主循环（RQ 同步 worker）。

    RQ 的 Worker 是同步实现，在主线程运行。
    """
    setup_logging()
    logger.info("worker_loop starting APP_ROLE={}", settings.APP_ROLE)

    if settings.APP_ROLE != "worker":
        logger.warning(
            "APP_ROLE != worker (got {}), exit. "
            "Set APP_ROLE=worker to run this container.",
            settings.APP_ROLE,
        )
        return

    # 优雅退出（RQ Worker 内置信号处理）
    conn = _get_redis_connection()

    with Connection(conn):
        worker = Worker([Queue(QUEUE_NAME)])
        logger.info("worker_loop ready, consuming queue={}", QUEUE_NAME)
        try:
            worker.work(with_scheduler=False)
        except KeyboardInterrupt:
            logger.info("worker_loop stopped by KeyboardInterrupt")
        except Exception:
            logger.exception("worker_loop crashed")
            raise

    logger.info("worker_loop stopped")


if __name__ == "__main__":
    main()
