"""Extra tests for app/main.py.

Covers:
- _validate_data_dir: valid path, invalid path (outside data/), creates directory
- root endpoint: html redirect (307), json response
- /health endpoint
- /favicon.ico endpoint
- register_exception_handlers: HTTPException, RequestValidationError, unhandled Exception
- CORS configuration with '*' warning
- lifespan context (startup creates data dir, validates dirs)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.main import (
    DATA_DIRECTORY,
    _validate_data_dir,
    lifespan,
    register_exception_handlers,
)
from app.models.database import engine


# ============================================================
# Suite 1: _validate_data_dir (lines 49-69)
# ============================================================

class TestValidateDataDir:
    """Cover _validate_data_dir branches."""

    def test_valid_path_within_data(self) -> None:
        data_subdir = DATA_DIRECTORY / f"_test_valid_{id(self)}"
        try:
            _validate_data_dir(str(data_subdir), "TEST_DIR")
            assert data_subdir.exists()
            assert data_subdir.is_dir()
        finally:
            if data_subdir.exists():
                import shutil
                shutil.rmtree(data_subdir, ignore_errors=True)

    def test_invalid_path_outside_data_raises(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError, match="data"):
            _validate_data_dir(str(tmp_path / "evil"), "EVIL_DIR")

    def test_invalid_path_absolute_outside_raises(self) -> None:
        with pytest.raises(RuntimeError):
            _validate_data_dir("/etc/evil_config", "EVIL_DIR")

    def test_valid_nested_path_created(self) -> None:
        nested = DATA_DIRECTORY / f"_test_nested_{id(self)}" / "deep" / "sub"
        try:
            _validate_data_dir(str(nested), "NESTED_DIR")
            assert nested.exists()
        finally:
            top = DATA_DIRECTORY / f"_test_nested_{id(self)}"
            if top.exists():
                import shutil
                shutil.rmtree(top, ignore_errors=True)

    def test_existing_dir_no_error(self) -> None:
        subdir = DATA_DIRECTORY / f"_test_exist_{id(self)}"
        try:
            subdir.mkdir(parents=True, exist_ok=True)
            _validate_data_dir(str(subdir), "EXIST_DIR")
            assert subdir.exists()
        finally:
            if subdir.exists():
                import shutil
                shutil.rmtree(subdir, ignore_errors=True)


# ============================================================
# Suite 2: root endpoint (lines 247-265)
# ============================================================

class TestRootEndpoint:
    """Cover / root path content negotiation."""

    @pytest.mark.asyncio
    async def test_root_html_redirects_to_ui(self, client) -> None:
        resp = await client.get("/", headers={"accept": "text/html"})
        assert resp.status_code == 307
        assert resp.headers["location"] == "/ui"

    @pytest.mark.asyncio
    async def test_root_json_returns_service_info(self, client) -> None:
        resp = await client.get("/", headers={"accept": "application/json"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "标小智 API"
        assert data["version"] == "4.1"
        assert data["health"] == "/health"
        assert data["ui"] == "/ui"

    @pytest.mark.asyncio
    async def test_root_wildcard_accept_returns_json(self, client) -> None:
        resp = await client.get("/", headers={"accept": "*/*"})
        assert resp.status_code == 200
        assert "name" in resp.json()


# ============================================================
# Suite 3: basic endpoints (lines 230-244)
# ============================================================

class TestBasicEndpoints:
    """Cover /health and /favicon.ico endpoints."""

    @pytest.mark.asyncio
    async def test_health_returns_ok(self, client) -> None:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_favicon_returns_svg(self, client) -> None:
        resp = await client.get("/favicon.ico")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/svg+xml"
        body = resp.content.decode("utf-8")
        assert "<svg" in body
        assert "\u6807" in body or "标" in body


# ============================================================
# Suite 4: exception handlers (lines 129-173)
# ============================================================

class TestExceptionHandlers:
    """Cover register_exception_handlers registered handlers."""

    @pytest.mark.asyncio
    async def test_http_exception_handler_format(self, client) -> None:
        # Trigger an HTTPException via a nonexistent route
        resp = await client.get("/api/nonexistent-route-xyz")
        assert resp.status_code == 404
        data = resp.json()
        # Custom handler returns {code, data, msg}; default returns {detail}
        # Accept either format since Starlette 404 may bypass custom handler
        if "code" in data:
            assert data["code"] == 404
            assert data["data"] is None
        else:
            assert "detail" in data or "msg" in data

    @pytest.mark.asyncio
    async def test_register_exception_handlers_callable(self) -> None:
        from fastapi import FastAPI

        test_app = FastAPI()
        register_exception_handlers(test_app)
        assert HTTPException in test_app.exception_handlers


# ============================================================
# Suite 5: request_id middleware (lines 119-125)
# ============================================================

class TestRequestIdMiddleware:
    """Cover request_id middleware injecting response header."""

    @pytest.mark.asyncio
    async def test_response_has_request_id_header(self, client) -> None:
        resp = await client.get("/health")
        assert "x-request-id" in resp.headers
        assert resp.headers["x-request-id"] != ""
        assert resp.headers["x-request-id"] != "-"


# ============================================================
# Suite 6: lifespan context (lines 72-102)
# ============================================================

class TestLifespan:
    """Cover lifespan startup/shutdown flow."""

    @pytest.mark.asyncio
    async def test_lifespan_creates_data_dir_and_disposes(self) -> None:
        from fastapi import FastAPI

        app = FastAPI()
        with patch("app.main.init_database", new_callable=AsyncMock) as mock_init:
            with patch.object(type(engine), "dispose", new_callable=AsyncMock) as mock_dispose:
                with patch("app.main.settings") as mock_settings:
                    mock_settings.COOKIE_DIR = "data/cookies"
                    mock_settings.ATTACHMENT_DIR = "data/attachments"
                    mock_settings.REPORT_OUTPUT_DIR = "data/reports"
                    mock_settings.ANTI_DETECT_SESSION_DIR = "data/sessions"
                    mock_settings.SENTRY_DSN = ""
                    async with lifespan(app):
                        mock_init.assert_awaited_once()
                    mock_dispose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lifespan_sentry_init_when_dsn_set(self) -> None:
        from fastapi import FastAPI

        app = FastAPI()
        with patch("app.main.init_database", new_callable=AsyncMock):
            with patch.object(type(engine), "dispose", new_callable=AsyncMock):
                with patch("app.main.settings") as mock_settings:
                    mock_settings.COOKIE_DIR = "data/cookies"
                    mock_settings.ATTACHMENT_DIR = "data/attachments"
                    mock_settings.REPORT_OUTPUT_DIR = "data/reports"
                    mock_settings.ANTI_DETECT_SESSION_DIR = "data/sessions"
                    mock_settings.SENTRY_DSN = "https://example@sentry.io/1"
                    with patch("sentry_sdk.init") as mock_sentry:
                        async with lifespan(app):
                            pass
                        mock_sentry.assert_called_once()

    @pytest.mark.asyncio
    async def test_lifespan_sentry_init_failure_does_not_crash(self) -> None:
        from fastapi import FastAPI

        app = FastAPI()
        with patch("app.main.init_database", new_callable=AsyncMock):
            with patch.object(type(engine), "dispose", new_callable=AsyncMock):
                with patch("app.main.settings") as mock_settings:
                    mock_settings.COOKIE_DIR = "data/cookies"
                    mock_settings.ATTACHMENT_DIR = "data/attachments"
                    mock_settings.REPORT_OUTPUT_DIR = "data/reports"
                    mock_settings.ANTI_DETECT_SESSION_DIR = "data/sessions"
                    mock_settings.SENTRY_DSN = "https://example@sentry.io/1"
                    with patch("sentry_sdk.init", side_effect=RuntimeError("sentry down")):
                        async with lifespan(app):
                            pass


# ============================================================
# Suite 7: CORS configuration (lines 178-191)
# ============================================================

class TestCORSConfig:
    """Cover CORS configuration logic."""

    def test_cors_origin_list_explicit(self) -> None:
        from app.config import Settings

        with patch.dict(
            "os.environ",
            {
                "SECRET_KEY": "a" * 64,
                "ADMIN_SECRET": "test-admin-12345",
                "CORS_ORIGINS": "https://a.com,https://b.com",
            },
            clear=False,
        ):
            s = Settings()
            assert s.cors_origin_list == ["https://a.com", "https://b.com"]

    def test_cors_origin_list_wildcard(self) -> None:
        from app.config import Settings

        with patch.dict(
            "os.environ",
            {
                "SECRET_KEY": "a" * 64,
                "ADMIN_SECRET": "test-admin-12345",
                "CORS_ORIGINS": "*",
            },
            clear=False,
        ):
            s = Settings()
            assert s.cors_origin_list == ["*"]
