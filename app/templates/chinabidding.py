"""中国招标投标网（chinabidding.cn）招标公告抓取模板。

列表项选择器 ul.bid-list li 为推测值，字段级选择器同样为合理推测；
生产环境如发现页面结构变化，应通过请求参数显式覆盖 selectors。
"""

from __future__ import annotations

from app.templates.base import ScrapeTemplate

CHINABIDDING_TEMPLATE = ScrapeTemplate(
    name="chinabidding",
    selectors={
        "title": "a, h3, .title",
        "publish_time": ".time, .date, span.time",
        "location": ".location, .area",
        "notice_type": ".type, .category",
        "detail_url": "a",
        "content": ".content, .summary, p",
    },
    list_selector="ul.bid-list li",  # 推测
    wait_for_selector="ul.bid-list li",
    next_page_selector="a.page-next",
    max_pages=1,
)
