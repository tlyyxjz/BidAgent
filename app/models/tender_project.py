"""v4.1 四层实体数据模型（re-export 入口）。

对应《标小智 项目总体规划 v4.1》第四章实体定义，建立四层聚合结构：

    TenderProject（采购项目）
      └─ TenderNotice（业务公告）
           └─ NoticeSource（来源页面）
                └─ NoticeVersion（抓取版本）

并附带两个辅助实体：
- NoticeParticipant：公告参与关系（v4.1 4.5）
- ProjectIdentifier：项目标识（v4.1 4.2）

为满足单文件 ≤ 300 行的工程约束，本模块已按四层聚合拆分为子模块：
- `_tender_ulid`：ULID 主键生成器（_new_ulid）
- `_tender_project_entities`：TenderProject + ProjectIdentifier
- `_tender_notice_entities`：TenderNotice + NoticeParticipant
- `_tender_source_entities`：NoticeSource + NoticeVersion

本文件仅做 re-export，保持对外公开 API 不变，向后兼容所有
`from app.models.tender_project import XXX` 的导入路径。

工程规范：
- 主键统一使用 ULID（String(26)），由 ulid-py 生成。
- 字符串字段必须指定长度；Text 字段例外。
- 外键列建索引；高频过滤字段建索引。
- 与现有 tender.py / organization.py 保持一致的 SQLAlchemy 2.x 风格。
"""

from __future__ import annotations

from app.models._tender_project_entities import (  # noqa: F401
    ProjectIdentifier,
    TenderProject,
)
from app.models._tender_source_entities import (  # noqa: F401
    NoticeSource,
    NoticeVersion,
)
from app.models._tender_notice_entities import (  # noqa: F401
    NoticeParticipant,
    TenderNotice,
)
from app.models._tender_ulid import _new_ulid  # noqa: F401

__all__ = [
    "NoticeParticipant",
    "NoticeSource",
    "NoticeVersion",
    "ProjectIdentifier",
    "TenderNotice",
    "TenderProject",
    "_new_ulid",
]
