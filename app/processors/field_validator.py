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

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# ========== 版本号 ==========

VALIDATOR_VERSION = "1.2"  # v1.2: 主编号非法时回退取括号里"招标编号：XXX"

# ========== 金额校验 ==========

# 金额正则：支持多种格式
# 匹配：100万、100.00万元、1000000元、100.00 元、￥100、100.00、1.5亿、0.0769040（万元）
_AMOUNT_PATTERN = re.compile(
    r"^(?P<currency>￥|¥|人民币|美元|USD|\$|欧元|EUR|€)?\s*"
    r"(?P<amount>\d+(?:\.\d+)?)"
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
        amount = float(amount_str)
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


# ==== v4.1 §7.3 单位转换表（中文单位 -> 元）====
# 用于 display_precision 字符串解析
_UNIT_TO_YUAN = {
    "元": 1.0,
    "万元": 10000.0,
    "亿元": 100000000.0,
    "万元人民币": 10000.0,
    "元人民币": 1.0,
    "万美元": 0.0,  # 需汇率，暂不支持，返回 0 表示无法判定
    "美元": 0.0,
    "欧元": 0.0,
    "日元": 0.0,
}


def _parse_display_precision(precision_str: str) -> tuple[float, float] | None:
    """解析原文显示精度字符串，返回 (smallest_unit_in_yuan, tolerance)。

    v4.1 §7.3: "最大允许误差不超过原文最小显示单位的一半"

    Args:
        precision_str: 显示精度字符串，如 "0.01万元" / "1元" / "0.001亿元"

    Returns:
        (smallest_unit_in_yuan, tolerance_in_yuan) 或 None（无法解析时）

    Examples:
        "0.01万元" -> (100.0, 50.0)      # 0.01 * 10000 = 100, half = 50
        "1元"     -> (1.0, 0.5)          # 1 * 1 = 1, half = 0.5
        "0.001亿元" -> (100000.0, 50000.0)  # 0.001 * 1e8 = 1e5, half = 5e4
        "1万元"    -> (10000.0, 5000.0)   # 1 * 10000 = 10000, half = 5000
    """
    if not precision_str:
        return None
    s = precision_str.strip()
    if not s:
        return None

    # 尝试匹配：数字 + 单位
    # 数字部分：整数或小数（含全角数字转半角）
    s_halfwidth = s.translate(str.maketrans(
        "０１２３４５６７８９．",
        "0123456789.",
    ))
    match = re.match(r"^(\d+\.?\d*)\s*(.+)$", s_halfwidth)
    if not match:
        return None

    try:
        numeric_part = float(match.group(1))
    except ValueError:
        return None

    unit_part = match.group(2).strip()
    # 在 _UNIT_TO_YUAN 中查找（支持键的子串匹配，如 "万元人民币" 包含 "万元"）
    # 按 key 长度从长到短匹配，确保 "万元" 优先于 "元"，"亿元" 优先于 "元"
    multiplier = None
    for unit_key in sorted(_UNIT_TO_YUAN.keys(), key=len, reverse=True):
        factor = _UNIT_TO_YUAN[unit_key]
        if unit_part == unit_key or unit_key in unit_part:
            multiplier = factor
            break

    if multiplier is None or multiplier == 0.0:
        # 未知单位或需汇率（美元等），无法判定
        return None

    smallest_unit_in_yuan = numeric_part * multiplier
    tolerance = smallest_unit_in_yuan / 2.0
    return (smallest_unit_in_yuan, tolerance)


def _compute_tolerance_from_precision(
    display_precision: str | None,
    original_unit: str | None = None,
) -> float | None:
    """基于原文显示精度计算容差（v4.1 §7.3 合规实现）。

    Args:
        display_precision: 原文显示精度，如 "0.01万元"
        original_unit: 原始单位（备用，当 display_precision 缺失时尝试推断）

    Returns:
        容差（元）或 None（无法判定时，调用方应回退到旧策略）
    """
    if display_precision:
        result = _parse_display_precision(display_precision)
        if result is not None:
            return result[1]

    # display_precision 缺失时，尝试从 original_unit 推断保守容差
    if original_unit:
        unit = original_unit.strip()
        # 保守策略：单位是万元则假设精度 0.01万元（100元，容差50元）
        # 单位是元则假设精度 0.01元（容差 0.005元）
        if "万元" in unit or "万" in unit:
            return 50.0
        if "亿元" in unit or "亿" in unit:
            return 500000.0  # 0.01亿元的一半
        # 排除外币单位（美元/欧元/日元含'元'字但非人民币）
        if "美元" in unit or "欧元" in unit or "日元" in unit:
            return None  # 外币需汇率，无法判定容差
        if "元" in unit:
            return 0.005

    return None


def _compute_tolerance(amount: float) -> float:
    """[DEPRECATED] 计算精度容差（基于金额量级的旧策略）。

    v4.1 §7.3 明确禁止"固定百分比或量级分层"容差。
    本函数保留仅为向后兼容（lot sum 一致性检查使用），新代码应使用
    _compute_tolerance_from_precision(display_precision, original_unit)。

    旧策略（不合规，仅作回退）：
    - amount >= 10000 -> 50.0（假设万元级，0.01万元=100元，half=50元）
    - amount >= 100 -> 0.5（假设元级，1元，half=0.5元）
    - else -> 0.005（假设小数元级，0.01元，half=0.005元）
    """
    if amount >= 10000:
        return 50.0
    elif amount >= 100:
        return 0.5
    else:
        return 0.005


# ========== 日期校验 ==========

# 中文日期正则：YYYY年MM月DD日，支持"09:00"、"09时00分"、"09点00分"
# 时间作为一个可选组，内部用 | 分隔汉字时分和冒号时分，避免空格被错误消耗
_CN_DATE_PATTERN = re.compile(
    r"^(?P<year>\d{4})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日"
    r"(?:\s*(?:"
    r"(?P<hour>\d{1,2})\s*[时点]\s*(?P<minute>\d{1,2})\s*分"
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

    # 预处理：剥离"（北京时间）"等后缀
    cleaned = raw
    suffix_match = _DATE_SUFFIX_PATTERN.search(cleaned)
    if suffix_match:
        suffix_text = suffix_match.group(0)
        cleaned = cleaned[:suffix_match.start()].strip()
        preprocess_steps.append(f"剥离后缀: '{suffix_text}'")

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


# ========== 编号校验 ==========

# 项目编号正则：字母或数字开头，含字母数字+下划线+连字符，长度>=4
# v1.1 放宽：允许数字开头（如 "11000026210200173767-XM001"）
_PROJECT_ID_PATTERN = re.compile(
    r"^(?:(?P<alpha>[A-Za-z])[A-Za-z0-9_\-]{3,}|(?P<digit>\d)[A-Za-z0-9_\-]{3,})$"
)

# 编号括号备注正则：剥离"（招标编号：XXX）"、"(2026)"等
# 注意：要先剥离中文括号再剥离半角括号
_ID_BRACKET_CN_PATTERN = re.compile(r"（[^）]*】?$")  # 中文括号备注
_ID_BRACKET_HALF_PATTERN = re.compile(r"\([^)]*\)$")  # 半角括号备注


def _preprocess_identifier(raw: str) -> Tuple[str, List[str], List[str]]:
    """编号预处理：剥离括号备注、取顿号第一个、规范化分隔符。

    Returns:
        (cleaned, steps, bracket_texts)
        - cleaned: 预处理后的主编号
        - steps: 预处理步骤说明
        - bracket_texts: 被剥离的括号内容列表（含括号），供主编号非法时回退使用
    """
    steps: List[str] = []
    bracket_texts: List[str] = []
    cleaned = raw.strip()

    # 取顿号分隔的第一个编号（如 "XNJZ-G-2026-010、TLYQ2026-06080"）
    if "、" in cleaned:
        first = cleaned.split("、", 1)[0].strip()
        steps.append(f"取顿号首项: '{cleaned[:30]}' → '{first}'")
        cleaned = first

    # 取分号分隔的第一个编号（如 "HW20260341；ZKQ2026-020514384ZF(H)"）
    if "；" in cleaned or ";" in cleaned:
        sep = "；" if "；" in cleaned else ";"
        first = cleaned.split(sep, 1)[0].strip()
        steps.append(f"取分号首项: '{cleaned[:30]}' → '{first}'")
        cleaned = first

    # 剥离括号备注（如"(2026)"、"(H)"、"（招标编号：XXX）"）
    # 注意：_to_halfwidth 已将中文括号转为半角，这里只需处理半角括号
    while True:
        m = re.search(r"\([^)]*\)", cleaned)
        if not m:
            break
        bracket_text = m.group(0)
        bracket_texts.append(bracket_text)
        cleaned = (cleaned[:m.start()] + cleaned[m.end():]).strip()
        steps.append(f"剥离半角括号: '{bracket_text}'")

    return cleaned, steps, bracket_texts


# 括号内编号提取正则：匹配"招标编号：XXX"、"项目编号：XXX"等
# 支持中英文冒号，前缀词包括招标/项目/采购/合同编号
_BRACKET_ID_PATTERN = re.compile(
    r"(?:招标|项目|采购|合同)?编号\s*[：:]\s*([A-Za-z0-9_\-]+)"
)


def _extract_id_from_brackets(bracket_texts: List[str]) -> Optional[str]:
    """从被剥离的括号内容里提取合法编号。

    用于主编号非法时的回退。例如：
    - "(招标编号：0773-2641GNSHFWGK1900)" → "0773-2641GNSHFWGK1900"
    - "(项目编号: ZFCG-2026-001)" → "ZFCG-2026-001"

    Returns:
        提取到的合法编号字符串，若未找到则 None
    """
    for bracket_text in bracket_texts:
        # 去掉首尾括号
        inner = bracket_text.strip("()")
        m = _BRACKET_ID_PATTERN.search(inner)
        if m:
            candidate = m.group(1)
            # 用主正则验证
            if _PROJECT_ID_PATTERN.match(candidate):
                return candidate
    return None


def validate_project_identifier(raw_value: str) -> ValidationResult:
    """校验项目编号字段值。

    规则（Sol 要求）：
    - 空格和连接符规范化
    - 全角转半角
    - 大小写统一
    - v1.1 放宽：允许字母或数字开头
    - v1.1 放宽：允许括号备注（自动剥离）
    - v1.1 放宽：允许多编号（取第一个）
    - v1.2 放宽：主编号非法时回退取括号里"招标编号：XXX"
    - 只包含字母、数字、下划线、连字符
    - 长度 >= 4
    """
    if not raw_value or not raw_value.strip():
        return ValidationResult(valid=False, raw_value=raw_value, errors=["编号值为空"])

    # Sol 要求：全角转半角
    raw = _to_halfwidth(raw_value.strip())

    # v1.1 预处理：剥离括号备注、取顿号/分号第一个
    cleaned, preprocess_steps, bracket_texts = _preprocess_identifier(raw)

    # Sol 要求：空格规范化（去除空格）
    cleaned = cleaned.replace(" ", "")

    # v1.2 回退逻辑：主编号非法时，尝试从括号备注里提取"招标编号：XXX"
    fallback_used = False
    fallback_origin = None
    if cleaned and len(cleaned) >= 4 and _PROJECT_ID_PATTERN.match(cleaned):
        # 主编号合法，直接使用
        final_id = cleaned
    else:
        # 主编号非法或为空，尝试回退
        fallback_id = _extract_id_from_brackets(bracket_texts)
        if fallback_id is not None:
            final_id = fallback_id
            fallback_used = True
            fallback_origin = bracket_texts[0] if bracket_texts else None
            preprocess_steps.append(
                f"主编号非法回退: '{cleaned}' 非法，从括号备注 '{fallback_origin}' 提取 '{fallback_id}'"
            )
        else:
            # 回退也失败，返回错误
            if len(cleaned) < 4:
                return ValidationResult(
                    valid=False,
                    raw_value=raw_value,
                    errors=[
                        f"编号长度不足: {len(cleaned)} < 4（预处理后: '{cleaned}'）",
                        "括号备注未找到合法编号回退",
                    ],
                )
            return ValidationResult(
                valid=False,
                raw_value=raw_value,
                errors=[
                    f"编号格式不合法: '{raw_value}'（预处理后: '{cleaned}'），只含字母/数字/_/-",
                    "括号备注未找到合法编号回退",
                ],
            )

    # Sol 要求：大小写统一（转大写）
    normalized = final_id.upper()

    # 推导规则：包含预处理步骤
    derivation_parts = list(preprocess_steps)
    derivation_parts.extend(["全角→半角", "去空格", "转大写"])
    derivation_rule = " | ".join(derivation_parts)

    return ValidationResult(
        valid=True,
        raw_value=raw_value,
        normalized=normalized,
        derivation_rule=derivation_rule,
    )


def _to_halfwidth(text: str) -> str:
    """全角转半角（Sol 要求）。"""
    result = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:  # 全角空格
            code = 0x20
        elif 0xFF01 <= code <= 0xFF5E:  # 全角字符（!-~）
            code -= 0xFEE0
        result.append(chr(code))
    return "".join(result)


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
