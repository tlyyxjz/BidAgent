"""Reddit 帖子抓取模板（旧版 web Reddit 页面）。

注意：Reddit 强烈建议使用官方 API。模板面向子版列表页（old.reddit.com）。
"""

from __future__ import annotations

from app.templates.base import ScrapeTemplate

REDDIT_TEMPLATE = ScrapeTemplate(
    name="reddit",
    selectors={
        "title": "a.title",
        "score": ".score.unvoted",
        "author": ".author",
        "comments": ".comments",
        "time": "time",
    },
    list_selector=".thing",  # 每个帖子
    wait_for_selector=".thing",
    next_page_selector=".next-button a",
    max_pages=1,
)
