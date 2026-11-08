# -*- coding: utf-8 -*-
"""一页尽调单演示端点（大件三：中标应收账款放款前核验）。

POST /api/demo/due-diligence
入参 JSON：{"winner": "...", "amount": "22.532万元", "purchaser": "...",
            "bid_number": "...", "finance_amount": "30万元"}
出参：尽调结果 JSON（含 report_text 一页尽调单）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from scripts.receivable_due_diligence import run_due_diligence

router = APIRouter(tags=["demo"])


class DueDiligenceRequest(BaseModel):
    winner: str = Field("", max_length=300, description="通知书上的中标人")
    amount: str = Field("", max_length=100, description="通知书上的中标金额")
    purchaser: str = Field("", max_length=300, description="通知书上的采购人")
    bid_number: str = Field("", max_length=100, description="通知书上的项目编号")
    finance_amount: str = Field("", max_length=100, description="申请融资金额")


@router.post("/due-diligence")
def demo_due_diligence(req: DueDiligenceRequest) -> dict:
    if not req.winner and not req.bid_number:
        raise HTTPException(status_code=400, detail="至少提供中标人或项目编号")
    return run_due_diligence(
        winner=req.winner,
        amount=req.amount,
        purchaser=req.purchaser,
        bid_number=req.bid_number,
        finance_amount=req.finance_amount,
    )
