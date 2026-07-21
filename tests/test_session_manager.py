from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.core.session_manager import SessionManager


def write_state(path: Path, cookies=None, origins=None) -> None:
    path.write_text(
        json.dumps({
            "cookies": cookies or [],
            "origins": origins or [],
        }),
        encoding="utf-8",
    )


def test_rejects_unsafe_platform_name():
    """平台名不能造成目录穿越。"""
    with pytest.raises(ValueError):
        SessionManager("../outside")


@pytest.mark.asyncio
async def test_missing_session_returns_none(tmp_path: Path):
    """文件不存在时安全返回 None。"""
    manager = SessionManager(
        "qianlima",
        tmp_path / "missing.json",
    )
    assert await manager.load_state() is None


@pytest.mark.asyncio
async def test_corrupted_json_returns_none(tmp_path: Path):
    """损坏 JSON 不应导致应用崩溃。"""
    path = tmp_path / "bad.json"
    path.write_text("{bad", encoding="utf-8")
    manager = SessionManager("qianlima", path)
    assert await manager.load_state() is None


@pytest.mark.asyncio
async def test_non_object_json_returns_none(tmp_path: Path):
    """顶层非对象 JSON 无效。"""
    path = tmp_path / "bad.json"
    path.write_text("[]", encoding="utf-8")
    manager = SessionManager("qianlima", path)
    assert await manager.load_state() is None


@pytest.mark.asyncio
async def test_load_valid_state(tmp_path: Path):
    """有效 storage_state 能完整读取。"""
    path = tmp_path / "state.json"
    write_state(path, [{"name": "sid", "value": "x"}])
    state = await SessionManager(
        "qianlima",
        path,
    ).load_state()
    assert state is not None
    assert state["cookies"][0]["name"] == "sid"


@pytest.mark.asyncio
async def test_save_and_reload(tmp_path: Path):
    """保存后应能重新加载。"""
    path = tmp_path / "state.json"
    context = AsyncMock()
    context.storage_state.return_value = {
        "cookies": [],
        "origins": [],
    }
    manager = SessionManager("qianlima", path)
    assert await manager.save(context) == path
    assert await manager.load_state() == {
        "cookies": [],
        "origins": [],
    }


@pytest.mark.asyncio
async def test_atomic_save_leaves_no_temp_file(tmp_path: Path):
    """原子保存后不应遗留临时文件。"""
    path = tmp_path / "state.json"
    context = AsyncMock()
    context.storage_state.return_value = {
        "cookies": [],
        "origins": [],
    }
    await SessionManager("qianlima", path).save(context)
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.asyncio
async def test_unexpired_cookie_is_valid(tmp_path: Path):
    """未过期且域名匹配的 Cookie 有效。"""
    path = tmp_path / "state.json"
    write_state(path, [{
        "name": "sid",
        "value": "x",
        "domain": ".qianlima.com",
        "expires": time.time() + 100,
    }])
    assert await SessionManager(
        "qianlima",
        path,
    ).is_valid(domain_suffix="qianlima.com")


@pytest.mark.asyncio
async def test_expired_cookie_is_invalid(tmp_path: Path):
    """已过期 Cookie 无效。"""
    path = tmp_path / "state.json"
    write_state(path, [{
        "name": "sid",
        "value": "x",
        "domain": ".qianlima.com",
        "expires": time.time() - 1,
    }])
    assert not await SessionManager(
        "qianlima",
        path,
    ).is_valid(domain_suffix="qianlima.com")


@pytest.mark.asyncio
async def test_domain_boundary_is_enforced(tmp_path: Path):
    """evilqianlima.com 不能匹配 qianlima.com。"""
    path = tmp_path / "state.json"
    write_state(path, [{
        "name": "sid",
        "value": "x",
        "domain": ".evilqianlima.com",
        "expires": -1,
    }])
    assert not await SessionManager(
        "qianlima",
        path,
    ).is_valid(domain_suffix="qianlima.com")


@pytest.mark.asyncio
async def test_required_cookie_must_exist(tmp_path: Path):
    """指定必需 Cookie 时必须全部存在。"""
    path = tmp_path / "state.json"
    write_state(path, [{
        "name": "sid",
        "value": "x",
        "domain": ".qianlima.com",
        "expires": -1,
    }])
    assert not await SessionManager(
        "qianlima",
        path,
    ).is_valid(
        {"sid", "token"},
        "qianlima.com",
    )


@pytest.mark.asyncio
async def test_create_context_injects_state(tmp_path: Path):
    """创建 Context 时注入 storage_state。"""
    path = tmp_path / "state.json"
    write_state(path)
    browser = AsyncMock()
    await SessionManager(
        "qianlima",
        path,
    ).create_context(browser, locale="zh-CN")
    browser.new_context.assert_awaited_once_with(
        locale="zh-CN",
        storage_state={
            "cookies": [],
            "origins": [],
        },
    )


@pytest.mark.asyncio
async def test_cookie_summary_does_not_expose_value(
    tmp_path: Path,
):
    """Cookie 摘要不得泄漏敏感 value。"""
    path = tmp_path / "state.json"
    write_state(path, [{
        "name": "sid",
        "value": "top-secret",
        "domain": ".qianlima.com",
    }])
    summary = await SessionManager(
        "qianlima",
        path,
    ).cookie_summary()
    assert summary[0]["name"] == "sid"
    assert "value" not in summary[0]
