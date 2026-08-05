"""W2-04 金额/日期/编号确定性校验。

对应 Sol 规划 v4.1 第六章 6.2「确定性等价变换示例」+ 第七章 7.3「金额正确性判定」。

校验规则：
1. 金额校验：
   - 万元/亿元转元（Sol 要求）
   - 精度容差：最大允许误差不超过原文最小显示单位的一半（Sol 要求）
   - 金额类型一致性检查（budget/ceiling/award/contract/unit_price）（Sol 要求）
   - 分包一致性检查（Sol 要求）
   - 币种一致性检查（Sol 要求）

2. 日期校验：
   - 格式统一（YYYY-MM-DD）
   - 支持点号、年月日汉字等格式（Sol 要求）

3. 编号校验：
   - 空格和连接符规范化
   - 全角转半角
   - 大小写统一

工程约束：
- 校验规则版本必须记录（Sol 要求）
- 校验结果不得覆盖原始值（raw_value 保留）（Sol 要求）
- 推导结果必须保存全部输入证据和推导规则（Sol 要求）
- 纯确定性规则，不调用 LLM
- 校验失败不抛异常，返回 ValidationResult 包含错误信息
- 支持 batch 校验
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# ========== 版本号 ==========

VALIDATOR_VERSION = "1.4"  # v1.2: 主编号非法时回退取括号里“招标编号：XXX”


@dataclass
class ValidationResult:
    """校验结果。

    约束：
    - valid=True 时 normalized 字段有值
    - valid=False 时 errors 包含错误信息
    - raw_value 始终保留（Sol 要求：不得覆盖原始值）
    - 推导规则保存在 derivation_rule 字段（Sol 要求）
    """
    valid: bool
    raw_value: Optional[str] = None  # Sol 要求：保留原始值
    normalized: Optional[str] = None
    normalized_value: Optional[float] = None  # 用于金额（元）
    currency: Optional[str] = None  # 用于金额
    amount_type: Optional[str] = None  # 用于金额
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    derivation_rule: Optional[str] = None  # Sol 要求：保存推导规则


# ========== 从子模块导入校验函数和常量 ==========
# 注意：必须在 ValidationResult 定义之后导入，因为子模块依赖 ValidationResult。
from app.processors.field_validator_amount import (  # noqa: E402
    AMOUNT_TYPES,
    CURRENCIES,
    validate_amount,
)
from app.processors.field_validator_tolerance import (  # noqa: E402
    _compute_tolerance,
    _compute_tolerance_from_precision,
    _parse_display_precision,
)
from app.processors.field_validator_date import validate_date  # noqa: E402
from app.processors.field_validator_identifier import (  # noqa: E402
    validate_project_identifier,
)


# ========== 批量校验 ==========

def validate_amount_batch(items: List[Tuple[str, Optional[str]]]) -> List[ValidationResult]:
    """批量校验金额。

    Args:
        items: [(raw_value, amount_type), ...]

    Returns:
        List[ValidationResult]
    """
    return [validate_amount(raw, amount_type) for raw, amount_type in items]


def validate_date_batch(items: List[str]) -> List[ValidationResult]:
    """批量校验日期。"""
    return [validate_date(raw) for raw in items]


def validate_identifier_batch(items: List[str]) -> List[ValidationResult]:
    """批量校验编号。"""
    return [validate_project_identifier(raw) for raw in items]


# ========== 通用字段校验调度器 ==========

def validate_field(
    field_type: str,
    raw_value: str,
    *,
    amount_type: Optional[str] = None,
) -> ValidationResult:
    """通用字段校验调度器（按 field_type 分发到具体校验函数）。

    Args:
        field_type: 字段类型（"amount" / "date" / "project_identifier"）
        raw_value: 原始值
        amount_type: 金额类型（仅 field_type="amount" 时使用）

    Returns:
        ValidationResult
    """
    if field_type == "amount":
        return validate_amount(raw_value, amount_type)
    if field_type == "date":
        return validate_date(raw_value)
    if field_type == "project_identifier":
        return validate_project_identifier(raw_value)
    return ValidationResult(
        valid=False, raw_value=raw_value, errors=[f"未知字段类型: {field_type}"]
    )
