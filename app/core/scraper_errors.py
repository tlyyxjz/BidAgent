"""抓取错误类型（从 scraper.py 拆分）。"""


class ScrapeError(Exception):
    """抓取过程中的统一错误。"""


class HttpForbiddenError(Exception):
    """HTTP 403 Forbidden：被反爬封禁，必须停止抓取，不得换 UA/代理重试。"""
