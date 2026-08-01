"""Web UI HTML 模板（S-4 拆分自 app/api/ui.py）。

每个文件提供一个 HTML 字符串常量，供 ui.py 直接返回。
"""

from app.templates.html.chat import CHAT_HTML

__all__ = [
    "CHAT_HTML",
]
