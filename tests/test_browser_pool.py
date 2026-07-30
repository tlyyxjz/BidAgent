from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.browser_pool import BrowserPool, BrowserSlot


def make_started_pool() -> tuple[BrowserPool, BrowserSlot]:
    pool = BrowserPool(size=1, acquire_timeout=0.02)
    pool._queue = asyncio.Queue(maxsize=1)
    pool._semaphore = asyncio.Semaphore(1)
    pool._started = True
    slot = BrowserSlot(0, AsyncMock(), AsyncMock())
    pool._slots = [slot]
    pool._queue.put_nowait(slot)
    return pool, slot


def test_zero_size_rejected():
    """池大小必须为正数。"""
    with pytest.raises(ValueError):
        BrowserPool(size=0)


def test_zero_timeout_rejected():
    """获取超时必须为正数。"""
    with pytest.raises(ValueError):
        BrowserPool(size=1, acquire_timeout=0)


@pytest.mark.asyncio
async def test_acquire_before_start_rejected():
    """未启动的池不能出租浏览器。"""
    with pytest.raises(RuntimeError):
        await BrowserPool(size=1).acquire()


@pytest.mark.asyncio
async def test_acquire_updates_counts():
    """获取后 busy/free 计数应变化。"""
    pool, slot = make_started_pool()
    acquired = await pool.acquire()
    assert acquired is slot
    assert pool.busy_count == 1
    assert pool.free_count == 0


@pytest.mark.asyncio
async def test_release_restores_counts():
    """归还后计数应恢复。"""
    pool, _ = make_started_pool()
    slot = await pool.acquire()
    await pool.release(slot)
    assert pool.busy_count == 0
    assert pool.free_count == 1


@pytest.mark.asyncio
async def test_release_is_idempotent():
    """重复归还不能增加池容量。"""
    pool, _ = make_started_pool()
    slot = await pool.acquire()
    await pool.release(slot)
    await pool.release(slot)
    assert pool.free_count == 1


@pytest.mark.asyncio
async def test_acquire_times_out():
    """池耗尽时应按配置超时。

    Note: Python 3.10 中 asyncio.exceptions.TimeoutError 与内置 TimeoutError
    是不同的类(3.11+ 才统一),必须使用 asyncio.TimeoutError 匹配 wait_for 抛出的异常。
    """
    pool, _ = make_started_pool()
    await pool.acquire()
    with pytest.raises(asyncio.TimeoutError):
        await pool.acquire()


@pytest.mark.asyncio
async def test_context_closes_and_releases():
    """Context 管理器退出时自动关闭并归还。"""
    pool, slot = make_started_pool()
    context = AsyncMock()
    # set_default_timeout / set_default_navigation_timeout 在 Playwright 中是同步方法
    # AsyncMock 会让它们变成 coroutine,导致 "coroutine never awaited" warning
    context.set_default_timeout = MagicMock()
    context.set_default_navigation_timeout = MagicMock()
    slot.browser.new_context.return_value = context

    async with pool.context() as returned:
        assert returned is context
        assert pool.busy_count == 1

    context.close.assert_awaited_once()
    assert pool.free_count == 1


@pytest.mark.asyncio
async def test_context_creation_failure_releases_slot():
    """Context 创建失败也必须归还槽位。"""
    pool, slot = make_started_pool()
    slot.browser.new_context.side_effect = RuntimeError(
        "create failed"
    )

    with pytest.raises(RuntimeError):
        async with pool.context():
            pass

    assert pool.free_count == 1
    assert pool.busy_count == 0


@pytest.mark.asyncio
async def test_close_resets_state():
    """关闭后应清空内部状态。"""
    pool, slot = make_started_pool()
    await pool.close()
    slot.browser.close.assert_awaited_once()
    slot.driver.stop.assert_awaited_once()
    assert not pool.started
    assert pool.free_count == 0

# ========== 部分启动失败清理测试 (#26 修复) ==========


class TestBrowserPoolStartFailure:
    """start() 部分槽位 launch 失败时清理已启动槽位 (#26 修复)。

    现有测试全部用 make_started_pool 绕过 start()，本类直接 mock
    async_playwright 链式调用，覆盖外层 except → self.close() 清理路径。
    """

    @pytest.mark.asyncio
    async def test_start_failure_cleans_launched_slots(self):
        """第 2 个 slot launch 失败时，第 1 个 slot 的 browser.close / driver.stop 被调用。"""
        pool = BrowserPool(size=2, acquire_timeout=0.02)

        driver0 = AsyncMock()
        driver1 = AsyncMock()
        browser0 = AsyncMock()
        driver0.chromium.launch.return_value = browser0
        driver1.chromium.launch.side_effect = RuntimeError("launch failed")

        pw_mock = MagicMock()
        pw_mock.start = AsyncMock(side_effect=[driver0, driver1])

        with patch("app.core.browser_pool.async_playwright", return_value=pw_mock):
            with pytest.raises(RuntimeError, match="launch failed"):
                await pool.start()

        # 第 1 个 slot 的 browser 和 driver 被外层 close() 清理
        browser0.close.assert_awaited_once()
        driver0.stop.assert_awaited_once()
        # 第 2 个 slot 的 driver 被内层 except stop
        driver1.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_failure_raises_exception(self):
        """launch 失败时异常向上抛出。"""
        pool = BrowserPool(size=1, acquire_timeout=0.02)
        driver0 = AsyncMock()
        driver0.chromium.launch.side_effect = RuntimeError("launch failed")

        pw_mock = MagicMock()
        pw_mock.start = AsyncMock(return_value=driver0)

        with patch("app.core.browser_pool.async_playwright", return_value=pw_mock):
            with pytest.raises(RuntimeError, match="launch failed"):
                await pool.start()

    @pytest.mark.asyncio
    async def test_start_failure_pool_state_clean(self):
        """异常后 pool 状态为初始状态，可重试。"""
        pool = BrowserPool(size=2, acquire_timeout=0.02)

        driver0 = AsyncMock()
        driver1 = AsyncMock()
        browser0 = AsyncMock()
        driver0.chromium.launch.return_value = browser0
        driver1.chromium.launch.side_effect = RuntimeError("launch failed")

        pw_mock = MagicMock()
        pw_mock.start = AsyncMock(side_effect=[driver0, driver1])

        with patch("app.core.browser_pool.async_playwright", return_value=pw_mock):
            with pytest.raises(RuntimeError):
                await pool.start()

        # 状态重置为初始
        assert pool.started is False
        assert pool._slots == []
        assert pool._queue is None
        assert pool._semaphore is None
        assert pool.free_count == 0
        assert pool.busy_count == 0

        # 可重试：用成功 mock 再次 start
        driver_ok = AsyncMock()
        browser_ok = AsyncMock()
        driver_ok.chromium.launch.return_value = browser_ok
        pw_ok = MagicMock()
        pw_ok.start = AsyncMock(return_value=driver_ok)
        with patch("app.core.browser_pool.async_playwright", return_value=pw_ok):
            await pool.start()
        assert pool.started is True
        assert pool.free_count == 2
        await pool.close()
