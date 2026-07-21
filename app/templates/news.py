"""新闻文章抓取模板。

通用 schema.org NewsArticle 风格的选择器；覆盖大多数主流新闻站点。
"""

from __future__ import annotations

from app.templates.base import ScrapeTemplate

NEWS_TEMPLATE = ScrapeTemplate(
    name="news",
    selectors={
        "headline": "h1",
        "subheadline": "h2, .subtitle, .article-subtitle",
        "author": '[rel="author"], .author, .byline',
        "published": 'time, [datetime], .published, .date',
        "body": "article, .article-body, .story-body, main",
    },
    list_selector=None,  # 单文章页
    wait_for_selector="h1",
    next_page_selector=None,
    max_pages=1,
)
