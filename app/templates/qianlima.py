"""千里马招标网登录态采集模板。

命题硬要求：至少 1 个登录态网站采集。

Sol S-11/S-1 升级：
- 不再在模块导入时固化 Cookie（旧实现导致重新登录后旧进程仍用旧 Cookie）
- 改用 SessionManager 动态加载 Playwright storage_state
- storage_state 包含 cookies + origins（LocalStorage），比纯 Cookie 更完整
- Session 失效时降级为匿名访问（不影响其他平台抓取）

使用方式：
    1. 运行 `python scripts/login_qianlima.py --username <user>` 人工完成验证码登录
    2. Session 保存到 data/sessions/qianlima_session.json
    3. scraper.scrape({"template": "qianlima", "url": "..."}) 自动加载 storage_state
    4. Session 失效时返回 401，提示用户重新登录
"""

from __future__ import annotations

from typing import Any

from app.core.session_manager import SessionManager
from app.templates.base import ScrapeTemplate, register_template
from app.utils.logger import get_logger

logger = get_logger("qianlima_template")

# Sol S-11：模块级 SessionManager 单例
# 不在导入时加载文件，避免旧 Cookie 固化
_SESSION_MANAGER = SessionManager("qianlima")


async def get_qianlima_storage_state() -> dict[str, Any] | None:
    """Sol S-11：动态获取千里马 storage_state（含 cookies + origins）。

    Returns:
        Playwright storage_state 字典；无有效 Session 时返回 None（降级匿名访问）
    """
    # 校验 Session 是否有效（域名匹配 + 关键 Cookie 未过期）
    valid = await _SESSION_MANAGER.is_valid(
        domain_suffix="qianlima.com",
    )

    if not valid:
        logger.warning(
            "qianlima 无有效登录态，本次降级为匿名访问"
        )
        return None

    return await _SESSION_MANAGER.load_state()


async def get_qianlima_cookies() -> list[dict[str, Any]]:
    """兼容调用方：从 storage_state 提取 cookies 列表。

    保留这个接口是为了向后兼容旧 scraper（如果它只支持 cookies 不支持 storage_state）。
    新代码应直接调用 get_qianlima_storage_state()。
    """
    state = await get_qianlima_storage_state()
    if state is None:
        return []

    cookies = state.get("cookies", [])
    return list(cookies) if isinstance(cookies, list) else []


def register_qianlima_template() -> None:
    """注册千里马模板。

    选择器为候选值，GPT-5.6 Sol 后续可根据真实页面调整（S-13 DOM 探测脚本输出）。
    不再在模板注册时固化 Cookie；scraper 在 scrape() 时动态加载 storage_state。
    """
    template = ScrapeTemplate(
        name="qianlima",
        selectors={
            "project_name": "h1.detail-title, .project-name, .title",
            "bid_number": ".bid-number, .project-no, span:has-text('编号')",
            "budget_amount": ".budget, .amount, span:has-text('预算')",
            "location": ".location, .area",
            "publish_time": ".publish-time, .date, time",
            "deadline": ".deadline, .end-time",
            "tender_org": ".tender-org, .buyer, span:has-text('采购人')",
            "agency": ".agency, .proxy",
            "contact_name": ".contact, .contact-name",
            "contact_phone": ".contact-phone, .phone",
            "contact_email": ".contact-email, .email",
            "notice_type": ".notice-type, .type",
            "core_content": ".content, .detail-content, #content",
            "attachment_url": "a.attachment, a[href$='.pdf'], a[href$='.doc']",
        },
        list_selector=".list-item, .bid-item, ul.list > li",
        wait_for_selector=".content, .detail-content, h1",
        next_page_selector=None,
        max_pages=1,
    )
    # Sol S-11：不再 template.__dict__["cookies"] = cookies 固化
    # storage_state 由 scraper 在 scrape() 时根据 template 名动态加载
    register_template(template)
    has_session = _SESSION_MANAGER.has_session()
    logger.info(
        "qianlima template registered (session_file=%s, has_session=%s)",
        _SESSION_MANAGER.session_path, has_session,
    )


# 兼容旧调用（scraper._merge_template 会 getattr(tpl, "cookies", None)）
# 这里返回空列表，真正登录态走 storage_state 通道
def _legacy_get_cookies() -> list[dict[str, Any]]:
    """旧接口：返回空，登录态走 storage_state。"""
    return []
