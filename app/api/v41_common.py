"""v4.1 端点共享响应助手（从 v41_extract.py 拆出）。

提供统一成功/错误响应包装，供 v41_extract / v41_organizations / v41_stats 复用，
独立成模块以避免端点模块之间的循环导入。
"""
from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


def _ok(data: Any) -> JSONResponse:
    """统一成功响应。"""
    return JSONResponse({"code": 0, "data": data, "msg": "ok"})


def _err(msg: str, code: int = 404) -> JSONResponse:
    """统一错误响应。"""
    return JSONResponse({"code": code, "data": None, "msg": msg}, status_code=code)
