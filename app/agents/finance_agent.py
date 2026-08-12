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

from dataclasses import asdict, is_dataclass
from typing import Any

from app.utils.logger import get_logger

from app.processors.observation_signals import analyze_observation_signals
from app.processors.observation_types import (
    SIGNAL_AWARD_ACTIVITY,
    SIGNAL_AWARD_CONCENTRATION,
    SIGNAL_CANCELLATION_LINK,
    SIGNAL_EXPLICIT_REJECTION,
    SIGNAL_HIGH_FREQ_COOCCURRENCE,
    SIGNAL_INFO_CONFLICT,
)

logger = get_logger("agent.finance")

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


# 字段名别名映射：finance_agent 逻辑名 -> LLM 抽取器规范名 + ORM 名
# LLM 抽取器（extraction_schemas.py CORE_FIELD_NAMES）输出：
#   project_identifier / purchaser_name / winner_name / amount / publish_date / bid_deadline
# ORM Tender 对象顶层字段：win_company / tender_org / budget_amount / publish_time ...
_FIELD_ALIASES: dict[str, list[str]] = {
    "win_company": ["winner_name", "win_company", "中标人", "中标企业"],
    "tender_org": ["purchaser_name", "tender_org", "采购人", "采购单位"],
    "win_amount": ["amount", "win_amount", "中标金额", "合同金额"],
    "budget_amount": ["amount", "budget_amount", "预算金额"],
    "publish_time": ["publish_date", "publish_time", "发布日期"],
    "win_date": ["publish_date", "win_date", "中标日期", "成交日期"],
    "project_name": ["project_name", "title", "项目名称"],
    "location": ["location", "region", "地区"],
    "source_url": ["source_url", "notice_url", "url"],
    "source_platform": ["source_platform", "platform"],
}


def _extract_field_from_tender(t: Any, field_name: str) -> Any:
    """从 tender 对象提取字段值（兼容顶层字段和 fields 列表两种格式）。

    通过 _FIELD_ALIASES 同时匹配逻辑名 + LLM 规范名 + ORM 名，解决
    字段名不一致（LLM输出winner_name而代码查win_company）导致分组为空的问题。
    """
    aliases = _FIELD_ALIASES.get(field_name, [field_name])

    # 1. 先尝试顶层字段（ORM 对象或扁平 dict）
    for alias in aliases:
        val = _field(t, alias)
        if val:
            return val

    # 2. 尝试从 fields 列表提取（processor/quality agent 产出的格式）
    fields = _field(t, "fields") or []
    if isinstance(fields, list):
        for f in fields:
            if isinstance(f, dict) and f.get("field_name") in aliases:
                return f.get("raw_value") or f.get("value")
    return None


def _build_record(t: Any) -> dict:
    """从 tender 构造单条公开活动观察记录（字段名对齐 analyze_observation_signals 入参）。"""
    return {
        "win_date": str(
            _extract_field_from_tender(t, "publish_time")
            or _extract_field_from_tender(t, "win_date")
            or _field(t, "publish_time")
            or ""
        ),
        "win_amount": (
            _extract_field_from_tender(t, "win_amount")
            or _extract_field_from_tender(t, "budget_amount")
            or _field(t, "win_amount")
            or _field(t, "budget_amount")
        ),
        "notice_title": (
            _extract_field_from_tender(t, "project_name")
            or _field(t, "project_name")
            or _field(t, "title")
            or ""
        ),
        "purchaser": _extract_field_from_tender(t, "tender_org") or _field(t, "tender_org") or "",
        "region": _extract_field_from_tender(t, "location") or _field(t, "location") or "",
        "notice_url": _extract_field_from_tender(t, "source_url") or _field(t, "source_url") or "",
        "source_platform": _extract_field_from_tender(t, "source_platform") or _field(t, "source_platform") or "",
    }


def _group_win_records(tenders: list) -> dict[str, list[dict]]:
    """按中标企业分组构造中标观察记录（v4.1 §9.2 信号计算入参）。

    仅当存在 winner_name / win_company 字段时才计入；若全无中标人字段，
    调用方应改走 _group_purchaser_records（采购人视角）。
    """
    grouped: dict[str, list[dict]] = {}
    for t in tenders:
        company = _extract_field_from_tender(t, "win_company") or ""
        if not company:
            continue
        grouped.setdefault(company, []).append(_build_record(t))
    return grouped


def _group_purchaser_records(tenders: list) -> dict[str, list[dict]]:
    """按采购人分组构造采购活动观察记录（招标公告场景的 fallback）。

    招标公告无中标人字段，六维信号中的"中标活跃度/中标集中度"在此场景下
    语义为"采购活跃度/采购集中度"，由调用方通过 perspective 字段标注。
    """
    grouped: dict[str, list[dict]] = {}
    for t in tenders:
        purchaser = _extract_field_from_tender(t, "tender_org") or _field(t, "tender_org") or ""
        if not purchaser:
            continue
        grouped.setdefault(purchaser, []).append(_build_record(t))
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
    # P1 修复：区分"quality_agent未运行"(None)和"quality_tenders为空"([])
    # 未运行时回退到processed_tenders；已运行时只用验证后的数据(即使为空)
    quality_tenders = state.get("quality_tenders")
    if quality_tenders is not None:
        tenders = quality_tenders
    else:
        tenders = state.get("processed_tenders") or []

    if not tenders:
        logger.warning("finance_agent: 无可用公告数据")
        state.setdefault("finance_summary", {"reason": "无可用公告数据"})
        # v4.1 契约：无论有无数据，state 必须含 observation_signals（空观察信号）
        state.setdefault("observation_signals", {})
        return state

    # P2: 读取采集平台列表，用于回填 coverage_platforms
    collect_summary = state.get("collect_summary") or {}

    try:
        # 优先按中标人分组（中标公告场景）
        grouped = _group_win_records(tenders)
        perspective = "winner"
        if not grouped:
            # 无中标人字段（招标公告场景）：fallback 到按采购人分组
            grouped = _group_purchaser_records(tenders)
            perspective = "purchaser"
            if grouped:
                logger.info(
                    "finance_agent: 无中标人字段，fallback到采购人视角分组 orgs={}",
                    len(grouped),
                )

        if not grouped:
            # 中标人和采购人字段都缺失
            logger.warning("finance_agent: 公告中无中标企业/采购人字段，跳过信号计算")
            state.setdefault("finance_summary", {
                "reason": "公告中无中标企业/采购人字段，无法生成观察信号",
            })
            state.setdefault("observation_signals", {})
            return state

        signals_by_org: dict[str, dict] = {}
        for company, records in grouped.items():
            # P1 修复：用hashlib替代内置hash()，确保跨进程确定性
            import hashlib
            org_hash = hashlib.md5(company.encode("utf-8")).hexdigest()[:12]
            result = analyze_observation_signals(
                org_id=f"org_{org_hash}",
                org_name=company,
                win_records=records,
            )
            signals_by_org[company] = _result_to_dict(result)

        # P2 修复：processed_tenders 缺 source_platform 字段，导致
        # observation_signals 内部算出的 coverage_platforms 恒为空。
        # 从 collect_summary 读取采集平台列表，后处理回填每个组织。
        # 兼容两种结构：platforms_collected (旧) 或 per_platform (新, list[dict])
        _platforms = collect_summary.get("platforms_collected") or []
        if not _platforms:
            for _pp in (collect_summary.get("per_platform") or []):
                _name = _pp.get("platform") if isinstance(_pp, dict) else None
                if _name and _name not in _platforms:
                    _platforms.append(_name)
        for _org_name, _org_res in signals_by_org.items():
            if not _org_res.get("coverage_platforms") and _platforms:
                _org_res["coverage_platforms"] = list(_platforms)

        # 全量按组织保存（供 API / 前端画像）
        state["observation_signals"] = signals_by_org

        # 报告章节用记录最多的组织，压平为英文键（兼容 docx_sections）
        top_org = max(grouped, key=lambda k: len(grouped[k]))
        state["finance_summary"] = {
            "observation_signals": _flat_signals_for_report(signals_by_org[top_org]),
            "by_organization": signals_by_org,
            "primary_organization": top_org,
            # 新增：观察视角标注（winner=中标人视角 / purchaser=采购人视角）
            "perspective": perspective,
        }
        logger.info(
            "finance_agent: 6 维观察信号生成完成 orgs={} perspective={}",
            len(signals_by_org), perspective,
        )
    except Exception:
        logger.exception("finance_agent: 观察信号生成失败")
        state.setdefault("finance_summary", {"reason": "观察信号生成异常"})
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
