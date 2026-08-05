"""版本差异 vs 事实冲突判定（从 source_lineage.py 拆分）。

对应总规划 v4.1 第六章 6.3「来源谱系判定」中的冲突区分逻辑。

W3 周验收要求：
- 同一项目不同公告（招标/中标/更正）不会被误判为冲突
- 同一公告转载不会被误判为独立验证
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ConflictJudgment:
    """冲突判定结果。"""
    is_conflict: bool
    is_version_diff: bool
    reason: str


def judge_field_conflict(
    fact_key_a: str,
    fact_key_b: str,
    field_name: str,
    value_a: str,
    value_b: str,
    project_identifier: str,
    notice_type_a: str,
    notice_type_b: str,
) -> ConflictJudgment:
    """判断两个字段值是版本差异还是事实冲突。

    W3 周验收要求：
    - 同一项目不同公告（招标/中标/更正）不会被误判为冲突
    - 同一公告转载不会被误判为独立验证

    判定规则：
    1. fact_key 相同 → 非冲突（同值）
    2. fact_key 不同但 project_identifier 相同 + notice_type 不同 → 版本差异
       （如招标公告预算 vs 中标公告合同金额，是正常的版本演进）
    3. fact_key 不同且 project_identifier 相同 + notice_type 相同 → 事实冲突
       （同一公告类型同一项目同一字段不同值，可能是数据错误）
    4. project_identifier 不同 → 不比较（不同项目）

    Args:
        fact_key_a: 字段 A 的事实断言键
        fact_key_b: 字段 B 的事实断言键
        field_name: 字段名
        value_a: 字段 A 的值
        value_b: 字段 B 的值
        project_identifier: 项目编号
        notice_type_a: 公告 A 类型 (tender/award/correction)
        notice_type_b: 公告 B 类型

    Returns:
        ConflictJudgment
    """
    # fact_key 相同 → 非冲突
    if fact_key_a == fact_key_b:
        return ConflictJudgment(
            is_conflict=False, is_version_diff=False,
            reason="事实断言键相同，同值非冲突"
        )

    # 不同项目不比较
    if not project_identifier:
        return ConflictJudgment(
            is_conflict=False, is_version_diff=False,
            reason="缺少项目编号，无法判断"
        )

    # 同项目同公告类型同字段不同值 → 事实冲突
    if notice_type_a == notice_type_b:
        return ConflictJudgment(
            is_conflict=True, is_version_diff=False,
            reason=f"同项目({project_identifier})同公告类型({notice_type_a})"
                   f"同字段({field_name})不同值: '{value_a}' vs '{value_b}'"
        )

    # 同项目不同公告类型同字段不同值 → 版本差异
    return ConflictJudgment(
        is_conflict=False, is_version_diff=True,
        reason=f"同项目({project_identifier})不同公告类型"
               f"({notice_type_a}→{notice_type_b})字段({field_name})"
               f"值变化: '{value_a}' → '{value_b}'"
    )
