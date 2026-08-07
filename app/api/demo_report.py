"""W3 Demo 报告生成端点（A2 修复）。

提供：
- GET /api/demo/report  生成 Word 报告（真实数据库 + docx_generator）

注：保持函数内局部 import（与原 demo_api.py 一致），避免模块加载副作用。
"""

from __future__ import annotations

from fastapi import APIRouter, Query

router = APIRouter(tags=["demo"])


@router.get("/report", summary="生成 Word 报告（真实数据库 + docx_generator）")
async def demo_report(query: str = Query("医疗设备采购", description="用户查询")):
    """Fallback 端点：直接查库生成报告（无主题过滤、无 core_content、无 finance_summary）。

    方案A 修复后，前端优先走 /report/download?sid=xxx 下载 pipeline 产物。
    本端点仅作为 fallback 保留，不推荐主链路使用。
    """
    import os
    from fastapi.responses import FileResponse
    from app.llm.schemas import ParsedFilters
    from app.report.docx_generator import generate_report
    from app.models.database import AsyncSessionLocal
    from app.models.tender import Tender
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Tender).limit(10))
        tenders = result.scalars().all()

    items = []
    for t in tenders:
        items.append({
            "project_name": t.project_name or "",
            "bid_number": t.bid_number or "",
            "budget_amount": float(t.budget_amount) if t.budget_amount else None,
            "win_amount": float(t.win_amount) if t.win_amount else None,
            "publish_time": t.publish_time.isoformat() if t.publish_time else None,
            "deadline": t.deadline.isoformat() if t.deadline else None,
            "tender_org": t.tender_org or "",
            "win_company": t.win_company or "",
            "source_platform": t.source_platform or "",
            "source_url": t.source_url or "",
            "location": t.location or "",
            "notice_type": t.notice_type or "",
        })

    filters = ParsedFilters(raw_query=query, topic=query)
    report_path = await generate_report(filters, items, job_id=f"demo_{query[:8]}")

    filename = os.path.basename(report_path)
    return FileResponse(
        path=report_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=filename,
    )


@router.get("/report/download", summary="下载 pipeline 生成的报告（方案A 修复）")
async def demo_report_download(sid: str = Query(..., description="pipeline session_id")):
    """方案A 修复：从 pipeline session 中提取 report_path 并下载。

    确保用户下载到经过完整 6 Agent 处理的报告：
    - 带主题过滤（get_unpushed_tenders）
    - 含 core_content 字段（命题 5 字段齐全）
    - 传 source_texts（反幻觉校验生效）
    - 传 finance_summary（金融分析章节有内容）
    """
    import os
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from app.agents.pipeline import get_session

    session = await get_session(sid)
    if not session:
        raise HTTPException(
            status_code=404, detail="session 不存在或已过期（TTL 1 小时）"
        )

    result = session.get("result") or {}
    report_path = result.get("report_path")
    if not report_path:
        raise HTTPException(
            status_code=404, detail="报告未生成（pipeline 可能未采集到新数据）"
        )

    if not os.path.exists(report_path):
        raise HTTPException(status_code=404, detail="报告文件不存在或已被清理")

    filename = os.path.basename(report_path)
    return FileResponse(
        path=report_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=filename,
    )
