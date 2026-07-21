"""Agent 5: 金融分析 Agent（核心卖点）。

职责：BOQ 报价异常检测 + 废标风险预警 + 供应商信用评分。

核心能力（三子模块）：
1. BOQ 报价异常检测（吸收完整版 boq_engine.py 并修复 bug）
   - 20 类常见采购品类基准价格库
   - 正则提取"数量+单位+品名"和"品名+数量+单位"两种模式
   - 按市场均价 ±std 判定 underpriced/overpriced/normal

2. 废标风险预警（吸收完整版 risk_engine.py 并修复 bug）
   - 18 条规则覆盖排他性资质、付款风险、交货期、资质门槛
   - 输出 RiskReport 含 score + risk_items + qualification_gaps

3. 供应商信用评分（重构 supplier_risk.py，去掉伪造联邦学习）
   - 三维度：投标活跃度 + 中标率 + 平均报价偏离度
   - 加权 30/40/30

复用：app/processors/boq_engine.py + risk_engine.py + supplier_risk.py
      （由 Sol S-1/S-2/S-3 交付后接入）

注意：本文件是骨架，等 Sol 交付三个模块后填充实现。
"""

from __future__ import annotations

from typing import Any

from app.utils.logger import get_logger

logger = get_logger("agent.finance")


async def finance_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Agent 5: 金融分析（BOQ + 废标 + 供应商信用评分）。

    输入 state:
        - quality_summary: dict — 质检结果（来自 quality_agent）
        - subscription_id: int — 订阅 ID
        - collect_summary: dict — 采集结果

    输出 state（新增）:
        - finance_summary: dict — 金融分析结果
            - boq_anomalies: int — BOQ 异常数
            - risk_items: int — 废标风险数
            - supplier_scores: list[dict] — 供应商信用评分列表
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

    # 子模块 1: BOQ 报价异常检测
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

    # 子模块 3: 供应商信用评分
    try:
        from app.processors.supplier_risk import analyze_supplier  # type: ignore[import-not-found]
        supplier_result = await _run_supplier_analysis(state)
        result["supplier_scores"] = supplier_result.get("scores", [])
        scores = [s.get("score", 0) for s in result["supplier_scores"]]
        result["avg_supplier_score"] = (
            sum(scores) / len(scores) if scores else 0.0
        )
    except ImportError:
        logger.warning("supplier_risk not yet available (waiting for Sol S-3)")
    except Exception as exc:  # noqa: BLE001
        logger.exception("supplier analysis failed: {}", exc)

    return result


async def _run_boq_analysis(state: dict[str, Any]) -> dict[str, Any]:
    """BOQ 报价异常检测（S-1 已交付，接入 boq_engine.analyze_boq）。"""
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
            total_risks += len(report.get("risk_items", []))
        reports.append(report)

    return {
        "risk_count": total_risks,
        "items": [r for report in reports for r in report.get("risk_items", [])],
        "reports": reports,
    }


async def _run_supplier_analysis(state: dict[str, Any]) -> dict[str, Any]:
    """供应商信用评分（等 Sol S-3 交付后实现）。"""
    # TODO: Sol S-3 交付后接入 supplier_risk.analyze_supplier
    return {"scores": []}
