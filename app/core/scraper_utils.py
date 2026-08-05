"""抓取工具函数（从 scraper.py 拆分）。

包含模板配置合并等工具函数。
"""
from __future__ import annotations

from typing import Any

from app.templates import get_template
from app.templates.base import ScrapeTemplate
from app.utils.logger import get_logger

logger = get_logger("scraper")


def merge_template(request: dict[str, Any]) -> dict[str, Any]:
    """合并模板默认配置；用户显式字段优先。"""
    template_name = request.get("template")
    if not template_name:
        return dict(request)

    tpl: ScrapeTemplate | None = get_template(template_name)
    if tpl is None:
        logger.warning("未知模板 %s，忽略", template_name)
        return dict(request)

    merged: dict[str, Any] = {
        "selectors": dict(tpl.selectors),
        "list_selector": tpl.list_selector,
        "wait_for_selector": tpl.wait_for_selector,
        "next_page_selector": tpl.next_page_selector,
        # Sol S-11：保留 template 名，scrape() 时根据它动态加载 storage_state
        "template": template_name,
    }
    # S-3 修复：传递模板上的 cookies（登录态采集，如 qianlima 模板）
    # 用户显式传 cookies 时优先用户传的（在下方覆盖逻辑里处理）
    template_cookies = getattr(tpl, "cookies", None)
    if template_cookies:
        merged["cookies"] = list(template_cookies)
    # 用户字段覆盖模板
    # Sol S-11：新增 storage_state 可覆盖字段（用户显式传入优先）
    for key in ("selectors", "list_selector", "wait_for_selector",
                "next_page_selector", "max_pages", "cookies",
                "extra_headers", "storage_state"):
        if key in request and request[key]:
            if key == "selectors" and isinstance(request[key], dict):
                merged["selectors"].update(request[key])
            else:
                merged[key] = request[key]
    merged["url"] = request.get("url")
    return merged
