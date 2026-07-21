"""中国政府采购网（ccgp.gov.cn）招投标公告抓取模板。

面向公告列表页，列表项选择器 ul.cggg-list li 已经过线上验证；
字段级选择器为基于页面结构的合理推测，可在请求时显式覆盖。
"""

from __future__ import annotations

from app.templates.base import ScrapeTemplate

CCGP_TEMPLATE = ScrapeTemplate(
    name="ccgp",
    selectors={
        "title": "a",
        "publish_time": ".time, .date, span.time",
        "location": ".location, .area",
        "notice_type": ".type, .category",
        "detail_url": "a",
        "content": ".content, .summary, p",
    },
    list_selector="ul.cggg-list li",  # 已验证
    wait_for_selector="ul.cggg-list li",
    next_page_selector="a.next",
    max_pages=1,
)
