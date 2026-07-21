from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.templates.qianlima_login as module


@pytest.mark.asyncio
async def test_empty_username_rejected():
    """空用户名在启动浏览器前被拒绝。"""
    result = await module.login_and_save_cookies(
        "",
        "password",
    )
    assert not result["success"]
    assert "用户名" in result["error"]


@pytest.mark.asyncio
async def test_empty_password_rejected():
    """空密码在启动浏览器前被拒绝。"""
    result = await module.login_and_save_cookies(
        "user",
        "",
    )
    assert not result["success"]
    assert "密码" in result["error"]


@pytest.mark.asyncio
async def test_invalid_timeout_rejected():
    """非正等待时间应被拒绝。"""
    result = await module.login_and_save_cookies(
        "user",
        "password",
        wait_timeout_seconds=0,
    )
    assert not result["success"]
    assert "必须 > 0" in result["error"]


def test_dom_config_missing_returns_empty(tmp_path: Path):
    """DOM 配置不存在时使用回退候选。"""
    assert module._load_dom_config(
        tmp_path / "missing.json"
    ) == {}


def test_dom_config_can_be_loaded(tmp_path: Path):
    """有效 DOM JSON 可以读取。"""
    path = tmp_path / "dom.json"
    path.write_text(
        json.dumps({"verified": True}),
        encoding="utf-8",
    )
    assert module._load_dom_config(path)["verified"]


@pytest.mark.asyncio
async def test_first_visible_returns_matching_locator():
    """应返回第一个可见选择器。"""
    # page.locator() 是同步方法，所以用 MagicMock 而非 AsyncMock
    page = MagicMock()
    hidden = MagicMock()
    hidden.is_visible = AsyncMock(return_value=False)
    visible = MagicMock()
    visible.is_visible = AsyncMock(return_value=True)

    page.locator.side_effect = [
        type("Result", (), {"first": hidden})(),
        type("Result", (), {"first": visible})(),
    ]

    result = await module._first_visible(
        page,
        ["#hidden", "#visible"],
    )
    assert result is visible


@pytest.mark.asyncio
async def test_url_transition_counts_as_success():
    """离开 login URL 可作为成功信号。"""
    page = AsyncMock()
    page.url = "https://www.qianlima.com/user"
    assert await module._login_succeeded(
        page,
        "https://www.qianlima.com/login",
        [],
    )


@pytest.mark.asyncio
async def test_same_login_url_without_element_is_failure(
    monkeypatch,
):
    """仍在登录页且没有成功元素时不能误判成功。"""
    page = AsyncMock()
    page.url = "https://www.qianlima.com/login"
    monkeypatch.setattr(
        module,
        "_first_visible",
        AsyncMock(return_value=None),
    )
    assert not await module._login_succeeded(
        page,
        page.url,
        [".user"],
    )
