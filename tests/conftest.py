"""Pytest 共享 fixtures。

工程规范：
- 测试前创建 data 目录，避免 SQLite 数据库文件错误。
- 在导入 app 模块之前设置环境变量。
- 每个 test class 重置数据库。
- 重置内存速率限制计数器。
- 重置域名级采集频率限制器状态（避免测试间相互阻塞）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ==== 在导入 app 之前设置环境变量 ====
os.environ.setdefault("SECRET_KEY", "a" * 64)  # 64 字符 hex（test 用全 a）
os.environ.setdefault("ADMIN_SECRET", "test-admin-secret-12345")
os.environ.setdefault(
    "DATABASE_URL", "sqlite+aiosqlite:///./data/test_scrapeflow.db"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("FREE_TIER_DAILY_LIMIT", "3")
os.environ.setdefault("PLAYWRIGHT_HEADLESS", "true")
os.environ.setdefault("PROXY_LIST", "")  # 测试不用代理

# 将项目根目录加入 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 确保 data 目录存在（避免 SQLite 文件错误）
Path(_PROJECT_ROOT / "data").mkdir(parents=True, exist_ok=True)
# 切换到项目根目录，让相对路径 ./data 生效
os.chdir(_PROJECT_ROOT)

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import generate_api_key, hash_api_key
from app.core.rate_limit import reset_memory_counter
from app.core.rate_limiter import domain_rate_limiter
from app.core.robots_checker import robots_checker
from app.main import app
from app.models.database import AsyncSessionLocal, Base, engine
from app.models.user import (
    ApiKey,
    PLAN_FREE,
    PLAN_PRO,
    User,
)


@pytest.fixture(autouse=True)
async def _reset_db_and_rate_limit(monkeypatch):
    """每个测试前：重置数据库 + 内存速率限制计数器 + 域名级频率限制状态。

    M-2 修复（第四轮）：mock 掉 is_safe_url / is_safe_url_async 避免测试做真实
    DNS 解析（避免 reddit.com 解析到保留 IPv6 被拦截导致测试不稳定）。

    W2 修复（测试环境稳定性）：teardown 阶段用 try/except 包裹 drop_all，
    避免因 basetemp 残留文件清理失败（PermissionError）连锁中断后续测试的
    fixture setup，导致 120 errors。setup 阶段 drop_all 同样容错，防止
    "no such table" / "table already exists" 异常。

    v4.1 §13：重置 domain_rate_limiter 状态，避免前一个测试对某域名的
    _last_request 记录导致后续测试同域名请求被 sleep 8 秒拖慢。
    """
    Path("data").mkdir(parents=True, exist_ok=True)
    # setup：先 drop（容错：表可能不存在），再 create
    async with engine.begin() as conn:
        try:
            await conn.run_sync(Base.metadata.drop_all)
        except Exception as e:
            # 表可能不存在（首个测试或前次 teardown 已清理），忽略
            print(f"[conftest setup] drop_all warning: {e}")
        try:
            await conn.run_sync(Base.metadata.create_all)
        except Exception as e:
            # 表可能已存在（前次 setup 中断），忽略
            print(f"[conftest setup] create_all warning: {e}")
    reset_memory_counter()
    # 重置域名级频率限制器：清空 _last_request 和 _domain_intervals
    domain_rate_limiter.reset()
    # 重置 robots.txt 检查器缓存：避免测试间缓存干扰
    robots_checker.reset()

    # mock SSRF 校验：测试环境跳过真实 DNS 解析
    async def _mock_safe_async(url: str) -> tuple[bool, str]:
        return True, ""

    def _mock_safe(url: str) -> tuple[bool, str]:
        return True, ""

    # patch 模块级函数（attachment_downloader 等用此路径）
    monkeypatch.setattr("app.utils.url_safety.is_safe_url", _mock_safe)
    monkeypatch.setattr("app.utils.url_safety.is_safe_url_async", _mock_safe_async)
    # patch scraper 模块里已 import 的引用（from ... import 形式不会跟随模块属性）
    monkeypatch.setattr("app.core.scraper.is_safe_url", _mock_safe)

    # v4.1 §5.2：mock robots_checker.is_allowed，避免测试发起真实 HTTP 请求
    # 测试 robots_checker 本身时用独立实例（不受此 mock 影响）
    async def _mock_robots_allowed(url: str, user_agent: str = "*") -> bool:
        return True

    monkeypatch.setattr(
        "app.core.scraper.robots_checker.is_allowed", _mock_robots_allowed
    )

    yield
    # teardown：drop_all 容错，避免 PermissionError 中断后续测试
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    except Exception as e:
        # teardown 失败不应影响后续测试，下个 setup 会重新 drop+create
        print(f"[conftest teardown] drop_all warning: {e}")


@pytest.fixture
async def client():
    """ASGI 测试客户端。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def admin_headers() -> dict[str, str]:
    """管理员认证头。"""
    return {"X-Admin-Secret": "test-admin-secret-12345"}


async def _create_user_and_key(
    email: str, plan: str, key_name: str = "test"
) -> tuple[int, str]:
    """创建用户 + API key，返回 (user_id, raw_api_key)。"""
    async with AsyncSessionLocal() as session:
        user = User(email=email, plan=plan)
        session.add(user)
        await session.commit()
        await session.refresh(user)

        raw_key = generate_api_key()
        api_key = ApiKey(
            user_id=user.id,
            key_hash=hash_api_key(raw_key),
            name=key_name,
        )
        session.add(api_key)
        await session.commit()
        return user.id, raw_key


@pytest.fixture
async def free_user_and_key() -> tuple[int, str]:
    """创建 free 套餐用户 + API key。"""
    return await _create_user_and_key("free@test.com", PLAN_FREE, "free-key")


@pytest.fixture
async def pro_user_and_key() -> tuple[int, str]:
    """创建 pro 套餐用户 + API key。"""
    return await _create_user_and_key("pro@test.com", PLAN_PRO, "pro-key")


def auth_headers(raw_api_key: str) -> dict[str, str]:
    """构造 Bearer 认证头。"""
    return {"Authorization": f"Bearer {raw_api_key}"}
