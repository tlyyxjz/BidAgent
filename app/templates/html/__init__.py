"""Web UI HTML 模板（S-4 拆分自 app/api/ui.py）。

历史：每个文件曾提供 HTML 字符串常量供 ui.py 直接返回。
v4.1：所有 UI 页面已迁出为独立 static/*.html 文件，由 ui.py 的 _serve_static_html() 加载，
本包不再导出 HTML 字符串常量。保留包以兼容潜在的旧导入路径。
"""

__all__: list[str] = []
