"""Amazon 商品抓取模板。

注意：Amazon 反爬较严，模板仅给出常见商品页字段。
生产环境需配合代理池 + 高质量 UA 使用。
"""

from __future__ import annotations

from app.templates.base import ScrapeTemplate

AMAZON_TEMPLATE = ScrapeTemplate(
    name="amazon",
    selectors={
        "title": "#productTitle",
        "price": ".a-price .a-offscreen",
        "rating": 'span[data-hook="rating-out-of-text"]',
        "review_count": "#acrCustomerReviewText",
        "availability": "#availability span",
        "asin": 'input[name="ASIN"]',
    },
    list_selector=None,  # 单商品页
    wait_for_selector="#productTitle",
    next_page_selector=".a-pagination li.a-last a",
    max_pages=1,
)
