"""事实断言键 (v4.1 第 6.5 节).

用于判断跨源字段是否可比, 避免错误的冲突判定.

字段:
- project_id: 所属项目 (不同项目不可比)
- notice_type: 公告类型 (tender/award/correction/clarification/
              cancellation/contract/other)
- field_name: 字段名
- semantic_role: 企业或字段业务角色
- amount_type: 金额类型 (budget/ceiling/award/contract/unit_price)
              budget vs award 不可比
- lot_id: 分包 (不同分包不可比)
- effective_time: 事实有效时间 (ISO 8601 date string)
- entity_role: 采购人/中标人/代理机构等 (不同角色不可比)
                purchaser/winner/bidder/consortium_member
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


RULE_VERSION = "fact_assertion_key_v1.0"


@dataclass(frozen=True)
class FactAssertionKey:
    """事实断言键, 用于判断跨源字段是否可比."""

    project_id: str
    notice_type: str
    field_name: str
    semantic_role: Optional[str] = None
    amount_type: Optional[str] = None
    lot_id: Optional[str] = None
    effective_time: Optional[str] = None
    entity_role: Optional[str] = None

    def is_compatible(self, other):
        """判断两个断言键是否可比.

        不可比的情况:
        - project_id 不同 (不同项目)
        - field_name 不同 (不同字段)
        - amount_type 不同 (budget vs award)
        - lot_id 不同 (不同分包)
        - entity_role 不同 (不同实体)

        可比的情况:
        - 完全相同 (可能一致)
        - notice_type 不同但属于同一项目版本链 (版本差异)
        - semantic_role / effective_time 可以不同 (允许)
        """
        if self.project_id != other.project_id:
            return False
        if self.field_name != other.field_name:
            return False
        if self.amount_type != other.amount_type:
            return False
        if self.lot_id != other.lot_id:
            return False
        if self.entity_role != other.entity_role:
            return False
        return True

    def is_identical(self, other):
        """完全相同（所有字段一致）."""
        return self == other

    def conflict_reason(self, other):
        """若不可比, 返回原因字符串; 可比返回 None."""
        if self.project_id != other.project_id:
            return "different_project"
        if self.field_name != other.field_name:
            return "different_field"
        if self.amount_type != other.amount_type:
            return f"different_amount_type: {self.amount_type} vs {other.amount_type}"
        if self.lot_id != other.lot_id:
            return f"different_lot: {self.lot_id} vs {other.lot_id}"
        if self.entity_role != other.entity_role:
            return f"different_entity_role: {self.entity_role} vs {other.entity_role}"
        return None


def build_assertion_key(
    project_id,
    notice_type,
    field_name,
    semantic_role=None,
    amount_type=None,
    lot_id=None,
    effective_time=None,
    entity_role=None,
):
    """便捷构造函数."""
    return FactAssertionKey(
        project_id=project_id,
        notice_type=notice_type,
        field_name=field_name,
        semantic_role=semantic_role,
        amount_type=amount_type,
        lot_id=lot_id,
        effective_time=effective_time,
        entity_role=entity_role,
    )


def assert_keys_compatible(k1, k2):
    """断言两个 key 可比, 不可比时抛 ValueError（带原因）."""
    reason = k1.conflict_reason(k2)
    if reason is not None:
        raise ValueError(f"断言键不可比: {reason}")
    return True
