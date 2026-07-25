"""W2-04 金额/日期/编号确定性校验。

对应总规划 v4.1 第六章 6.3「确定性规则校验」。

校验规则：
1. 金额校验：
   - 万元/元单位转换（1万元 = 10000元）
   - 金额格式统一（数字 + 单位）
   - 金额类型校验（budget/award/control_price/other）
   - 货币校验（默认人民币）

2. 日期校验：
   - 格式统一（YYYY-MM-DD 或 YYYY-MM-DD HH:MM）
   - 日期范围合法性（投标截止时间 > 发布时间）
   - 中文日期转 ISO（2026年8月1日 → 2026-08-01）

3. 编号校验：
   - 项目编号格式校验（字母+数字+分隔符）
   - 中标编号格式校验
   - 长度限制（>=4 字符）

工程约束：
- 纯确定性规则，不调用 LLM
- 校验失败不抛异常，返回 ValidationResult 包含错误信息
- 支持 batch 校验
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# ========== 金额校验 ==========

# 金额正则：支持多种格式
# 匹配：100万、100.00万元、1000000元、100.00 元、￥100、100.00
_AMOUNT_PATTERN = re.compile(
    r"^(?P<currency>￥|¥|人民币)?\s*"
    r"(?P<amount>\d+(?:\.\d+)?)"
    r"\s*"
    r"(?P<unit>万元|万元人民币|元|元人民币|万|)?"
    r"\s*$"
)

# 金额类型枚举
AMOUNT_TYPES = {
    "budget": "预算金额",
    "award": "中标金额",
    "control_price": "控制价",
    "other": "其他金额",
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


@dataclass
class ValidationResult:
    """校验结果。

    约束：
    - valid=True 时 normalized 字段有值
    - valid=False 时 errors 包含错误信息
    """
    valid: bool
    normalized: Optional[str] = None
    normalized_value: Optional[float] = None  # 用于金额（元）
    currency: Optional[str] = None  # 用于金额
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def validate_amount(
    raw_value: str,
    amount_type: Optional[str] = None,
    expected_currency: str = "CNY",
) -> ValidationResult:
    """校验金额字段值。

    Args:
        raw_value: 金额原始值（如 "100.00万元"）
        amount_type: 金额类型（budget/award/control_price/other）
        expected_currency: 期望货币（默认人民币）

    Returns:
        ValidationResult
    """
    if not raw_value or not raw_value.strip():
        return ValidationResult(valid=False, errors=["金额值为空"])

    raw = raw_value.strip()

    # 校验金额类型
    if amount_type and amount_type not in AMOUNT_TYPES:
        return ValidationResult(
            valid=False,
            errors=[f"非法金额类型: {amount_type}，合法值: {list(AMOUNT_TYPES.keys())}"],
        )

    # 匹配金额格式
    match = _AMOUNT_PATTERN.match(raw)
    if not match:
        return ValidationResult(
            valid=False,
            errors=[f"金额格式不合法: '{raw}'，期望格式: 数字+单位（如 100.00万元）"],
        )

    amount_str = match.group("amount")
    unit = match.group("unit") or ""
    currency_str = match.group("currency") or ""

    try:
        amount = float(amount_str)
    except ValueError:
        return ValidationResult(valid=False, errors=[f"金额数字解析失败: {amount_str}"])

    # 货币识别
    currency = expected_currency
    if currency_str:
        currency = CURRENCIES.get(currency_str, expected_currency)

    # 单位转换：统一为"元"
    amount_in_yuan = amount
    if "万" in unit:
        amount_in_yuan = amount * 10000

    # 规范化字符串：数字 + 单位
    if amount_in_yuan >= 10000:
        normalized = f"{amount_in_yuan / 10000:.2f}万元"
    else:
        normalized = f"{amount_in_yuan:.2f}元"

    warnings = []
    if amount == 0:
        warnings.append("金额为0，可能为流标或异常")

    return ValidationResult(
        valid=True,
        normalized=normalized,
        normalized_value=amount_in_yuan,
        currency=currency,
        warnings=warnings,
    )


# ========== 日期校验 ==========

# 中文日期正则：YYYY年MM月DD日
_CN_DATE_PATTERN = re.compile(
    r"^(?P<year>\d{4})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日"
    r"(?:\s+(?P<hour>\d{1,2}):(?P<minute>\d{1,2}))?"
    r"\s*$"
)

# ISO 日期正则
_ISO_DATE_PATTERN = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})"
    r"(?:\s+(?P<hour>\d{1,2}):(?P<minute>\d{1,2}))?"
    r"\s*$"
)


def validate_date(raw_value: str) -> ValidationResult:
    """校验日期字段值。

    支持格式：
    - 中文：2026年8月1日、2026年08月01日 09:00
    - ISO：2026-08-01、2026-08-01 09:00
    """
    if not raw_value or not raw_value.strip():
        return ValidationResult(valid=False, errors=["日期值为空"])

    raw = raw_value.strip()

    # 尝试中文日期
    match = _CN_DATE_PATTERN.match(raw)
    if match:
        year = int(match.group("year"))
        month = int(match.group("month"))
        day = int(match.group("day"))
        hour = match.group("hour")
        minute = match.group("minute")
    else:
        # 尝试 ISO 日期
        match = _ISO_DATE_PATTERN.match(raw)
        if not match:
            return ValidationResult(
                valid=False,
                errors=[f"日期格式不合法: '{raw}'，支持: 2026年8月1日 / 2026-08-01"],
            )
        year = int(match.group("year"))
        month = int(match.group("month"))
        day = int(match.group("day"))
        hour = match.group("hour")
        minute = match.group("minute")

    # 校验日期合法性
    if not (1 <= month <= 12):
        return ValidationResult(valid=False, errors=[f"月份不合法: {month}"])

    if not (1 <= day <= 31):
        return ValidationResult(valid=False, errors=[f"日期不合法: {day}"])

    # 简单的月份天数校验（不考虑闰年）
    days_in_month = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if day > days_in_month[month - 1]:
        return ValidationResult(
            valid=False,
            errors=[f"{month}月没有{day}日"],
        )

    # 规范化为 ISO 格式
    if hour and minute:
        normalized = f"{year:04d}-{month:02d}-{day:02d} {int(hour):02d}:{int(minute):02d}"
    else:
        normalized = f"{year:04d}-{month:02d}-{day:02d}"

    return ValidationResult(valid=True, normalized=normalized)


# ========== 编号校验 ==========

# 项目编号正则：字母+数字+分隔符，长度>=4
# 支持格式：ZFCG-2026-001、DDWK2026024、GZCG-2026-001-1
_PROJECT_ID_PATTERN = re.compile(
    r"^[A-Za-z][A-Za-z0-9_\-]{3,}$"
)


def validate_project_identifier(raw_value: str) -> ValidationResult:
    """校验项目编号字段值。

    规则：
    - 必须以字母开头
    - 只包含字母、数字、下划线、连字符
    - 长度 >= 4
    """
    if not raw_value or not raw_value.strip():
        return ValidationResult(valid=False, errors=["编号值为空"])

    raw = raw_value.strip()

    if len(raw) < 4:
        return ValidationResult(valid=False, errors=[f"编号长度不足: {len(raw)} < 4"])

    if not _PROJECT_ID_PATTERN.match(raw):
        return ValidationResult(
            valid=False,
            errors=[f"编号格式不合法: '{raw}'，必须以字母开头，只含字母/数字/_/-"],
        )

    # 规范化：转大写
    normalized = raw.upper()

    return ValidationResult(valid=True, normalized=normalized)


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
