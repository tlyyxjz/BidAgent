"""日期校验逻辑。

从 field_validator.py 拆分而来，包含日期相关的正则模式和校验函数。
"""
from __future__ import annotations

import re

from app.processors.field_validator import ValidationResult

# 中文日期正则：YYYY年MM月DD日，支持"09:00"、"09时00分"、"09点00分"
# 时间作为一个可选组，内部用 | 分隔汉字时分和冒号时分，避免空格被错误消耗
_CN_DATE_PATTERN = re.compile(
    r"^(?P<year>\d{4})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日"
    r"(?:\s*(?:"
    r"(?P<hour>\d{1,2})\s*[时点]\s*(?P<minute>\d{1,2})\s*分(?:\s*(?P<second>\d{1,2})\s*秒)?"
    r"|"
    r"(?P<hour2>\d{1,2}):(?P<minute2>\d{1,2})"
    r"))?"
    r"\s*$"
)

# ISO 日期正则
_ISO_DATE_PATTERN = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})"
    r"(?:\s+(?P<hour>\d{1,2}):(?P<minute>\d{1,2}))?"
    r"\s*$"
)

# 点号日期正则（Sol 要求）：YYYY.MM.DD 或 YYYY.MM.DD HH:MM
_DOT_DATE_PATTERN = re.compile(
    r"^(?P<year>\d{4})\.(?P<month>\d{1,2})\.(?P<day>\d{1,2})"
    r"(?:\s+(?P<hour>\d{1,2}):(?P<minute>\d{1,2}))?"
    r"\s*$"
)

# 日期后缀正则：剥离"（北京时间）"、"（UTC+8）"等
_DATE_SUFFIX_PATTERN = re.compile(
    r"（[^）]*）\s*$"
)


def validate_date(raw_value: str) -> ValidationResult:
    """校验日期字段值。

    支持格式（Sol 要求：支持点号、年月日汉字等格式）：
    - 中文：2026年8月1日、2026年08月01日 09:00、2026年8月6日09时00分、2026年08月06日 09点00分
    - ISO：2026-08-01、2026-08-01 09:00
    - 点号：2026.08.01、2026.8.1 09:00
    - 自动剥离后缀：2026年08月11日 11:00（北京时间）
    """
    if not raw_value or not raw_value.strip():
        return ValidationResult(valid=False, raw_value=raw_value, errors=["日期值为空"])

    raw = raw_value.strip()
    preprocess_steps = []

    # 预处理：剥离括号备注（中文/半角），如"2026年8月10日（原8月1日）"、"（北京时间）"。
    # 日期中的括号仅为备注，主日期在括号之外，剥离后不影响匹配。
    cleaned = raw
    while True:
        m = re.search(r"[（(][^（）()]*[）)]", cleaned)
        if not m:
            break
        tag = m.group(0)
        cleaned = (cleaned[:m.start()] + cleaned[m.end():]).strip()
        preprocess_steps.append(f"剥离括号备注: '{tag}'")

    # 尝试中文日期
    match = _CN_DATE_PATTERN.match(cleaned)
    pattern_type = "中文"
    if not match:
        # 尝试 ISO 日期
        match = _ISO_DATE_PATTERN.match(cleaned)
        pattern_type = "ISO"
        if not match:
            # 尝试点号日期（Sol 要求）
            match = _DOT_DATE_PATTERN.match(cleaned)
            pattern_type = "点号"
            if not match:
                return ValidationResult(
                    valid=False,
                    raw_value=raw_value,
                    errors=[f"日期格式不合法: '{raw}'（预处理后: '{cleaned}'），支持: 2026年8月1日 / 2026-08-01 / 2026.08.01"],
                )

    # 提取时分（兼容汉字时分和冒号时分，不同模式可能没有 hour2/minute2 组）
    gd = match.groupdict()
    hour = gd.get("hour") or gd.get("hour2")
    minute = gd.get("minute") or gd.get("minute2")

    year = int(match.group("year"))
    month = int(match.group("month"))
    day = int(match.group("day"))

    # 校验日期合法性
    if not (1 <= month <= 12):
        return ValidationResult(valid=False, raw_value=raw_value, errors=[f"月份不合法: {month}"])

    if not (1 <= day <= 31):
        return ValidationResult(valid=False, raw_value=raw_value, errors=[f"日期不合法: {day}"])

    # 简单的月份天数校验（不考虑闰年）
    days_in_month = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if day > days_in_month[month - 1]:
        return ValidationResult(
            valid=False,
            raw_value=raw_value,
            errors=[f"{month}月没有{day}日"],
        )

    # 规范化为 ISO 格式
    if hour and minute:
        normalized = f"{year:04d}-{month:02d}-{day:02d} {int(hour):02d}:{int(minute):02d}"
    else:
        normalized = f"{year:04d}-{month:02d}-{day:02d}"

    # 推导规则：包含预处理步骤
    derivation_parts = list(preprocess_steps)
    derivation_parts.append(f"{pattern_type}格式 → ISO格式")
    derivation_rule = " | ".join(derivation_parts)

    return ValidationResult(
        valid=True,
        raw_value=raw_value,
        normalized=normalized,
        derivation_rule=derivation_rule,
    )
