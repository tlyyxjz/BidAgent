"""tender_project 实体共用的 ULID 主键生成器。

从 `app.models.tender_project` 中拆出，避免多个实体子模块循环导入。
"""

from __future__ import annotations

try:  # ulid-py 已安装则优先使用
    import ulid as _ulid

    def _new_ulid() -> str:
        """生成 26 字符 ULID 字符串。"""
        return str(_ulid.new())
except ImportError:  # pragma: no cover
    import uuid

    def _new_ulid() -> str:
        # TODO: 安装 ulid-py 后替换为真正的 ULID 生成
        return uuid.uuid4().hex[:26]


__all__ = ["_new_ulid"]
