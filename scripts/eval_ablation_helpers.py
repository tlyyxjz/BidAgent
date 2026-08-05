"""W2-08/W4 消融实验指标计算辅助函数。"""
from __future__ import annotations

import re
from typing import Optional

from app.eval.set_metrics import (
    compute_multi_value_field_metrics,
    is_multi_value_field,
)
from scripts.eval_ablation_types import GoldField


def _status_matches(gold: str, pred: str) -> bool:
    """字段状态匹配判定。"""
    if gold == pred:
        return True
    # absent vs present/ambiguous 都算不匹配
    # multi_value 视为 present 的特殊形式
    if gold == "multi_value" and pred == "present":
        return True
    return False


def _extract_amount_unit(raw: str) -> str | None:
    """从金额字符串中提取单位 (元/万元/亿元)。"""
    if not raw:
        return None
    if "亿元" in raw or "亿" in raw:
        return "亿元"
    if "万元" in raw or "万" in raw:
        return "万元"
    if "元" in raw:
        return "元"
    return None


def _amount_correct(pred_raw: str, gold_raw: str) -> bool:
    """v4.1 sec 7.3 金额正确性判定 (基于显示精度的容差策略)。

    1. 用 validate_amount 把 pred 和 gold 都转换为元
    2. 用 _compute_tolerance_from_precision 计算容差 (基于 gold 单位推断)
    3. |pred_yuan - gold_yuan| <= tolerance -> 正确

    回退: 解析失败时退化为数字字符串精确匹配。
    """
    from app.processors.field_validator import (
        validate_amount,
        _compute_tolerance_from_precision,
    )

    pred_vr = validate_amount(pred_raw)
    gold_vr = validate_amount(gold_raw)
    if not pred_vr.valid or not gold_vr.valid:
        # 回退: 数字字符串精确匹配
        pred_num = re.findall(r"\d+\.?\d*", pred_raw)
        gold_num = re.findall(r"\d+\.?\d*", gold_raw)
        return bool(pred_num and gold_num and pred_num[0] == gold_num[0])

    pred_yuan = pred_vr.normalized_value or 0.0
    gold_yuan = gold_vr.normalized_value or 0.0

    # 容差: 优先用 gold 的 display_precision (金标通常无此字段, 这里为 None),
    # 退化到 original_unit 推断 (如 "万元" -> 50 元容差)
    gold_unit = _extract_amount_unit(gold_raw)
    pred_unit = _extract_amount_unit(pred_raw)
    # 容差取金标与预测单位容差的最大值, 解决单位换算四舍五入导致的误判
    tolerance = max(
        _compute_tolerance_from_precision(None, gold_unit) or 0.0,
        _compute_tolerance_from_precision(None, pred_unit) or 0.0,
    )

    diff = abs(pred_yuan - gold_yuan)
    return diff <= tolerance


def _classify_gold_status(gold_status: str) -> str:
    """将金标状态分类为评测口径。

    v4.1 §10.3 6 种金标状态分为 4 类评测口径：
    - "should_have_value": present/ambiguous/multi_value → 系统应输出值
    - "should_not_have_value": absent/not_applicable → 系统不应输出值
    - "attachment_only": 字段在附件中，正文抽取算无依据但不算错误
    - "unreadable": 无法判定，correct=None

    Args:
        gold_status: 金标状态（6 种之一）

    Returns:
        评测口径类别
    """
    if gold_status in ("present", "ambiguous", "multi_value"):
        return "should_have_value"
    if gold_status in ("absent", "not_applicable"):
        return "should_not_have_value"
    if gold_status == "attachment_only":
        return "attachment_only"
    if gold_status == "unreadable":
        return "unreadable"
    # 默认按 should_have_value 处理（向后兼容）
    return "should_have_value"


def _date_correct(pred: str, gold_v: str) -> bool:
    """日期字段归一化比较：同一日期的不同写法应视为正确。

    处理 W3 评测中出现的两类误判：
    - 时分写法差异：\"2026年08月20日 09时00分\" vs \"2026年08月20日 09:00\"
    - 是否携带时间：\"2026-07-29 08:45\" vs \"2026年07月29日\"
    同时支持剥离常见前缀（\"开标时间：\" 等）与括号备注（\"（北京时间）\"）。

    判定规则：
    - 日期部分（YYYY-MM-DD）不一致 → 错误
    - 双方都带时间 → 比较到分钟；一方带时间一方不带 → 日期一致即正确
    """
    from app.processors.field_validator import validate_date

    def _norm(s: str):
        if not s:
            return None
        s = s.strip()
        # 剥离常见前缀（"开标时间："、"投标截止时间："、"更正日期："、"发布时间："等）
        s = re.sub(
            r"^(开标时间|投标截止时间|投标文件递交截止时间|提交投标文件截止时间|截止时间|"
            r"响应文件递交截止时间|响应文件提交截止时间|递交截止时间|询价截止时间|更正日期|"
            r"发布日期|公告时间|发布时间|采购公告时间|原公告时间)[：:：\s]*",
            "",
            s,
        )
        vr = validate_date(s)
        if vr.valid and vr.normalized:
            return vr.normalized  # 已规范为 "YYYY-MM-DD[ HH:MM]"
        # ISO 带秒回退: "2026-08-14 09:30:00" / "2026-08-14T09:30:00" -> "2026-08-14 09:30"
        m = re.search(r"(\d{4}-\d{2}-\d{2})[ T](\d{1,2}):(\d{2})", s)
        if m:
            return f"{m.group(1)} {m.group(2).zfill(2)}:{m.group(3)}"
        return None

    pn = _norm(pred)
    gn = _norm(gold_v)
    if pn is None or gn is None:
        return False
    # 日期部分（前 10 字符 "YYYY-MM-DD"）必须一致
    if pn[:10] != gn[:10]:
        return False

    # 00:00 视为"无具体时间"（标注默认值）：剥离时间部分
    def _strip_midnight(s: str) -> str:
        # s 形如 "YYYY-MM-DD" 或 "YYYY-MM-DD HH:MM"
        if len(s) > 10 and s[11:16] == "00:00":
            return s[:10]
        return s

    pn2 = _strip_midnight(pn)
    gn2 = _strip_midnight(gn)

    # 时间部分判定
    p_has_time = len(pn2) > 10
    g_has_time = len(gn2) > 10
    if p_has_time and g_has_time:
        return pn2[:16] == gn2[:16]  # 比较到分钟
    return True  # 一方带时间一方不带：日期一致即正确


def _value_correct(field_name: str, pred_value: str, gold_field: GoldField) -> bool:
    """判断字段值是否与金标一致。

    - amount 字段: v4.1 sec 7.3 容差策略 (单位转换 + 显示精度容差)
    - 日期字段 (publish_date/bid_deadline): 归一化比较 (同一日期不同写法算正确)
    - 其他字段: 子串匹配 (容错：LLM 可能多带前后缀)
    """
    if not pred_value or not gold_field.values:
        return False
    pred = pred_value.strip()
    for v in gold_field.values:
        gold_v = (v.get("raw_value") or "").strip()
        if not gold_v:
            continue
        # amount 字段: 走 v4.1 sec 7.3 容差比较
        if field_name == "amount":
            if _amount_correct(pred, gold_v):
                return True
            continue
        # 日期字段: 归一化比较
        if field_name in ("publish_date", "bid_deadline"):
            if _date_correct(pred, gold_v):
                return True
            continue
        # 其他字段: 子串匹配 (容错：LLM 可能多带前后缀)
        if gold_v in pred or pred in gold_v:
            return True
    return False


# ========== v4.1 sec 7.4 多值字段集合级评测辅助函数 ==========

def _collect_pred_values_by_name(fields: list) -> dict[str, list[str]]:
    """按 field_name 收集所有预测值（多值字段不被压平）。"""
    result: dict[str, list[str]] = {}
    for f in fields:
        if f.raw_value:
            result.setdefault(f.field_name, []).append(f.raw_value)
    return result


def _collect_gold_values(gold_field: GoldField) -> list[str]:
    """从 gold_field.values 收集所有金标值。"""
    values = []
    for v in gold_field.values:
        raw = v.get("raw_value")
        if raw:
            values.append(raw)
    return values


def _compute_mv_f1(field_name: str, pred_values: list[str], gold_field: GoldField) -> Optional[float]:
    """对多值字段计算集合级 F1，非多值字段返回 None。"""
    if not is_multi_value_field(field_name):
        return None
    gold_values = _collect_gold_values(gold_field)
    metrics = compute_multi_value_field_metrics(pred_values, gold_values)
    return round(metrics.f1, 4)
