"""Agent 5: 金融分析 Agent（核心卖点）。

职责：BOQ 报价异常检测 + 废标风险预警 + 供应商公开活动观察度。

核心能力（三子模块）：
1. BOQ 报价异常检测（吸收完整版 boq_engine.py 并修复 bug）
   - 实验性能力：本模块为实验性功能，非 MVP 核心范围，结果仅供研究参考。
   - 32 类常见采购品类基准价格库（覆盖工程/服务/货物三大类）
   - 正则提取"数量+单位+品名"和"品名+数量+单位"两种模式
   - 按市场均价 ±std 判定 underpriced/overpriced/normal

2. 废标风险预警（吸收完整版 risk_engine.py 并修复 bug）
   - 18 条规则覆盖排他性资质、付款风险、交货期、资质门槛
   - 输出 RiskReport 含 score + risk_items + qualification_gaps

3. 供应商公开活动观察度（对齐 v4.1 第九章 observation_signals.py）
   - 6 信号：中标活跃度 + 公开中标集中度 + 废标公告关联 + 明确投标否决 + 信息冲突观察 + 高频共现提示
   - 严格不输出信用评分（v4.1 §9.1）

复用：app/processors/boq_engine.py + risk_engine.py + observation_signals.py
      （由 Sol S-1/S-2/S-3 交付后接入）

注意：本文件是骨架，等 Sol 交付三个模块后填充实现。
"""

from __future__ import annotations

from typing import Any

from app.utils.logger import get_logger

logger = get_logger("agent.finance")


async def finance_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Agent 5: 金融分析（BOQ + 废标 + 供应商公开活动观察度）。

    输入 state:
        - quality_summary: dict — 质检结果（来自 quality_agent）
        - subscription_id: int — 订阅 ID
        - collect_summary: dict — 采集结果

    输出 state（新增）:
        - finance_summary: dict — 金融分析结果
            - boq_anomalies: int — BOQ 异常数
            - risk_items: int — 废标风险数
            - supplier_scores: list[dict] — 供应商公开活动观察度列表
            - avg_supplier_score: float — 平均供应商评分
    """
    logger.info("finance_agent started")

    # 骨架实现：等 Sol S-1/S-2/S-3 交付后填充
    # 目前返回默认值，保证 pipeline 可运行
    state["finance_summary"] = await _run_finance_analysis(state)

    summary = state["finance_summary"]
    logger.info(
        "finance_agent completed boq_anomalies={} risk_items={} suppliers={}",
        summary.get("boq_anomalies", 0),
        summary.get("risk_items", 0),
        len(summary.get("supplier_scores", [])),
    )
    return state


async def _run_finance_analysis(state: dict[str, Any]) -> dict[str, Any]:
    """运行三子模块金融分析。

    优先调用 Sol 交付的模块；若未交付，返回默认值。
    """
    result: dict[str, Any] = {
        "boq_anomalies": 0,
        "risk_items": 0,
        "supplier_scores": [],
        "avg_supplier_score": 0.0,
    }

    # 子模块 1: BOQ 报价异常检测（实验性能力，非 MVP 核心范围，结果仅供研究参考）
    try:
        from app.processors.boq_engine import analyze_boq  # type: ignore[import-not-found]
        boq_result = await _run_boq_analysis(state)
        result["boq_anomalies"] = boq_result.get("anomalies", 0)
        result["boq_report"] = boq_result
    except ImportError:
        logger.warning("boq_engine not yet available (waiting for Sol S-1)")
    except Exception as exc:  # noqa: BLE001
        logger.exception("boq analysis failed: {}", exc)

    # 子模块 2: 废标风险预警
    try:
        from app.processors.risk_engine import analyze_risk  # type: ignore[import-not-found]
        risk_result = await _run_risk_analysis(state)
        result["risk_items"] = risk_result.get("risk_count", 0)
        result["risk_report"] = risk_result
    except ImportError:
        logger.warning("risk_engine not yet available (waiting for Sol S-2)")
    except Exception as exc:  # noqa: BLE001
        logger.exception("risk analysis failed: {}", exc)

    # 子模块 3: 组织实体公开活动观察信号（v4.1 第九章）
    try:
        from app.processors.observation_signals import analyze_observation_signals  # type: ignore[import-not-found]  # noqa: F401
        supplier_result = await _run_supplier_analysis(state)
        # 保留 supplier_scores 字段名以兼容下游 docx_generator 与现有测试，
        # 实际承载 observation_signals 返回的 signals 列表。
        result["supplier_scores"] = supplier_result.get("signals", [])
        result["supplier_data_completeness"] = supplier_result.get(
            "data_completeness", {}
        )
        result["supplier_profile"] = supplier_result.get("profile")
        values = [
            float(s.get("observed_value", 0) or 0)
            for s in result["supplier_scores"]
        ]
        result["avg_supplier_score"] = (
            sum(values) / len(values) if values else 0.0
        )
    except ImportError:
        logger.warning(
            "observation_signals not yet available (waiting for Sol S-3)"
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("observation signals analysis failed: {}", exc)

    return result


async def _run_boq_analysis(state: dict[str, Any]) -> dict[str, Any]:
    """BOQ 报价异常检测（S-1 已交付，接入 boq_engine.analyze_boq）。

    实验性能力：本模块为实验性功能，非 MVP 核心范围，结果仅供研究参考。
    """
    from app.processors.boq_engine import analyze_boq

    tenders = state.get("quality_tenders") or state.get("processed_tenders") or []
    if not tenders:
        return {"anomalies": 0, "items": [], "reports": []}

    reports: list[dict[str, Any]] = []
    total_anomalies = 0
    for tender in tenders:
        text = (
            f"{tender.get('project_name', '')} "
            f"{tender.get('content', '')} "
            f"{tender.get('budget_text', '')}"
        )
        report = await analyze_boq(
            text,
            tender.get("project_name", ""),
        )
        if report.get("suspicious_count", 0) > 0:
            total_anomalies += report["suspicious_count"]
        reports.append(report)

    return {
        "anomalies": total_anomalies,
        "items": [r for report in reports for r in report.get("items", [])],
        "reports": reports,
    }


async def _run_risk_analysis(state: dict[str, Any]) -> dict[str, Any]:
    """废标风险预警（S-2 已交付，接入 risk_engine.analyze_risk）。"""
    from app.processors.risk_engine import analyze_risk

    tenders = state.get("quality_tenders") or state.get("processed_tenders") or []
    if not tenders:
        return {"risk_count": 0, "items": [], "reports": []}

    reports: list[dict[str, Any]] = []
    total_risks = 0
    for tender in tenders:
        report = await analyze_risk(
            tender.get("project_name", ""),
            tender.get("content", ""),
            tender.get("qualification"),
            tender_id=tender.get("id"),
        )
        if report.get("risk_score", 0) > 0:
            total_risks += report.get("total_risk_items", len(report.get("risk_items", [])))
        reports.append(report)

    return {
        "risk_count": total_risks,
        "items": [r for report in reports for r in report.get("risk_items", [])],
        "reports": reports,
    }


async def _run_supplier_analysis(state: dict[str, Any]) -> dict[str, Any]:
    """组织实体公开活动观察信号（v4.1 第九章）。

    接入 app.processors.observation_signals.analyze_observation_signals，
    返回六个 MVP 信号（中标活跃度/公开中标集中度/废标公告关联/
    明确投标否决/信息冲突观察/高频共现提示）+ 数据完整性 + 供应商画像。
    """
    from app.processors.observation_signals import analyze_observation_signals

    # 从 state 中提取组织公开活动记录
    org_id = state.get("organization_id", "")
    org_name = state.get("normalized_name", "") or state.get("raw_name", "")
    win_records = state.get("win_records", [])
    cancellation_records = state.get("cancellation_records", [])
    rejection_records = state.get("rejection_records", [])
    conflict_records = state.get("conflict_records", [])
    cooccurrence_records = state.get("cooccurrence_records", [])

    if not win_records:
        return {
            "signals": [],
            "data_completeness": {},
            "profile": None,
            "summary": "无中标记录，跳过观察信号分析",
        }

    result = analyze_observation_signals(
        org_id,
        org_name,
        win_records,
        cancellation_records,
        rejection_records,
        conflict_records,
        cooccurrence_records,
    )
    return {
        "signals": [
            {
                "signal_name": s.signal_name,
                "observed_value": s.observed_value,
                "observation_period": s.observation_period,
                "coverage_note": s.coverage_note,
                "details": s.details,
                "disclaimer": s.disclaimer,
            }
            for s in result.signals
        ],
        "data_completeness": {
            "coverage_platforms": result.coverage_platforms,
            "coverage_time_range": result.coverage_time_range,
            "valid_notice_count": result.valid_notice_count,
            "entity_resolution_status": result.entity_resolution_status,
            "signal_caliber": result.signal_caliber,
        },
        "profile": {
            "win_count": result.profile.win_count,
            "total_win_amount": result.profile.total_win_amount,
            "main_purchasers": result.profile.main_purchasers,
            "main_agencies": result.profile.main_agencies,
            "active_regions": result.profile.active_regions,
            "first_win_date": result.profile.first_win_date,
            "last_win_date": result.profile.last_win_date,
        } if result.profile else None,
        "summary": result.summary,
    }
