"""最简浏览器启动测试 - 排查 Chromium 是否能正常弹出。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def main() -> int:
    print("[1/5] 导入 playwright...", flush=True)
    from playwright.async_api import async_playwright

    print("[2/5] 启动 playwright driver...", flush=True)
    driver = await async_playwright().start()

    print("[3/5] 启动 Chromium (headless=False)...", flush=True)
    browser = await driver.chromium.launch(headless=False)

    print("[4/5] 创建 context + page...", flush=True)
    context = await browser.new_context()
    page = await context.new_page()

    print("[5/5] 打开 example.com...", flush=True)
    await page.goto("https://example.com", wait_until="domcontentloaded", timeout=15000)
    title = await page.title()
    print(f"页面标题: {title}", flush=True)

    print("浏览器应该已弹出！5 秒后自动关闭...", flush=True)
    await asyncio.sleep(5)

    await context.close()
    await browser.close()
    await driver.stop()
    print("OK", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
