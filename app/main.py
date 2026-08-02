"""FastAPI 入口 - ScrapeFlow API.

工程规范：
- 所有中间件 async/await。
- 统一错误响应 {code, data, msg}。
- 结构化日志带 request_id 上下文。
- Admin 路由不能被认证中间件拦截（admin_router 独立挂载，verify_admin 内部依赖）。
- lifespan 钩子初始化数据库表。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi.middleware import SlowAPIMiddleware

from app.api.admin import admin_router
from app.api.agents import router as agents_router
from app.api.scrape import router as scrape_router
from app.api.subscribe import router as subscribe_router
from app.api.tender import router as tender_router
from app.api.ui import router as ui_router
from app.api.evidence_demo import router as evidence_demo_router
from app.api.real_demo import router as real_demo_router
from app.api.demo_api import router as demo_router, demo_org_by_name
from app.config import settings
from app.core.rate_limit import limiter
from app.models.database import engine, get_db, init_database
# 导入所有 ORM 模型，确保 create_all 能创建新表
from app.models.subscription import Subscription, PushLog  # noqa: F401
from app.models.tender import Tender  # noqa: F401
from app.utils.logger import get_logger, new_request_id, setup_logging

# 初始化日志（必须在导入其他模块后第一时间）
setup_logging()
logger = get_logger("main")

DATA_DIRECTORY = Path("data")


def _validate_data_dir(configured_path: str, name: str) -> None:
    """新-8 修复：校验配置目录在 data/ 范围内，防止被配到敏感位置。

    Args:
        configured_path: 配置的目录路径（相对或绝对）
        name: 配置项名（用于日志）

    Raises:
        RuntimeError: 路径不在 data/ 范围内
    """
    target = Path(configured_path).resolve()
    data_root = DATA_DIRECTORY.resolve()
    try:
        target.relative_to(data_root)
    except ValueError:
        raise RuntimeError(
            f"{name}='{configured_path}' 不在 data/ 目录范围内，"
            f"resolved={target}, data_root={data_root}. "
            f"请配置为 data/ 下的子目录。"
        ) from None
    target.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：初始化数据库表，退出时释放连接。"""
    DATA_DIRECTORY.mkdir(parents=True, exist_ok=True)
    logger.info("starting ScrapeFlow API, data_dir=%s", DATA_DIRECTORY.resolve())

    # 新-8 修复：校验 COOKIE_DIR / ATTACHMENT_DIR 在 data/ 目录范围内
    # M-6 修复（第四轮）：补上 REPORT_OUTPUT_DIR 校验
    # Sol S-11：补上 ANTI_DETECT_SESSION_DIR 校验（登录态文件安全）
    _validate_data_dir(settings.COOKIE_DIR, "COOKIE_DIR")
    _validate_data_dir(settings.ATTACHMENT_DIR, "ATTACHMENT_DIR")
    _validate_data_dir(settings.REPORT_OUTPUT_DIR, "REPORT_OUTPUT_DIR")
    _validate_data_dir(settings.ANTI_DETECT_SESSION_DIR, "ANTI_DETECT_SESSION_DIR")

    # 新-1/新-2 修复：统一 init_database（PRAGMA + create_all + 轻量迁移）
    await init_database()

    # Sentry（可选）
    if settings.SENTRY_DSN:
        try:
            import sentry_sdk  # type: ignore[import-not-found]

            sentry_sdk.init(dsn=settings.SENTRY_DSN, traces_sample_rate=0.1)
            logger.info("sentry initialized")
        except Exception as exc:  # noqa: BLE001
            logger.warning("sentry init failed: %s", exc)

    yield

    logger.info("shutting down")
    await engine.dispose()


app = FastAPI(
    title="标小智 - 智能招投标助手",
    description="AI+金融方向的招投标数据服务系统",
    version="2.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.state.limiter = limiter


# ==== 中间件 ====

@app.middleware("http")
async def request_id_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    """为每个请求注入 request_id（写入日志上下文 + 响应头）。"""
    rid = new_request_id()
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    return response


# 全局异常处理器（统一 {code, data, msg} 格式）
def register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器。"""

    @app.exception_handler(HTTPException)
    async def http_exception_handler(
        request: Request, exc: HTTPException
    ) -> JSONResponse:
        """统一 HTTPException 响应格式。"""
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.status_code,
                "data": None,
                "msg": exc.detail if isinstance(exc.detail, str) else str(exc.detail),
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """422 参数校验错误统一格式。"""
        # exc.errors() 可能含不可序列化对象（ValueError 等），转 str
        from fastapi.encoders import jsonable_encoder

        return JSONResponse(
            status_code=422,
            content={
                "code": 422,
                "data": None,
                "msg": "参数校验失败",
                "errors": jsonable_encoder(exc.errors()),
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        """兜底处理未捕获异常。"""
        logger.exception("unhandled exception: path=%s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={"code": 500, "data": None, "msg": "服务器内部错误"},
        )


register_exception_handlers(app)
app.add_middleware(SlowAPIMiddleware)
# C-4 修复：CORS 严格配置
_cors_origins = settings.cors_origin_list
if "*" in _cors_origins:
    logger.warning(
        "CORS_ORIGINS contains '*' — NOT recommended for production. "
        "Set CORS_ORIGINS to specific domains in production."
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)


# ==== 路由挂载 ====

# /admin 不受 API key 中间件影响（admin_router 内部用 verify_admin 依赖）
app.include_router(admin_router)
# /api/scrape 必须传 Bearer API key
app.include_router(scrape_router)
# /api/agents 多 Agent 协作（答辩差异化亮点）
app.include_router(agents_router)
# /api/subscriptions 订阅 + 增量推送（命题第 5/6 项硬要求）
app.include_router(subscribe_router)
# /api/tenders 招标信息查询 + admin 注入
app.include_router(tender_router)
# /ui Web UI（命题 Demo 视频用，无需认证）
app.include_router(ui_router)
app.include_router(evidence_demo_router)
# /api/demo Demo 数据接口（Turbo-W3 前端页面用，无需认证）
app.include_router(real_demo_router)
# /api/demo Demo data (incl. /api/demo/orgs/by-name/{name} 6-dim credit)
app.include_router(demo_router)

@app.get('/api/org/{name:path}', include_in_schema=False, tags=['demo'])
async def org_by_name_alias(name: str, db=Depends(get_db)):
    """Frontend alias: /api/org/{name} -> /api/demo/orgs/by-name/{name}."""
    return await demo_org_by_name(name, db)


# 静态文件服务（Web Demo 页面：notice_detail/version_history/org_profile）
STATIC_DIR = Path("static")
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ==== 基础端点 ====

@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    """健康检查（无需认证）。"""
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def root():
    """根路径重定向到工作台。"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/ui", status_code=307)
