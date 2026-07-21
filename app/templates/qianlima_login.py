"""千里马登录态建立工具。

验证码由用户本人在打开的浏览器中完成。程序不自动破解验证码，
只负责填写授权账号、等待登录成功并保存 storage_state。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

from app.config import settings
from app.core.session_manager import SessionManager
from app.utils.logger import get_logger

logger = get_logger("qianlima_login")

DEFAULT_LOGIN_URL = "https://vip.qianlima.com/login.html"
DEFAULT_DOM_CONFIG = Path("qianlima-dom.json")

FALLBACK_USERNAME_SELECTORS = [
    "input[name='username']",
    "input[name='account']",
    "input[name='mobile']",
    "input[type='text']",
]
FALLBACK_PASSWORD_SELECTORS = [
    "input[name='password']",
    "input[type='password']",
]
FALLBACK_SUCCESS_SELECTORS = [
    "a:has-text('退出')",
    "a:has-text('个人中心')",
    ".user-center",
    ".user-info",
    ".logout",
]


def _load_dom_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _as_selector_list(
    value: Any,
    fallback: list[str],
) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, list):
        result = [
            str(item).strip()
            for item in value
            if str(item).strip()
        ]
        if result:
            return result
    return list(fallback)


async def _first_visible(
    page: Any,
    selectors: list[str],
) -> Any | None:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if await locator.is_visible(timeout=1000):
                return locator
        except Exception:
            continue
    return None


async def _login_succeeded(
    page: Any,
    login_url: str,
    success_selectors: list[str],
) -> bool:
    current = str(page.url or "").lower()
    expected = login_url.lower().rstrip("/")

    if (
        current
        and current.rstrip("/") != expected
        and "login" not in current
    ):
        return True

    return (
        await _first_visible(page, success_selectors)
        is not None
    )


async def login_and_save_cookies(
    username: str,
    password: str,
    login_url: str = DEFAULT_LOGIN_URL,
    cookie_file: Path | None = None,
    wait_timeout_seconds: int = 300,
    dom_config_path: Path = DEFAULT_DOM_CONFIG,
) -> dict[str, Any]:
    """打开登录页并保存用户完成验证码后的登录态。"""
    if not username.strip():
        return {
            "success": False,
            "session_path": None,
            "error": "用户名不能为空",
        }
    if not password:
        return {
            "success": False,
            "session_path": None,
            "error": "密码不能为空",
        }
    if wait_timeout_seconds <= 0:
        return {
            "success": False,
            "session_path": None,
            "error": "wait_timeout_seconds 必须 > 0",
        }

    dom = _load_dom_config(dom_config_path)
    login = dom.get("login", {}) if isinstance(
        dom.get("login", {}), dict
    ) else {}

    actual_login_url = str(
        login.get("url") or login_url
    )
    username_selectors = _as_selector_list(
        login.get("username_selector"),
        FALLBACK_USERNAME_SELECTORS,
    )
    password_selectors = _as_selector_list(
        login.get("password_selector"),
        FALLBACK_PASSWORD_SELECTORS,
    )
    success_selectors = _as_selector_list(
        login.get("success_selector"),
        FALLBACK_SUCCESS_SELECTORS,
    )

    manager = SessionManager(
        "qianlima",
        session_path=Path(cookie_file)
        if cookie_file is not None
        else None,
    )

    driver = None
    browser = None
    context = None

    try:
        print("[step 1/6] 启动 playwright driver", flush=True)
        driver = await async_playwright().start()
        print("[step 2/6] 启动 Chromium (headless=False)", flush=True)
        browser = await driver.chromium.launch(
            headless=False
        )
        print("[step 3/6] 创建 context (locale=zh-CN)", flush=True)
        context = await browser.new_context(
            locale="zh-CN",
            viewport={"width": 1366, "height": 850},
        )
        context.set_default_timeout(
            settings.PLAYWRIGHT_TIMEOUT_SECONDS * 1000
        )

        page = await context.new_page()
        print(f"[step 4/6] 打开登录页: {actual_login_url}", flush=True)
        await page.goto(
            actual_login_url,
            wait_until="domcontentloaded",
            timeout=20000,
        )
        print(
            f"[step 4/6] 页面加载完成 final_url={page.url} title={await page.title()}",
            flush=True,
        )

        print("[step 5/6] 查找用户名输入框", flush=True)
        username_input = await _first_visible(
            page,
            username_selectors,
        )
        print("[step 5/6] 查找密码输入框", flush=True)
        password_input = await _first_visible(
            page,
            password_selectors,
        )

        if username_input is None:
            return {
                "success": False,
                "session_path": None,
                "error": "未找到用户名输入框，请先运行 DOM 探测",
            }
        if password_input is None:
            return {
                "success": False,
                "session_path": None,
                "error": "未找到密码输入框，请先运行 DOM 探测",
            }

        await username_input.fill(username)
        await password_input.fill(password)

        logger.info(
            "登录页已打开，请在浏览器中完成验证码并提交登录"
        )

        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_timeout_seconds

        while loop.time() < deadline:
            if await _login_succeeded(
                page,
                actual_login_url,
                success_selectors,
            ):
                saved_path = await manager.save(context)
                return {
                    "success": True,
                    "session_path": str(saved_path),
                    "error": None,
                }

            await asyncio.sleep(1)

        return {
            "success": False,
            "session_path": None,
            "error": (
                f"等待登录超时: {wait_timeout_seconds} 秒"
            ),
        }

    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "qianlima login failed type={}",
            type(exc).__name__,
        )
        return {
            "success": False,
            "session_path": None,
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        if driver is not None:
            try:
                await driver.stop()
            except Exception:
                pass
