"""金额校验逻辑。

从 field_validator.py 拆分而来，包含金额相关的常量、正则模式和校验函数。
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from app.processors.field_validator import ValidationResult
from app.processors.field_validator_tolerance import _compute_tolerance

# 金额正则：支持多种格式
# 匹配：100万、100.00万元、1000000元、100.00 元、￥100、100.00、1.5亿、0.0769040（万元）
_AMOUNT_PATTERN = re.compile(
    r"^(?P<currency>￥|¥|人民币|美元|USD|\$|欧元|EUR|€)?\s*"
    r"(?P<amount>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*"
    r"(?P<unit>亿元|万元|万元人民币|元|元人民币|万|亿|)?"
    r"\s*$"
)

# 金额前缀正则：剥离"中标（成交）金额：人民币"、"预算金额："等上下文
_AMOUNT_PREFIX_PATTERN = re.compile(
    r"^(?:中标|成交|预算|控制价|合同|采购|项目)?(?:（[^）]*）)?"
    r"(?:金额|总价|价格|价)?[：:\s]*"
    r"(?:人民币|美元|欧元)?\s*"
)

# 金额后缀正则：剥离"（人民币）"、"（美元）"、"（含税）"等
_AMOUNT_SUFFIX_PATTERN = re.compile(
    r"（[^）]*）$"
)

# 金额类型枚举（Sol 要求：budget/ceiling/award/contract/unit_price）
AMOUNT_TYPES = {
    "budget": "预算金额",
    "ceiling": "控制价（上限）",
    "award": "中标金额",
    "contract": "合同金额",
    "unit_price": "单价",
}

# 货币枚举
CURRENCIES = {
    "人民币": "CNY",
    "CNY": "CNY",
    "￥": "CNY",
    "¥": "CNY",
    "美元": "USD",
    "USD": "USD",
    "$": "USD",
    "欧元": "EUR",
    "EUR": "EUR",
    "€": "EUR",
}


def validate_amount(
    raw_value: str,
    amount_type: Optional[str] = None,
    expected_currency: str = "CNY",
    lot_amounts: Optional[List[Tuple[str, str]]] = None,
) -> ValidationResult:
    """校验金额字段值。

    Args:
        raw_value: 金额原始值（如 "100.00万元"）
        amount_type: 金额类型（budget/ceiling/award/contract/unit_price）
        expected_currency: 期望货币（默认人民币）
        lot_amounts: 分包金额列表 [(lot_id, amount_raw), ...]，用于分包一致性检查

    Returns:
        ValidationResult
    """
    if not raw_value or not raw_value.strip():
        return ValidationResult(
            valid=False,
            raw_value=raw_value,
            errors=["金额值为空"],
        )

    raw = raw_value.strip()
    preprocess_steps = []

    # 容错：amount_type='unknown' 视为 None（金标中标注员未确定类型时填 unknown）
    if amount_type and amount_type not in AMOUNT_TYPES:
        if amount_type.lower() in ("unknown", "其他", "未指定", ""):
            amount_type = None
            preprocess_steps.append("amount_type=unknown→None")
        else:
            return ValidationResult(
                valid=False,
                raw_value=raw_value,
                errors=[f"非法金额类型: {amount_type}，合法值: {list(AMOUNT_TYPES.keys())}"],
            )

    # 预处理1：剥离前缀（"中标（成交）金额：人民币"等）
    cleaned = raw
    prefix_match = _AMOUNT_PREFIX_PATTERN.match(cleaned)
    if prefix_match and prefix_match.group(0):
        cleaned = cleaned[prefix_match.end():].strip()
        if cleaned != raw:
            preprocess_steps.append(f"剥离前缀: '{raw[:30]}...' → '{cleaned[:30]}'")

    # 预处理2：支持"0.0769040（万元）"格式（括号包单位）
    # 注意：必须先于"剥离后缀"检查，否则"（万元）"会被当普通后缀剥离掉
    bracket_unit_match = re.search(r"（\s*(万元|亿元|元|万|亿)\s*）\s*$", cleaned)
    if bracket_unit_match:
        unit_in_bracket = bracket_unit_match.group(1)
        cleaned = cleaned[:bracket_unit_match.start()].strip() + unit_in_bracket
        preprocess_steps.append(f"括号单位展开: '（{unit_in_bracket}）' → '{unit_in_bracket}'")

    # 预处理3：剥离后缀"（人民币）"、"（美元）"等（在括号单位展开之后）
    suffix_match = _AMOUNT_SUFFIX_PATTERN.search(cleaned)
    if suffix_match:
        suffix_text = suffix_match.group(0)
        cleaned = cleaned[:suffix_match.start()].strip()
        preprocess_steps.append(f"剥离后缀: '{suffix_text}'")

    # 匹配金额格式
    match = _AMOUNT_PATTERN.match(cleaned)
    if not match:
        return ValidationResult(
            valid=False,
            raw_value=raw_value,
            errors=[f"金额格式不合法: '{raw}'（预处理后: '{cleaned}'），期望格式: 数字+单位（如 100.00万元 / 1.5亿元）"],
        )

    amount_str = match.group("amount")
    unit = match.group("unit") or ""
    currency_str = match.group("currency") or ""

    try:
        # 千分位支持：去掉逗号（如 "1,234.56" -> "1234.56"）
        amount = float(amount_str.replace(",", ""))
    except ValueError:
        return ValidationResult(
            valid=False,
            raw_value=raw_value,
            errors=[f"金额数字解析失败: {amount_str}"],
        )

    # 货币识别（Sol 要求：币种一致性检查）
    currency = expected_currency
    if currency_str:
        currency = CURRENCIES.get(currency_str, expected_currency)

    # 单位转换：统一为"元"（Sol 要求：万元/亿元转元）
    amount_in_yuan = amount
    derivation_parts = []
    if "亿" in unit:
        amount_in_yuan = amount * 100000000  # 1亿 = 10^8
        derivation_parts.append(f"{amount}亿 × 10^8 = {amount_in_yuan}元")
    elif "万" in unit:
        amount_in_yuan = amount * 10000  # 1万 = 10^4
        derivation_parts.append(f"{amount}万 × 10^4 = {amount_in_yuan}元")

    # 规范化字符串：数字 + 单位
    if amount_in_yuan >= 100000000:
        normalized = f"{amount_in_yuan / 100000000:.2f}亿元"
    elif amount_in_yuan >= 10000:
        normalized = f"{amount_in_yuan / 10000:.2f}万元"
    else:
        normalized = f"{amount_in_yuan:.2f}元"

    warnings = []
    if amount == 0:
        warnings.append("金额为0，可能为流标或异常")

    # 分包一致性检查（Sol 要求）
    if lot_amounts:
        lot_sum = 0.0
        lot_errors = []
        for lot_id, lot_raw in lot_amounts:
            lot_result = validate_amount(lot_raw, amount_type=amount_type, expected_currency=currency)
            if lot_result.valid:
                lot_sum += lot_result.normalized_value or 0
            else:
                lot_errors.append(f"分包 {lot_id} 金额无效: {lot_raw}")
        if lot_errors:
            return ValidationResult(
                valid=False,
                raw_value=raw_value,
                errors=lot_errors,
            )
        # 精度容差：分包总和与总金额误差不超过最小显示单位一半
        tolerance = _compute_tolerance(amount_in_yuan)
        diff = abs(amount_in_yuan - lot_sum)
        if diff > tolerance:
            warnings.append(
                f"分包总和 {lot_sum:.2f}元 与总金额 {amount_in_yuan:.2f}元 差异 {diff:.2f}元，"
                f"超过容差 {tolerance:.2f}元"
            )
        derivation_parts.append(f"分包总和 = {lot_sum:.2f}元")

    # 推导规则（Sol 要求）：包含预处理步骤
    all_parts = preprocess_steps + derivation_parts
    derivation_rule = " | ".join(all_parts) if all_parts else None

    return ValidationResult(
        valid=True,
        raw_value=raw_value,
        normalized=normalized,
        normalized_value=amount_in_yuan,
        currency=currency,
        amount_type=amount_type,
        warnings=warnings,
        derivation_rule=derivation_rule,
    )
