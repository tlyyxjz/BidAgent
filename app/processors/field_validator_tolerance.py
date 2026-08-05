"""金额容差计算工具（v4.1 §7.3 合规实现）。

从 field_validator.py 拆分而来，包含显示精度解析和容差计算的辅助函数。
"""
from __future__ import annotations

import re

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
