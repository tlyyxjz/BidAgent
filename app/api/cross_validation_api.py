"""跨源交叉验证 API（可查验增强·多源佐证链演示端点）。

挂到 real_demo.router（/api/real）下的子模块：
- GET /api/real/realtime/cross-validation        全库佐证报告（零网络）
- GET /api/real/realtime/cross-validation/{tid}  单条佐证判定（零网络）

匹配逻辑全部来自 app.services.cross_validation（纯函数），
本端点只做 ORM→NoticeRef 转换，不含任何匹配策略。
诚实口径：无跨源匹配的公告如实标注 single_source（孤证）。
"""

from __future__ import annotations

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import get_db
from app.models.tender import Tender
from app.services.cross_validation import (
    NoticeRef,
    build_corroboration_report,
    corroborate_notice,
)
from app.utils.logger import get_logger

logger = get_logger("api.cross_validation")

# router 与 _ok 由主模块注入（子模块注册模式，见 real_demo.py 底部）
from app.api.real_demo import _ok, router  # noqa: E402


def _to_ref(t: Tender) -> NoticeRef:
    return NoticeRef(
        tender_id=t.id,
        source_platform=t.source_platform or "",
        bid_number=t.bid_number,
        project_name=t.project_name,
        simhash=t.simhash,
    )


async def _load_all_refs(db: AsyncSession) -> list[NoticeRef]:
    rows = (await db.execute(
        select(Tender.id, Tender.source_platform, Tender.bid_number,
               Tender.project_name, Tender.simhash))).all()
    return [NoticeRef(tender_id=tid, source_platform=plat or "",
                      bid_number=bn, project_name=pn, simhash=sh)
            for tid, plat, bn, pn, sh in rows]


@router.get("/realtime/cross-validation")
async def cross_validation_report(db: AsyncSession = Depends(get_db)):
    """全库跨源佐证报告（离线安全，零网络）。"""
    refs = await _load_all_refs(db)
    report = build_corroboration_report(refs)
    return _ok(report)


@router.get("/realtime/cross-validation/{tender_id}")
async def cross_validation_notice(tender_id: int,
                                  db: AsyncSession = Depends(get_db)):
    """单条公告佐证判定：有跨源匹配→corroborated，否则孤证标注。"""
    tender = (await db.execute(
        select(Tender).where(Tender.id == tender_id))
    ).scalar_one_or_none()
    if tender is None:
        raise HTTPException(status_code=404, detail=f"公告 {tender_id} 不存在")

    refs = await _load_all_refs(db)
    target = _to_ref(tender)
    result = corroborate_notice(target, refs)
    return _ok({
        "tender_id": tender.id,
        "source_platform": tender.source_platform or "",
        "project_name": tender.project_name,
        "bid_number": tender.bid_number,
        **result.to_dict(),
    })
