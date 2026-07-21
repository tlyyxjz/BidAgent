from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

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
    """池耗尽时应按配置超时。"""
    pool, _ = make_started_pool()
    await pool.acquire()
    with pytest.raises(TimeoutError):
        await pool.acquire()


@pytest.mark.asyncio
async def test_context_closes_and_releases():
    """Context 管理器退出时自动关闭并归还。"""
    pool, slot = make_started_pool()
    context = AsyncMock()
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
