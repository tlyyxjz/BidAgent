"""标小智 内置网站模板注册."""

from __future__ import annotations

from app.templates.amazon import AMAZON_TEMPLATE
from app.templates.base import (
    ScrapeTemplate,
    get_template,
    list_templates,
    register_template,
)
from app.templates.ccgp import CCGP_TEMPLATE
from app.templates.chinabidding import CHINABIDDING_TEMPLATE
from app.templates.ggzy import GGZY_TEMPLATE
from app.templates.news import NEWS_TEMPLATE
from app.templates.qianlima import register_qianlima_template
from app.templates.reddit import REDDIT_TEMPLATE

# 注册内置模板
register_template(AMAZON_TEMPLATE)
register_template(REDDIT_TEMPLATE)
register_template(NEWS_TEMPLATE)
register_template(CCGP_TEMPLATE)
register_template(CHINABIDDING_TEMPLATE)
register_template(GGZY_TEMPLATE)
# 千里马登录态采集模板（命题硬要求：≥1 登录态网站）
register_qianlima_template()

__all__ = [
    "ScrapeTemplate",
    "register_template",
    "get_template",
    "list_templates",
    "AMAZON_TEMPLATE",
    "REDDIT_TEMPLATE",
    "NEWS_TEMPLATE",
    "CCGP_TEMPLATE",
    "CHINABIDDING_TEMPLATE",
    "GGZY_TEMPLATE",
]
