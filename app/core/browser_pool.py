"""有界 Playwright Browser 池。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from playwright.async_api import async_playwright

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger("browser_pool")


@dataclass
class BrowserSlot:
    index: int
    driver: Any
    browser: Any
    in_use: bool = False


class BrowserPool:
    """浏览器有界池。

    Queue 保存实际空闲槽位；Semaphore 明确约束并发租约数。
    不在模块导入阶段创建 Lock/Semaphore/Queue。
    """

    def __init__(
        self,
        size: int | None = None,
        acquire_timeout: float | None = None,
        headless: bool | None = None,
    ) -> None:
        self.size = (
            settings.BROWSER_POOL_SIZE
            if size is None
            else size
        )
        self.acquire_timeout = (
            float(settings.BROWSER_POOL_TIMEOUT)
            if acquire_timeout is None
            else float(acquire_timeout)
        )
        self.headless = (
            settings.ANTI_DETECT_HEADLESS
            if headless is None
            else headless
        )

        if self.size <= 0:
            raise ValueError("size 必须 > 0")
        if self.acquire_timeout <= 0:
            raise ValueError("acquire_timeout 必须 > 0")

        self._slots: list[BrowserSlot] = []
        self._queue: asyncio.Queue[BrowserSlot] | None = None
        self._semaphore: asyncio.Semaphore | None = None
        self._started = False

    async def __aenter__(self) -> "BrowserPool":
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def start(self) -> None:
        if self._started:
            return

        self._queue = asyncio.Queue(maxsize=self.size)
        self._semaphore = asyncio.Semaphore(self.size)

        try:
            for index in range(self.size):
                driver = await async_playwright().start()
                try:
                    args = ["--disable-dev-shm-usage"]
                    if settings.ANTI_DETECT_NO_SANDBOX:
                        args.append("--no-sandbox")

                    browser = await driver.chromium.launch(
                        headless=self.headless,
                        args=args,
                    )
                except Exception:
                    # launch 失败：单独 stop 当前 driver，然后走外层
                    # except BaseException → self.close() 清理已启动的 slot
                    await driver.stop()
                    raise

                slot = BrowserSlot(
                    index=index,
                    driver=driver,
                    browser=browser,
                )
                # M-1 修复：先 append 到 _slots 再 put_nowait 到 queue。
                # 这样即便 put_nowait 因任何原因失败（理论上不会，因为
                # queue maxsize = size 且每次循环只 put 一次），
                # 外层 except 也能通过 self.close() 清理这个 slot。
                self._slots.append(slot)
                self._queue.put_nowait(slot)

        except BaseException:
            # M-1 修复：部分启动失败时，已加入 _slots 的所有 slot
            # （含 driver + browser）都会被 close() 清理，无资源泄漏。
            await self.close()
            raise

        self._started = True
        logger.info("browser pool started size={}", self.size)

    async def acquire(self) -> BrowserSlot:
        if (
            not self._started
            or self._queue is None
            or self._semaphore is None
        ):
            raise RuntimeError("浏览器池未启动")

        # M-3 修复：semaphore.acquire 成功后立即标记，后续任何异常路径
        # （包括 wait_for 超时但 acquire 恰好成功、queue.get 失败等）
        # 都在 finally 中归还 permit，避免信号量永久少一个。
        acquired_semaphore = False
        slot: BrowserSlot | None = None
        try:
            await asyncio.wait_for(
                self._semaphore.acquire(),
                timeout=self.acquire_timeout,
            )
            acquired_semaphore = True

            slot = await asyncio.wait_for(
                self._queue.get(),
                timeout=self.acquire_timeout,
            )
            slot.in_use = True
            return slot
        except BaseException:
            # queue.get 失败时归还信号量；如果 slot 已经拿到但发生其他异常，
            # 也要把 slot 放回队列
            if slot is not None:
                slot.in_use = False
                if self._queue is not None:
                    try:
                        self._queue.put_nowait(slot)
                    except asyncio.QueueFull:
                        logger.warning(
                            "queue full when returning slot {}",
                            slot.index,
                        )
            if acquired_semaphore and self._semaphore is not None:
                self._semaphore.release()
            raise

    async def release(self, slot: BrowserSlot) -> None:
        """归还槽位；重复归还无副作用。"""
        if not slot.in_use:
            return

        slot.in_use = False
        if self._queue is not None:
            self._queue.put_nowait(slot)
        if self._semaphore is not None:
            self._semaphore.release()

    @asynccontextmanager
    async def context(
        self,
        **context_options: Any,
    ) -> AsyncIterator[Any]:
        slot = await self.acquire()
        context = None

        try:
            context_options.setdefault("locale", "zh-CN")
            context_options.setdefault(
                "viewport",
                {"width": 1366, "height": 768},
            )

            context = await slot.browser.new_context(
                **context_options
            )
            timeout_ms = (
                settings.PLAYWRIGHT_TIMEOUT_SECONDS * 1000
            )
            context.set_default_timeout(timeout_ms)
            context.set_default_navigation_timeout(timeout_ms)
            yield context
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    logger.warning(
                        "context close failed slot={}",
                        slot.index,
                    )
            await self.release(slot)

    async def close(self) -> None:
        for slot in reversed(self._slots):
            try:
                await slot.browser.close()
            except Exception:
                logger.warning(
                    "browser close failed slot={}",
                    slot.index,
                )
            try:
                await slot.driver.stop()
            except Exception:
                logger.warning(
                    "driver stop failed slot={}",
                    slot.index,
                )

        self._slots.clear()
        self._queue = None
        self._semaphore = None
        self._started = False

    @property
    def started(self) -> bool:
        return self._started

    @property
    def free_count(self) -> int:
        return self._queue.qsize() if self._queue else 0

    @property
    def busy_count(self) -> int:
        return sum(
            1 for slot in self._slots if slot.in_use
        )
