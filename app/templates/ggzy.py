"""公共资源交易网（ggzy.gov.cn）交易公告抓取模板。

列表项选择器 ul.news-list li 为推测值，字段级选择器同样为合理推测；
如目标子站点页面结构差异较大，应在请求时通过 selectors 参数覆盖。
"""

from __future__ import annotations

from app.templates.base import ScrapeTemplate

GGZY_TEMPLATE = ScrapeTemplate(
    name="ggzy",
    selectors={
        "title": "a, h3, .title",
        "publish_time": ".time, .date, span.time",
        "location": ".location, .area",
        "notice_type": ".type, .category",
        "detail_url": "a",
        "content": ".content, .summary, p",
    },
    list_selector="ul.news-list li",  # 推测
    wait_for_selector="ul.news-list li",
    next_page_selector="a.next-page",
    max_pages=1,
)
