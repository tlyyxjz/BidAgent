"""编号校验逻辑。

从 field_validator.py 拆分而来，包含项目编号相关的正则模式、辅助函数和校验函数。
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from app.processors.field_validator import ValidationResult

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
