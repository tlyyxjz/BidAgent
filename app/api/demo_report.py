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
    from app.llm.parser import parse_query
    from app.report.docx_generator import generate_report
    from app.models.database import AsyncSessionLocal
    from app.models.tender import Tender
    from sqlalchemy import select, or_

    # 解析用户查询（复用 intent_agent 的 parse_query，含 LLM + fallback）
    parsed = await parse_query(query)

    # 构建过滤条件（与 processor_agent 逻辑一致）
    topic = parsed.topic or ""
    region = parsed.region or ""
    keywords = parsed.keywords or []
    search_words = [topic] + keywords if topic else keywords
    # 从 raw_query 提取额外关键词
    raw_q = parsed.raw_query or query
    region_words = ["北京", "上海", "广东", "深圳", "浙江", "江苏", "四川",
                    "湖北", "山东", "河南", "福建", "安徽", "最近", "的", "招标", "公告"]
    extra = [w for w in raw_q.replace("招标", "").replace("公告", "").split()
             if w and w not in region_words and w not in search_words]
    search_words.extend(extra)

    # 公告类型映射
    _nt_map = {"中标": "award", "成交": "award", "更正": "correction",
               "变更": "correction", "招标": "tender", "采购": "tender"}
    _nt_vals = []
    for nt in (parsed.notice_types or []):
        for k, v in _nt_map.items():
            if k in str(nt) or v == str(nt):
                _nt_vals.append(v)
                break

    # P0 修复：无搜索关键词时返回空（不退化为全表查询）
    if not search_words:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=400,
            content={"detail": "无法解析查询关键词，请提供更具体的查询条件"},
        )

    async with AsyncSessionLocal() as db:
        stmt = select(Tender)
        # 关键词过滤（topic/keywords 任一命中 project_name 或 core_content）
        if search_words:
            kw_filters = []
            for kw in search_words:
                if kw:
                    kw_filters.append(or_(
                        Tender.project_name.ilike(f"%{kw}%"),
                        Tender.core_content.ilike(f"%{kw}%"),
                    ))
            if kw_filters:
                stmt = stmt.where(or_(*kw_filters) if len(kw_filters) > 1 else kw_filters[0])
        # 地区过滤（P0 修复：补上 project_name）
        if region:
            stmt = stmt.where(or_(
                Tender.project_name.ilike(f"%{region}%"),
                Tender.tender_org.ilike(f"%{region}%"),
                Tender.core_content.ilike(f"%{region}%"),
                Tender.location.ilike(f"%{region}%"),
            ))
        # 公告类型过滤
        if _nt_vals:
            stmt = stmt.where(Tender.notice_type.in_(_nt_vals))
        # P0 修复：查不到时如实返回空（不再 fallback 返回不相关数据）
        result = await db.execute(stmt.order_by(Tender.id.desc()).limit(30))
        tenders = result.scalars().all()

    items = []
    source_texts = {}
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
            "core_content": t.core_content or "",
        })
        if t.source_url and t.core_content:
            source_texts[t.source_url] = t.core_content[:500]

    report_path = await generate_report(
        parsed, items, job_id=f"demo_{query[:8]}",
        source_texts=source_texts or None,
    )

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
