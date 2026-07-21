"""Web UI HTML 模板（S-4 拆分自 app/api/ui.py）。

每个文件提供一个 HTML 字符串常量，供 ui.py 直接返回。
"""

from app.templates.html.index import INDEX_HTML
from app.templates.html.subscriptions import SUBSCRIPTIONS_HTML
from app.templates.html.tenders import TENDERS_HTML

__all__ = ["INDEX_HTML", "SUBSCRIPTIONS_HTML", "TENDERS_HTML"]
