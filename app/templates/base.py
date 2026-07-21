"""模板基类与注册表。

每个内置模板定义一组默认 CSS 选择器，用户可在请求中显式覆盖。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ScrapeTemplate:
    """抓取模板：定义默认选择器与翻页规则。

    Attributes:
        name: 模板名（小写，例如 amazon/reddit/news）。
        selectors: 字段名 -> CSS 选择器 的映射。
        list_selector: 列表项选择器；为空表示单条目抓取。
        wait_for_selector: JS 渲染等待选择器。
        next_page_selector: 下一页按钮选择器（可选）。
        max_pages: 默认最大翻页数。
    """

    name: str
    selectors: dict[str, str] = field(default_factory=dict)
    list_selector: str | None = None
    wait_for_selector: str | None = None
    next_page_selector: str | None = None
    max_pages: int = 1


# 模板注册表（在 templates/__init__.py 中通过 register_template 注册）
_REGISTRY: dict[str, ScrapeTemplate] = {}


def register_template(template: ScrapeTemplate) -> None:
    """注册一个模板到全局注册表。"""
    _REGISTRY[template.name.lower()] = template


def get_template(name: str) -> ScrapeTemplate | None:
    """根据名称获取模板；不存在返回 None。"""
    return _REGISTRY.get(name.lower())


def list_templates() -> list[str]:
    """返回所有已注册模板名。"""
    return sorted(_REGISTRY.keys())
