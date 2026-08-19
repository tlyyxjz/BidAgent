"""湖北省政府采购网（ccgp-hubei.gov.cn）中标(成交)公告抓取模板。

2026-08-16 实测验证（HTTP GET 真实页面）：
- 首页 中标(成交) tab `#area-1-4` 为服务端直出列表，无 AJAX；
- 列表项结构：`li > a[href=/notice/YYYYMM/notice_<32hex>.html] + span[日期]`；
- 标题含 `[采购方式]` 前缀（a 内 font 标签），详情页标题在 h2、正文在 p；
- 详情页字段格式：`一、项目编号 X`、`中标（成交）金额： 382.47103 (万元)`（半角括号）、
  `1、采购人信息 名 称： XXX`、`发布日期：2026-08-16 20:32`（横杠格式）。
- robots.txt：无（404）→ 按惯例视为允许；采集时仍受域名级限流约束。

TODO：站内"更多"列表页在 https://www.ccgp-hubei.gov.cn:9040/quSer/searchXmgg.html，
待实机验证后可加翻页。
"""

from __future__ import annotations

from app.templates.base import ScrapeTemplate

HUBEI_TEMPLATE = ScrapeTemplate(
    name="hubei",
    selectors={
        "title": "a",
        "publish_time": "span",
        "notice_type": "a font",
        "detail_url": "a",
        "content": "h2, p",
    },
    list_selector="#area-1-4 ul.news-list-content li",  # 2026-08-16 实测验证
    wait_for_selector="#area-1-4 ul.news-list-content li",
    next_page_selector=None,
    max_pages=1,
)
