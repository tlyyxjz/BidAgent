"""W3 Demo 版本历史链端点。

提供：
- GET /api/demo/sources/{source_id}/versions  版本历史链（change_type + content_sha256 + material 变更）
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["demo"])


@router.get("/sources/{source_id}/versions")
async def demo_source_versions(source_id: str) -> JSONResponse:  # pragma: no cover
    """Demo: 版本历史链（change_type + content_sha256 + material 变更）。"""
    base_date = datetime(2026, 7, 15)
    versions = []
    change_types = ["create", "update_content", "update_material", "correction", "reissue"]
    materials = ["招标公告原文", "招标文件附件", "资格预审文件", "补遗文件", "答疑文件"]
    for i in range(6):
        vdate = base_date + timedelta(days=i * 2)
        ct = change_types[i % len(change_types)]
        sha = hashlib.sha256(f"{source_id}:content:v{i+1}:{ct}".encode("utf-8")).hexdigest()
        mat_change = []
        if "material" in ct or "correction" in ct:
            mat_change = [
                {"name": materials[i % 3], "action": "modified", "size": 102400 + i * 5120},
                {"name": materials[(i + 1) % 3], "action": "added", "size": 204800 + i * 10240},
            ]
        versions.append({
            "version_id": i + 1,
            "version_label": f"v{i + 1}.0",
            "change_type": ct,
            "content_sha256": sha,
            "material_sha256": hashlib.sha256(f"{source_id}:material:v{i+1}:{ct}".encode("utf-8")).hexdigest(),
            "publish_time": vdate.isoformat(),
            "change_summary": f"第 {i + 1} 版变更：{ct}，更新了 {len(mat_change)} 个附件",
            "material_changes": mat_change,
            "diff_highlight": {
                "added": 12 + i * 5,
                "removed": 3 + i * 2,
                "modified": 2 + i,
            },
        })
    versions.reverse()
    return JSONResponse(content={
        "code": 200,
        "data": {
            "source_id": source_id,
            "source_platform": "中国政府采购网",
            "source_url": f"http://www.ccgp.gov.cn/cggg/{source_id}",
            "total_versions": len(versions),
            "current_version": versions[0]["version_id"],
            "versions": versions,
        },
        "msg": "ok",
    })
