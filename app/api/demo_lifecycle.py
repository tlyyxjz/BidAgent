# -*- coding: utf-8 -*-
"""项目生命周期链演示端点（大件五：证据再加固·链条）。

POST /api/demo/lifecycle-chain
入参 JSON：{"bid_number": "..."}
出参：生命周期链 JSON（含 stages/report_text，每环节附 SHA-256 存证与重放命令）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from scripts.lifecycle_chain import build_lifecycle_chain

router = APIRouter(tags=["demo"])


class LifecycleChainRequest(BaseModel):
    bid_number: str = Field("", max_length=100, description="项目编号")


@router.post("/lifecycle-chain")
def demo_lifecycle_chain(req: LifecycleChainRequest) -> dict:
    if not req.bid_number.strip():
        raise HTTPException(status_code=400, detail="请提供项目编号")
    return build_lifecycle_chain(req.bid_number)
