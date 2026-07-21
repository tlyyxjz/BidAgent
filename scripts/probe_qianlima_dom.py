# =====================================================================

"""在本机打开千里马页面并生成 DOM 候选报告。

本脚本只采集公开 DOM 元数据，不自动登录、不破解验证码。
运行后人工检查 qianlima-dom-probe.json，再生成 qianlima-dom.json。
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright


async def collect_elements(page: Any) -> dict[str, Any]:
    return await page.evaluate(
        """
        () => {
          const describe = (el) => ({
            tag: el.tagName.toLowerCase(),
            id: el.id || null,
            name: el.getAttribute('name'),
            type: el.getAttribute('type'),
            placeholder: el.getAttribute('placeholder'),
            className: typeof el.className === 'string'
              ? el.className : null,
            text: (el.innerText || el.alt || '').trim().slice(0, 120),
            src: el.getAttribute('src'),
            href: el.getAttribute('href')
          });
          return {
            inputs: [...document.querySelectorAll('input')].map(describe),
            buttons: [...document.querySelectorAll(
              'button,input[type="submit"]'
            )].map(describe),
            images: [...document.querySelectorAll('img')].map(describe),
            links: [...document.querySelectorAll('a')].slice(0, 300).map(describe),
            forms: [...document.querySelectorAll('form')].map(describe)
          };
        }
        """
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--login-url",
        default="https://www.qianlima.com/login",
    )
    parser.add_argument(
        "--search-url",
        default="https://www.qianlima.com/",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("qianlima-dom-probe.json"),
    )
    args = parser.parse_args()

    async with async_playwright() as driver:
        browser = await driver.chromium.launch(
            headless=False
        )
        context = await browser.new_context(
            locale="zh-CN"
        )

        result: dict[str, Any] = {
            "verified": False,
            "note": (
                "探测结果需人工确认后，才能标记 verified=true"
            ),
        }

        for key, url in (
            ("login_probe", args.login_url),
            ("search_probe", args.search_url),
        ):
            page = await context.new_page()
            await page.goto(
                url,
                wait_until="domcontentloaded",
            )
            result[key] = {
                "requested_url": url,
                "final_url": page.url,
                "title": await page.title(),
                "elements": await collect_elements(page),
            }
            await page.screenshot(
                path=f"{key}.png",
                full_page=True,
            )
            await page.close()

        args.output.write_text(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        await context.close()
        await browser.close()

    print("DOM 探测完成:", args.output)


if __name__ == "__main__":
    asyncio.run(main())
