"""中国政府采购网（ccgp.gov.cn）招投标公告抓取模板。

面向公告列表页(2026-08-02 实测更新)：
- list_selector: ul.vT-srch-result-list-bid li (旧 ul.cggg-list li 已失效)
- 字段选择器基于真实页面结构验证
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
    list_selector="ul.vT-srch-result-list-bid li",  # 2026-08-02 实测验证
    wait_for_selector="ul.vT-srch-result-list-bid li",
    next_page_selector="a.next",
    max_pages=1,
)
