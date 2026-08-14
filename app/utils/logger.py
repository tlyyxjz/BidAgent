"""结构化日志配置，带 request_id 上下文。

工程规范：
- 所有日志带 request_id 上下文（ContextVar）。
- INFO/WARN/ERROR 级别，统一格式。
- 第三方库日志降级到 WARNING，避免噪音。
- 使用 loguru 作为底层引擎，同时保留标准 logging 接口以兼容 slowapi / uvicorn。
"""

from __future__ import annotations

import logging
import sys
import uuid
from contextvars import ContextVar
from typing import Any

from loguru import logger as loguru_logger

# 请求级上下文变量
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

# 统一日志格式
_LOG_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} [{level}] [{extra[request_id]}] "
    "{name}: {message}"
)


class RequestIdFilter(logging.Filter):
    """标准 logging 注入 request_id（用于第三方库日志）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def _request_id_patcher(record: Any) -> None:
    """loguru patcher：每条日志记录时从 ContextVar 同步 request_id 到 extra。

    没有这个 patcher，`{extra[request_id]}` 永远是默认值 "-"，
    中间件设置的 request_id 无法渗透到端点日志中。
    """
    record["extra"]["request_id"] = request_id_var.get()


def setup_logging(level: str = "INFO") -> None:
    """初始化 loguru 结构化日志。

    Args:
        level: 日志级别，默认 INFO。
    """
    # 清掉 loguru 默认 handler
    loguru_logger.remove()

    # patcher 会在每条日志记录时调用，从 ContextVar 读取当前 request_id
    loguru_logger.configure(
        extra={"request_id": "-"},
        patcher=_request_id_patcher,
    )
    loguru_logger.add(
        sys.stdout,
        format=_LOG_FORMAT,
        level=level,
        enqueue=False,
        backtrace=False,
        diagnose=False,
    )

    # 拦截标准 logging，统一走 loguru
    class _InterceptHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
            try:
                level = loguru_logger.level(record.levelname).name
            except ValueError:
                level = record.levelno
            frame, depth = logging.currentframe(), 2
            while frame and frame.f_code.co_filename == logging.__file__:
                frame = frame.f_back
                depth += 1
            loguru_logger.opt(depth=depth, exception=record.exc_info).log(
                level, record.getMessage()
            )

    logging.root.handlers = [_InterceptHandler()]
    logging.root.setLevel(level)

    # 第三方库噪音降级
    for name in ("httpx", "httpcore", "sqlalchemy", "asyncio", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)


def new_request_id() -> str:
    """生成新的 request_id 并写入上下文。"""
    rid = uuid.uuid4().hex[:12]
    request_id_var.set(rid)
    return rid


def get_logger(name: str = "bidagent") -> "Any":
    """获取绑定 request_id 上下文的 logger。"""
    return loguru_logger.bind(name=name)
