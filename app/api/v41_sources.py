"""v4.1 第12节 - 来源与版本相关端点。

从 app/api/v41_api.py 拆出（保证单文件 ≤300 行，公开接口不变）。
本模块在 import 时将路由注册到 v41_api.router 上：
- GET /api/notices/{notice_id}/sources
- GET /api/sources/{source_id}/versions

依赖 v41_api.py 中已定义的 router / _ok / _err / _parse_int_id
（通过底部 import 触发本模块加载，避免循环导入：
v41_api.py 先定义 router 与助手函数，再 import 本模块）。
"""
from __future__ import annotations

import hashlib
from datetime import timedelta

from fastapi import Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v41_api import _err, _ok, _parse_int_id, router
from app.api.auth import verify_api_key
from app.models.database import get_db
from app.models.tender import Tender
from app.models.tender_project import NoticeSource, NoticeVersion


# 4. GET /api/notices/{notice_id}/sources
@router.get("/notices/{notice_id}/sources", dependencies=[Depends(verify_api_key)])
async def get_notice_sources(notice_id: str, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """获取来源页面和谱系（v4.1 第12节）。

    优先查 NoticeSource 表（真实四层实体）；未命中时调用
    source_lineage.judge_source_role 基于 URL 域名 + 内容实际计算来源质量。
    数据源标注：data_source 字段（notice_source_table / computed_by_source_lineage）。
    """
    nid = _parse_int_id(notice_id, "notice_id")
    if nid is None:
        return _err(f"非法 notice_id: {notice_id}", 400)
    tender = (await db.execute(select(Tender).where(Tender.id == nid))).scalar_one_or_none()
    if not tender:
        return _err(f"公告 {notice_id} 不存在")

    sources: list[dict] = []

    # 1. 优先查 NoticeSource 表（通过 source_url 关联）
    if tender.source_url:
        ns_rows = (await db.execute(
            select(NoticeSource).where(NoticeSource.source_url == tender.source_url)
        )).scalars().all()
        for ns in ns_rows:
            sources.append({
                "source_id": ns.notice_source_id,
                "source_url": ns.source_url,
                "source_platform": ns.source_platform,
                "publication_role": ns.publication_role,
                "source_quality": ns.source_quality,
                "quality_reason": ns.quality_reason or "",
                "first_seen_at": ns.first_seen_at.isoformat() if ns.first_seen_at else None,
                "data_source": "notice_source_table",
            })

    # 2. 未命中 NoticeSource → 调用 source_lineage.judge_source_role 实际计算
    if not sources:
        from app.processors.source_lineage import (
            judge_source_role, compute_source_group,
        )
        source_url = tender.source_url or ""
        content_text = tender.core_content or ""
        source_role, reason = judge_source_role(source_url, content_text=content_text)
        simhash_val = tender.simhash if tender.simhash is not None else 0
        source_group = (compute_source_group(source_url, simhash_val)
                        if source_url else f"grp_{tender.id}")
        sources.append({
            "source_id": f"src_{tender.id}",
            "source_url": source_url,
            "source_platform": tender.source_platform or "",
            "publication_role": source_role,
            "source_quality": source_role,
            "quality_reason": reason,
            "first_seen_at": tender.created_at.isoformat() if tender.created_at else None,
            "source_group": source_group,
            "data_source": "computed_by_source_lineage",
        })

    # 3. 构造 lineage（单源无转载链）
    first_source = sources[0]
    lineage = {
        "origin_source_id": first_source["source_id"],
        "repost_chain": [],
        "source_group": first_source.get("source_group", f"grp_{tender.id}"),
    }

    return _ok({"sources": sources, "lineage": lineage})


# 6. GET /api/sources/{source_id}/versions
@router.get("/sources/{source_id}/versions", dependencies=[Depends(verify_api_key)])
async def get_source_versions(source_id: str, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """获取页面版本历史（v4.1 第12节）。

    source_id 支持两种格式：
    - ULID（26 字符）：直接对应 NoticeSource.notice_source_id，查 NoticeVersion 表
    - src_{tender_id}：兼容旧格式，先尝试通过 source_url 关联 NoticeSource，
      命中则查 NoticeVersion 表；未命中回退到 Tender 表 created_at/updated_at

    数据源标注：data_source 字段（notice_version_table / tender_fallback）。
    """
    # 1. ULID 格式：直接查 NoticeVersion 表
    if len(source_id) == 26 and not source_id.startswith("src_"):
        versions_rows = (await db.execute(
            select(NoticeVersion)
            .where(NoticeVersion.notice_source_id == source_id)
            .order_by(NoticeVersion.fetched_at.desc())
        )).scalars().all()
        if not versions_rows:
            return _err(f"来源 {source_id} 无版本记录")
        return _ok(_build_versions_payload_from_notice_versions(source_id, versions_rows))

    # 2. src_{tender_id} 兼容格式
    if not source_id.startswith("src_"):
        return _err(f"非法 source_id: {source_id}", 400)
    nid = _parse_int_id(source_id[4:], "source_id")
    if nid is None:
        return _err(f"非法 source_id: {source_id}", 400)
    tender = (await db.execute(select(Tender).where(Tender.id == nid))).scalar_one_or_none()
    if not tender:
        return _err(f"来源 {source_id} 不存在")

    # 2a. 尝试通过 source_url 关联 NoticeSource → NoticeVersion（真实四层实体）
    if tender.source_url:
        ns = (await db.execute(
            select(NoticeSource).where(NoticeSource.source_url == tender.source_url)
        )).scalar_one_or_none()
        if ns is not None:
            versions_rows = (await db.execute(
                select(NoticeVersion)
                .where(NoticeVersion.notice_source_id == ns.notice_source_id)
                .order_by(NoticeVersion.fetched_at.desc())
            )).scalars().all()
            if versions_rows:
                return _ok(_build_versions_payload_from_notice_versions(
                    ns.notice_source_id, versions_rows
                ))

    # 2b. 回退到 Tender 表（向后兼容，标注 data_source=tender_fallback）
    return _ok(_build_versions_payload_from_tender(tender))


def _build_versions_payload_from_notice_versions(
    source_id: str, versions_rows: list,
) -> dict:
    """从 NoticeVersion 表组装版本列表 payload。"""
    return {
        "source_id": source_id,
        "total_versions": len(versions_rows),
        "versions": [{
            "version_id": v.version_id,
            "fetched_at": v.fetched_at.isoformat() if v.fetched_at else None,
            "change_type": v.change_type,
            "content_sha256": v.content_sha256,
            "raw_text_sha256": v.raw_text_sha256,
            "http_status": v.http_status,
            "snapshot_path": v.snapshot_path,
            "previous_version_id": v.previous_version_id,
        } for v in versions_rows],
        "data_source": "notice_version_table",
    }


def _build_versions_payload_from_tender(tender) -> dict:
    """从 Tender 表组装版本列表 payload（向后兼容回退路径）。"""
    raw_text = tender.core_content or ""
    content_sha = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
    versions = [{
        "version_id": 1,
        "fetched_at": tender.created_at.isoformat() if tender.created_at else None,
        "change_type": "initial",
        "content_sha256": content_sha,
        "snapshot_path": None,
    }]
    if tender.updated_at and tender.created_at and tender.updated_at > tender.created_at + timedelta(milliseconds=10):
        versions.insert(0, {
            "version_id": 2,
            "fetched_at": tender.updated_at.isoformat(),
            "change_type": "none",
            "content_sha256": content_sha,
            "snapshot_path": None,
        })
    return {
        "source_id": f"src_{tender.id}",
        "total_versions": len(versions),
        "versions": versions,
        "data_source": "tender_fallback",
    }
