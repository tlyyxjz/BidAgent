"""抓取端点：POST /api/scrape, POST /api/scrape/batch, GET /api/scrape/{job_id}。

工程规范：
- 所有路由用 async/await。
- 统一错误响应 {code, data, msg}。
- API key 认证 + 速率限制（免费 5/天，付费无限）。
- batch 最多 100 个 URL。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import verify_api_key
from app.core.queue import enqueue_scrape_job, get_job_status
from app.core.rate_limit import check_and_increment_rate_limit
from app.core.scraper import ScrapeError, scraper
from app.models.database import get_db
from app.models.user import User
from app.templates import list_templates
from app.utils.logger import get_logger

logger = get_logger("scrape_api")

router = APIRouter(prefix="/api/scrape", tags=["scrape"])

BATCH_MAX_URLS = 100


# ==== 请求/响应模型 ====

class ScrapeRequest(BaseModel):
    """单次抓取请求。"""
    url: str = Field(..., description="目标 URL")
    selectors: dict[str, str] | None = Field(
        default=None, description="字段名 -> CSS 选择器映射"
    )
    list_selector: str | None = Field(default=None, description="列表项选择器")
    wait_for_selector: str | None = Field(
        default=None, description="等待该元素出现表示 JS 渲染完成"
    )
    next_page_selector: str | None = Field(default=None, description="下一页按钮选择器")
    max_pages: int = Field(default=1, ge=1, le=50, description="最大翻页数")
    template: str | None = Field(
        default=None, description="内置模板名：amazon / reddit / news"
    )
    auto_save: bool = Field(
        default=False,
        description="是否自动把抓取结果写入 Tender 表（招投标场景建议开启）",
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("url 不能为空")
        v = v.strip()
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("url 必须以 http:// 或 https:// 开头")
        return v


class BatchScrapeRequest(BaseModel):
    """批量抓取请求。"""
    items: list[ScrapeRequest] = Field(..., description="抓取任务列表")

    @field_validator("items")
    @classmethod
    def validate_items(cls, v: list[ScrapeRequest]) -> list[ScrapeRequest]:
        if not v:
            raise ValueError("items 不能为空")
        if len(v) > BATCH_MAX_URLS:
            raise ValueError(f"items 最多 {BATCH_MAX_URLS} 个 URL")
        return v


# ==== 端点 ====

@router.post("")
async def scrape(
    payload: ScrapeRequest,
    db: AsyncSession = Depends(get_db),
    auth: tuple[User, Any, str] = Depends(verify_api_key),
) -> JSONResponse:
    """POST /api/scrape - 同步抓取单个 URL，立即返回结构化 JSON。"""
    user, _api_key_obj, raw_api_key = auth

    # 速率限制（超出时抛 HTTPException(429)，由全局 handler 统一格式化）
    await check_and_increment_rate_limit(raw_api_key, user.plan)

    try:
        # auto_save 字段不传给 scraper（scraper 不识别）
        request_data = payload.model_dump(exclude={"auto_save"})
        result = await scraper.scrape(request_data)

        # 命题硬要求：抓取后自动入库 Tender 表（auto_save=True 时）
        ingest_summary = None
        if payload.auto_save and result.get("data"):
            try:
                from app.processors.simhash import compute_simhash
                from app.processors.tender_ingestor import ingest_scrape_result
                ingest_summary = await ingest_scrape_result(
                    scrape_result=result,
                    template=payload.template,
                    simhash_computer=compute_simhash,
                )
                result["ingest"] = ingest_summary
            except Exception as ingest_exc:  # noqa: BLE001
                logger.exception("auto_save 入库失败 url=%s", payload.url)
                result["ingest"] = {"error": str(ingest_exc)}

        return JSONResponse(
            status_code=200,
            content={"code": 200, "data": result, "msg": "ok"},
        )
    except ScrapeError as exc:
        logger.warning("scrape failed url=%s err=%s", payload.url, exc)
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"code": 502, "data": None, "msg": "抓取失败"},
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("scrape unexpected error url=%s err=%s", payload.url, exc)
        return JSONResponse(
            status_code=500,
            content={"code": 500, "data": None, "msg": "服务器内部错误"},
        )


@router.post("/batch")
async def scrape_batch(
    payload: BatchScrapeRequest,
    auth: tuple[User, Any, str] = Depends(verify_api_key),
) -> JSONResponse:
    """POST /api/scrape/batch - 批量抓取，入队后立即返回 job_id 列表供轮询。"""
    user, _api_key_obj, raw_api_key = auth

    # batch 按实际 URL 数量计速率限制
    await check_and_increment_rate_limit(
        raw_api_key, user.plan, count=len(payload.items)
    )

    job_ids: list[str] = []
    errors: list[dict[str, Any]] = []
    for idx, item in enumerate(payload.items):
        try:
            job_id = await enqueue_scrape_job(
                user_id=user.id,
                request_data=item.model_dump(),
            )
            job_ids.append(job_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("batch enqueue failed url=%s err=%s", item.url, exc)
            errors.append({"index": idx, "url": item.url, "error": str(exc)})

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "code": 202,
            "data": {
                "job_ids": job_ids,
                "total": len(job_ids),
                "errors": errors,
            },
            "msg": "ok" if not errors else f"部分任务入队失败 ({len(errors)})",
        },
    )


@router.get("/{job_id}")
async def get_job(
    job_id: str,
    auth: tuple[User, Any, str] = Depends(verify_api_key),
) -> JSONResponse:
    """GET /api/scrape/{job_id} - 查询异步任务状态。"""
    _user, _api_key_obj, _raw_api_key = auth  # 认证必须通过

    if not job_id or len(job_id) > 64:
        return JSONResponse(
            status_code=400,
            content={"code": 400, "data": None, "msg": "job_id 无效"},
        )

    job = await get_job_status(job_id)
    if job is None:
        return JSONResponse(
            status_code=404,
            content={"code": 404, "data": None, "msg": "job not found"},
        )

    return JSONResponse(
        status_code=200,
        content={"code": 200, "data": job, "msg": "ok"},
    )


@router.get("/templates/list")
async def list_template_names(
    auth: tuple[User, Any, str] = Depends(verify_api_key),
) -> JSONResponse:
    """GET /api/scrape/templates/list - 列出内置模板。"""
    _user, _api_key_obj, _raw_api_key = auth
    return JSONResponse(
        status_code=200,
        content={"code": 200, "data": {"templates": list_templates()}, "msg": "ok"},
    )
