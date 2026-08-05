"""页面内容提取（从 scraper.py 拆分）。

包含单条目提取、列表提取、翻页点击。
"""
from __future__ import annotations

from typing import Any

from playwright.async_api import (
    Page,
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
)

from app.utils.logger import get_logger

logger = get_logger("scraper")


async def extract_page(
    page: Page,
    selectors: dict[str, str],
    list_selector: str | None,
) -> list[dict[str, Any]]:
    """从当前 page 提取数据。"""
    if not selectors:
        # 没有选择器，返回页面纯文本摘要
        title = await page.title()
        return [{"_title": title}]

    if list_selector:
        return await extract_list(page, selectors, list_selector)
    return await extract_single(page, selectors)


async def extract_single(
    page: Page, selectors: dict[str, str]
) -> list[dict[str, Any]]:
    """单条目提取。"""
    item: dict[str, Any] = {}
    for field, sel in selectors.items():
        try:
            element = await page.query_selector(sel)
            item[field] = (await element.inner_text()) if element else None
        except PlaywrightError as exc:
            logger.warning("字段提取失败 field=%s selector=%s err=%s", field, sel, exc)
            item[field] = None
    return [item]


async def extract_list(
    page: Page, selectors: dict[str, str], list_selector: str
) -> list[dict[str, Any]]:
    """列表提取：每个 list_selector 元素都按 selectors 抽取字段。"""
    elements = await page.query_selector_all(list_selector)
    items: list[dict[str, Any]] = []
    for el in elements:
        item: dict[str, Any] = {}
        for field, sel in selectors.items():
            try:
                child = await el.query_selector(sel)
                item[field] = (await child.inner_text()) if child else None
            except PlaywrightError as exc:
                logger.warning(
                    "列表字段提取失败 field=%s selector=%s err=%s", field, sel, exc
                )
                item[field] = None
        items.append(item)
    return items


async def click_next(page: Page, next_page_selector: str) -> bool:
    """点击下一页；返回是否成功。"""
    try:
        btn = await page.query_selector(next_page_selector)
        if not btn:
            return False
        await btn.click()
        # 等待网络空闲
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except PlaywrightTimeoutError:
            pass
        return True
    except PlaywrightError as exc:
        logger.warning("翻页失败 selector=%s err=%s", next_page_selector, exc)
        return False
