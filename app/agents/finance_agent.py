"""金融分析 Agent（v4.1 合规版）。

v4.1 边界：
- 只输出公开招投标活动观察信号
- 不输出信用评分
- 不实施 BOQ 异常检测
- 不实施废标风险预警

P0-2 修复（2026-08-06）：
- 原实现以 analyze_observation_signals(tenders) 调用，与其真实签名
  (org_id, org_name, win_records, ...) 不符，有数据时必抛 TypeError
  被静默吞掉，导致六 Agent 跑完金融信号恒为空。
- 现按中标企业分组构造 win_records，逐组织计算六维信号。
"""
from __future__ import annotations

import logging
from dataclasses import asdict, is_dataclass
from typing import Any

from app.processors.observation_signals import analyze_observation_signals
from app.processors.observation_types import (
    SIGNAL_AWARD_ACTIVITY,
    SIGNAL_AWARD_CONCENTRATION,
    SIGNAL_CANCELLATION_LINK,
    SIGNAL_EXPLICIT_REJECTION,
    SIGNAL_HIGH_FREQ_COOCCURRENCE,
    SIGNAL_INFO_CONFLICT,
)

logger = logging.getLogger("finance_agent")

# 中文信号名 → docx 报告章节使用的英文键（对齐 report/docx_sections.py）
_SIGNAL_KEY_MAP = {
    SIGNAL_AWARD_ACTIVITY: "award_activity",
    SIGNAL_AWARD_CONCENTRATION: "award_concentration",
    SIGNAL_CANCELLATION_LINK: "cancellation_link",
    SIGNAL_EXPLICIT_REJECTION: "explicit_rejection",
    SIGNAL_INFO_CONFLICT: "info_conflict",
    SIGNAL_HIGH_FREQ_COOCCURRENCE: "high_freq_cooccurrence",
}


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """兼容 ORM 对象与 dict 的字段读取。"""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _group_win_records(tenders: list) -> dict[str, list[dict]]:
    """按中标企业分组构造中标观察记录（v4.1 §9.2 信号计算入参）。"""
    grouped: dict[str, list[dict]] = {}
    for t in tenders:
        company = _field(t, "win_company") or ""
        if not company:
            continue
        record = {
            "win_date": str(_field(t, "publish_time") or _field(t, "win_date") or ""),
            "win_amount": _field(t, "win_amount") or _field(t, "budget_amount"),
            "notice_title": _field(t, "project_name") or _field(t, "title") or "",
            "purchaser": _field(t, "tender_org") or "",
            "region": _field(t, "location") or "",
            "notice_url": _field(t, "source_url") or "",
            "source_platform": _field(t, "source_platform") or "",
        }
        grouped.setdefault(company, []).append(record)
    return grouped


def _result_to_dict(result: Any) -> dict:
    """ObservationResult → 纯 dict（跳过 profile，避免嵌套序列化问题）。"""
    signals = []
    for sig in result.signals:
        signals.append(asdict(sig) if is_dataclass(sig) else dict(sig))
    return {
        "organization_id": result.organization_id,
        "normalized_name": result.normalized_name,
        "coverage_platforms": result.coverage_platforms,
        "coverage_time_range": result.coverage_time_range,
        "valid_notice_count": result.valid_notice_count,
        "entity_resolution_status": result.entity_resolution_status,
        "signal_caliber": result.signal_caliber,
        "signals": signals,
        "summary": getattr(result, "summary", "") or "",
    }


def _flat_signals_for_report(org_result: dict) -> dict:
    """把单组织信号压平为 docx 章节期望的英文键字典。"""
    flat: dict[str, Any] = {}
    for sig in org_result.get("signals", []):
        key = _SIGNAL_KEY_MAP.get(sig.get("signal_name", ""), sig.get("signal_name", ""))
        entry = {
            "observed_value": sig.get("observed_value"),
            "observation_period": sig.get("observation_period", ""),
            "coverage_note": sig.get("coverage_note", ""),
            "disclaimer": sig.get("disclaimer", ""),
        }
        details = sig.get("details") or {}
        if isinstance(details, dict):
            entry.update(details)
        flat[key] = entry
    return flat


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """金融分析 Agent 主入口：生成 6 维公开活动观察信号。

    严格不输出信用评分，不调用 BOQ/废标引擎。
    """
    tenders = state.get("quality_tenders") or state.get("processed_tenders") or []

    if not tenders:
        logger.warning("finance_agent: 无可用公告数据")
        state.setdefault("finance_summary", {})
        # v4.1 契约：无论有无数据，state 必须含 observation_signals（空观察信号）
        state.setdefault("observation_signals", {})
        return state

    try:
        grouped = _group_win_records(tenders)
        if not grouped:
            logger.warning("finance_agent: 公告中无中标企业字段，跳过信号计算")
            state.setdefault("finance_summary", {})
            state.setdefault("observation_signals", {})
            return state

        signals_by_org: dict[str, dict] = {}
        for company, records in grouped.items():
            result = analyze_observation_signals(
                org_id=f"org_{abs(hash(company)) % 10**12:012d}",
                org_name=company,
                win_records=records,
            )
            signals_by_org[company] = _result_to_dict(result)

        # 全量按组织保存（供 API / 前端画像）
        state["observation_signals"] = signals_by_org

        # 报告章节用记录最多的组织，压平为英文键（兼容 docx_sections）
        top_org = max(grouped, key=lambda k: len(grouped[k]))
        state["finance_summary"] = {
            "observation_signals": _flat_signals_for_report(signals_by_org[top_org]),
            "by_organization": signals_by_org,
            "primary_organization": top_org,
        }
        logger.info("finance_agent: 6 维观察信号生成完成 orgs={}", len(signals_by_org))
    except Exception:
        logger.exception("finance_agent: 观察信号生成失败")
        state.setdefault("finance_summary", {})
        state.setdefault("observation_signals", {})

    # AgentGraph 约定：Agent 返回完整 state（含 _agent_history 与前序输出）
    return state


async def main(state: dict[str, Any]) -> dict[str, Any]:
    """兼容旧入口，等价于 run。"""
    return await run(state)


class _FinanceAgentCompat:
    """兼容 pipeline.py 中 finance_agent = FinanceAgent() 的调用。"""
    async def run(self, state):
        return await run(state)

    async def __call__(self, state):
        # coordinator.AgentGraph 以 await func(state) 方式驱动各 Agent
        return await run(state)


finance_agent = _FinanceAgentCompat()
