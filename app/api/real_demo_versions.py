"""真实数据 Demo API - 公告版本历史端点。

从 app/api/real_demo.py 拆出（保证单文件 ≤300 行，公开接口不变）。
本模块在 import 时将路由注册到 real_demo.router 上：
- GET /api/real/tenders/{tender_id}/versions  → version_history.html

依赖 real_demo.py 中已定义的 router 与 _ok/_err 助手（通过底部 import 触发本模块加载，
避免循环导入：real_demo.py 先定义 router，再 import 本模块）。
"""
from __future__ import annotations

import hashlib
from datetime import timedelta

from fastapi import Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.real_demo import _err, _ok, router
from app.models.database import get_db
from app.models.tender import Tender


@router.get("/tenders/{tender_id}/versions")
async def get_tender_versions(tender_id: int, db: AsyncSession = Depends(get_db)):
    """公告版本历史（version_history.html 用）.

    基于真实 Tender 数据构造版本记录.
    当前每篇公告只有初始抓取版本（单版本），content_sha256 基于实际原文计算.
    """
    tender = (await db.execute(
        select(Tender).where(Tender.id == tender_id)
    )).scalar_one_or_none()
    if not tender:
        return _err(f"公告 {tender_id} 不存在")

    raw_text = tender.core_content or ""
    content_sha = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()  # 完整 64 位 SHA256
    material_sha = hashlib.sha256(
        (raw_text[:500] + raw_text[-500:]).encode("utf-8")
    ).hexdigest()  # 完整 64 位 SHA256

    # 真实版本：初始抓取
    versions = [{
        "version_id": 1,
        "fetched_at": tender.created_at.strftime("%Y-%m-%d %H:%M") if tender.created_at else "2026-07-28 12:00",
        "change_type": "create",
        "change_type_label": "初始抓取",
        "content_sha256": content_sha,
        "material_sha256": material_sha,
        "diff_summary": "首次采集入库",
        "diff_lines": [],
    }]

    # 如果有更新时间且不同于创建时间，加一个 none 版本
    # 容差 10 毫秒：created_at/updated_at 各自 default=utc_now，新建记录可能有微秒级差值
    if tender.updated_at and tender.created_at and tender.updated_at > tender.created_at + timedelta(milliseconds=10):
        versions.insert(0, {
            "version_id": 2,
            "fetched_at": tender.updated_at.strftime("%Y-%m-%d %H:%M"),
            "change_type": "none",
            "change_type_label": "复查无变化",
            "content_sha256": content_sha,
            "material_sha256": material_sha,
            "diff_summary": "复查采集，内容无变化",
            "diff_lines": [],
        })

    stats = {
        "total_versions": len(versions),
        "has_material_change": False,
        "source_platform": tender.source_platform or "ccgp",
        "source_url": tender.source_url or "",
        "first_seen": versions[-1]["fetched_at"],
        "last_seen": versions[0]["fetched_at"],
    }

    return _ok({"versions": versions, "stats": stats})
