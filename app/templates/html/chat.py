"""聊天 Demo 页 HTML（W2-06 智能问答 · 6 Agent 协作）。

用于 Demo 视频展示：用户输入查询 → 展示 6 Agent 协作进度 → 输出 Word 报告下载链接。

为满足单文件 ≤ 300 行的工程约束，本模块已按职责拆分为子模块：
- `_chat_styles`：`<style>` CSS 样式块（CHAT_CSS）
- `_chat_body`：`<body>` HTML 结构块（CHAT_BODY）
- `_chat_scripts`：`<script>` JS 脚本块（CHAT_SCRIPT）

本文件仅做组装，保持对外公开 API（`CHAT_HTML` 常量）不变，
向后兼容所有 `from app.templates.html.chat import CHAT_HTML` 的导入路径。
"""

from __future__ import annotations

from app.templates.html._chat_body import CHAT_BODY
from app.templates.html._chat_scripts import CHAT_SCRIPT
from app.templates.html._chat_styles import CHAT_CSS

CHAT_HTML = (
    '<!DOCTYPE html>\n'
    '<html lang="zh-CN">\n'
    '<head>\n'
    '<meta charset="UTF-8">\n'
    '<meta name="viewport" content="width=1366, initial-scale=1">\n'
    '<title>标小智 · 智能问答 · 6 Agent 协作</title>\n'
    + CHAT_CSS + '\n'
    + '<link rel="stylesheet" href="/static/vendor/phosphor/phosphor-icons.min.css" />\n'
    + '</head>\n'
    + CHAT_BODY
    + CHAT_SCRIPT + '\n'
    + '</div>\n'
    + '</div>\n'
    + '\n'
    + '</body>\n'
    + '</html>'
)

__all__ = ["CHAT_HTML"]
