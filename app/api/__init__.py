"""标小智 API 路由."""

from app.api.admin import admin_router  # noqa: F401
from app.api.agents import router as agents_router  # noqa: F401
from app.api.scrape import router as scrape_router  # noqa: F401
from app.api.subscribe import router as subscribe_router  # noqa: F401
from app.api.tender import router as tender_router  # noqa: F401
from app.api.ui import router as ui_router  # noqa: F401
from app.api.evidence_demo import router as evidence_demo_router  # noqa: F401
from app.api.real_demo import router as real_demo_router  # noqa: F401
from app.api.demo_api import router as demo_router  # noqa: F401
from app.api.v41_api import router as v41_router  # noqa: F401
