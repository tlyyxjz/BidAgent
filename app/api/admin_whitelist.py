"""管理后台 - v4.1 §5.2 source whitelist management 路由。

从 app/api/admin.py 拆出（保证单文件 ≤300 行，公开接口不变）。
本模块在 import 时将路由注册到 admin.admin_router 上：
- GET  /admin/sources/whitelist
- POST /admin/sources/whitelist
- POST /admin/sources/whitelist/{domain}/decommission
- POST /admin/sources/whitelist/{domain}/recommission

依赖 admin.py 中已定义的 admin_router（通过底部 import 触发本模块加载，
避免循环导入：admin.py 先定义 admin_router，再 import 本模块）。
"""
from __future__ import annotations

from fastapi import Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.admin import admin_router
from app.api.auth import verify_admin
from app.utils.logger import get_logger

logger = get_logger("admin_whitelist")


# ==== v4.1 §5.2 source whitelist management ====

class AddSourceRequest(BaseModel):
    """Add a source to the whitelist."""
    domain: str = Field(..., description="domain or URL, auto-normalized")
    platform_name: str = Field(..., max_length=200)
    platform_type: str = Field(default="commercial", description="government/authorized/commercial/unknown")
    notes: str = Field(default="", max_length=500)


class DecommissionRequest(BaseModel):
    """Decommission a source."""
    reason: str = Field(..., min_length=1, max_length=500, description="decommission reason, required")


@admin_router.get("/sources/whitelist")
async def list_whitelist_sources(
    status: str | None = None,
    _: None = Depends(verify_admin),
) -> JSONResponse:
    """List all sources in the whitelist (filterable by status)."""
    from app.core.source_whitelist import source_whitelist

    sources = source_whitelist.list_sources(status=status)
    return JSONResponse(
        status_code=200,
        content={"code": 200, "data": sources, "msg": "ok"},
    )


@admin_router.post("/sources/whitelist")
async def add_whitelist_source(
    req: AddSourceRequest,
    _: None = Depends(verify_admin),
) -> JSONResponse:
    """Add a new source to the whitelist."""
    from app.core.source_whitelist import source_whitelist

    try:
        entry = await source_whitelist.add_source(
            domain=req.domain,
            platform_name=req.platform_name,
            platform_type=req.platform_type,
            notes=req.notes,
        )
        logger.info(
            "whitelist source added domain=%s platform=%s",
            entry.domain, entry.platform_name,
        )
        return JSONResponse(
            status_code=201,
            content={"code": 201, "data": entry.to_dict(), "msg": "created"},
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"code": 400, "data": None, "msg": str(exc)},
        )


@admin_router.post("/sources/whitelist/{domain}/decommission")
async def decommission_whitelist_source(
    domain: str,
    req: DecommissionRequest,
    _: None = Depends(verify_admin),
) -> JSONResponse:
    """Decommission a source (stop new scraping, do not delete historical data)."""
    from app.core.source_whitelist import source_whitelist

    try:
        entry = await source_whitelist.decommission(domain, reason=req.reason)
        logger.warning(
            "whitelist source decommissioned domain=%s reason=%s",
            entry.domain, req.reason,
        )
        return JSONResponse(
            status_code=200,
            content={"code": 200, "data": entry.to_dict(), "msg": "decommissioned"},
        )
    except KeyError as exc:
        return JSONResponse(
            status_code=404,
            content={"code": 404, "data": None, "msg": str(exc)},
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"code": 400, "data": None, "msg": str(exc)},
        )


@admin_router.post("/sources/whitelist/{domain}/recommission")
async def recommission_whitelist_source(
    domain: str,
    _: None = Depends(verify_admin),
) -> JSONResponse:
    """Recommission a previously decommissioned source."""
    from app.core.source_whitelist import source_whitelist

    try:
        entry = await source_whitelist.recommission(domain)
        logger.info("whitelist source recommissioned domain=%s", entry.domain)
        return JSONResponse(
            status_code=200,
            content={"code": 200, "data": entry.to_dict(), "msg": "recommissioned"},
        )
    except KeyError as exc:
        return JSONResponse(
            status_code=404,
            content={"code": 404, "data": None, "msg": str(exc)},
        )
