"""Playwright storage_state 登录态持久化。"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger("session_manager")

_PLATFORM_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class SessionManager:
    """管理单个平台的 Playwright storage_state。"""

    def __init__(
        self,
        platform: str,
        session_path: Path | None = None,
    ) -> None:
        if not _PLATFORM_RE.fullmatch(platform):
            raise ValueError(
                "platform 只能包含字母、数字、下划线和连字符"
            )

        self.platform = platform
        self.session_path = (
            Path(session_path)
            if session_path is not None
            else Path(settings.ANTI_DETECT_SESSION_DIR)
            / f"{platform}_session.json"
        )

    def has_session(self) -> bool:
        return self.session_path.is_file()

    async def load_state(self) -> dict[str, Any] | None:
        if not self.has_session():
            return None

        try:
            state = await asyncio.to_thread(self._read_sync)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning(
                "session load failed platform={} type={}",
                self.platform,
                type(exc).__name__,
            )
            return None

        if not isinstance(state.get("cookies", []), list):
            return None
        if not isinstance(state.get("origins", []), list):
            return None
        return state

    def _read_sync(self) -> dict[str, Any]:
        state = json.loads(
            self.session_path.read_text(encoding="utf-8")
        )
        if not isinstance(state, dict):
            raise ValueError("storage_state 顶层必须是对象")
        return state

    async def save(self, context: Any) -> Path:
        state = await context.storage_state()

        if not isinstance(state, dict):
            raise ValueError("storage_state 必须是对象")
        if not isinstance(state.get("cookies", []), list):
            raise ValueError("storage_state.cookies 必须是列表")
        if not isinstance(state.get("origins", []), list):
            raise ValueError("storage_state.origins 必须是列表")

        await asyncio.to_thread(
            self._write_atomic_sync,
            state,
        )
        logger.info(
            "session saved platform={} path={} cookies={}",
            self.platform,
            self.session_path,
            len(state.get("cookies", [])),
        )
        return self.session_path

    def _write_atomic_sync(
        self,
        state: dict[str, Any],
    ) -> None:
        self.session_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.session_path.parent,
                prefix=f".{self.session_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                json.dump(
                    state,
                    temporary,
                    ensure_ascii=False,
                    indent=2,
                )
                temporary.flush()
                os.fsync(temporary.fileno())

            os.replace(temporary_name, self.session_path)
        finally:
            if temporary_name:
                temporary_path = Path(temporary_name)
                if temporary_path.exists():
                    temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _cookie_expired(
        cookie: dict[str, Any],
        now: float,
    ) -> bool:
        """兼容 expires、maxAge 和 max-age。"""
        max_age = cookie.get(
            "maxAge",
            cookie.get("max-age"),
        )
        if max_age is not None:
            try:
                if float(max_age) <= 0:
                    return True
            except (TypeError, ValueError):
                return True

        expires = cookie.get("expires", -1)
        try:
            expiry = float(expires)
        except (TypeError, ValueError):
            return True

        # -1/0 代表会话 Cookie，不在本地判过期。
        return expiry > 0 and expiry <= now

    @staticmethod
    def _domain_matches(
        cookie_domain: str,
        domain_suffix: str,
    ) -> bool:
        domain = cookie_domain.lower().lstrip(".")
        suffix = domain_suffix.lower().lstrip(".")
        return domain == suffix or domain.endswith("." + suffix)

    async def is_valid(
        self,
        required_cookie_names: set[str] | None = None,
        domain_suffix: str | None = None,
    ) -> bool:
        state = await self.load_state()
        if state is None:
            return False

        now = time.time()
        usable_names: set[str] = set()

        for cookie in state.get("cookies", []):
            if not isinstance(cookie, dict):
                continue

            name = str(cookie.get("name") or "")
            domain = str(cookie.get("domain") or "")
            if not name:
                continue

            if (
                domain_suffix
                and not self._domain_matches(
                    domain,
                    domain_suffix,
                )
            ):
                continue

            if self._cookie_expired(cookie, now):
                continue

            usable_names.add(name)

        if required_cookie_names:
            return required_cookie_names.issubset(
                usable_names
            )
        return bool(usable_names)

    async def create_context(
        self,
        browser: Any,
        **context_options: Any,
    ) -> Any:
        state = await self.load_state()
        if state is not None:
            context_options["storage_state"] = state

        return await browser.new_context(
            **context_options
        )

    async def cookie_summary(
        self,
    ) -> list[dict[str, Any]]:
        """返回脱敏摘要，绝不包含 value。"""
        state = await self.load_state()
        if state is None:
            return []

        result: list[dict[str, Any]] = []
        for cookie in state.get("cookies", []):
            if not isinstance(cookie, dict):
                continue
            result.append({
                "name": cookie.get("name"),
                "domain": cookie.get("domain"),
                "path": cookie.get("path"),
                "expires": cookie.get("expires"),
                "httpOnly": cookie.get("httpOnly"),
                "secure": cookie.get("secure"),
                "sameSite": cookie.get("sameSite"),
            })
        return result

    async def delete(self) -> bool:
        if not self.session_path.exists():
            return False
        await asyncio.to_thread(
            self.session_path.unlink
        )
        return True
