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
    """A2 修复：聊天页下载按钮接真实后端。
    从真实数据库查 tenders，调 docx_generator.generate_report 生成 Word 文件。
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
