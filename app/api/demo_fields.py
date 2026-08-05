"""W3 Demo 招标字段 + 证据链端点。

提供：
- GET /api/demo/tenders/{tender_id}/fields  招标字段列表（含证据）
- GET /api/demo/fields/{field_id}            单个字段的证据详情（带偏移量）
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from app.api.demo_common import (
    FIELD_LABELS,
    _load_annotation,
    _load_raw,
)
from app.api.demo_tender_mock import _build_mock_tender_fields

router = APIRouter(tags=["demo"])


@router.get("/tenders/{tender_id}/fields")
async def demo_tender_fields(tender_id: str) -> JSONResponse:  # pragma: no cover
    """Demo: 获取招标公告的所有字段 + 证据列表。"""
    ann = _load_annotation(tender_id)
    raw = _load_raw(tender_id)
    if ann is None:
        data = _build_mock_tender_fields(tender_id)
        return JSONResponse(content={"code": 200, "data": data, "msg": "ok"})
    fields = []
    for f in ann.get("fields", []):
        fname = f.get("field_name", "")
        values = []
        for vi, v in enumerate(f.get("values", [])):
            evidences = []
            for ei, e in enumerate(v.get("acceptable_evidence_spans", [])):
                evidences.append({
                    "id": f"{fname}_{vi}_{ei}",
                    "text": e.get("text", ""),
                    "start": e.get("start", 0),
                    "end": e.get("end", 0),
                    "role": e.get("role", "primary"),
                    "match_method": e.get("match_method", "exact"),
                    "confidence": e.get("confidence", 0.9),
                })
            values.append({
                "value_id": f"{fname}_{vi}",
                "raw_value": v.get("raw_value", ""),
                "normalized_value": v.get("normalized_value", ""),
                "amount_type": v.get("amount_type"),
                "lot_id": v.get("lot_id"),
                "evidences": evidences,
            })
        fields.append({
            "field_id": fname,
            "field_name": fname,
            "field_label": FIELD_LABELS.get(fname, fname),
            "support_level": f.get("support_level", "unsupported"),
            "field_status": f.get("gold_status", "present"),
            "values": values,
        })
    return JSONResponse(content={
        "code": 200,
        "data": {
            "tender_id": tender_id,
            "document_id": ann.get("document_id", tender_id),
            "notice_type": ann.get("notice_type", ""),
            "clean_raw_text": raw or "",
            "fields": fields,
        },
        "msg": "ok",
    })


@router.get("/fields/{field_id}")
async def demo_field_evidence(field_id: str, doc: str = Query("mock_tender")) -> JSONResponse:  # pragma: no cover
    """Demo: 获取单个字段的证据详情（带偏移量）。"""
    ann = _load_annotation(doc)
    raw = _load_raw(doc)
    if ann is None:
        mock = _build_mock_tender_fields(doc)
        field = next((f for f in mock["fields"] if f["field_id"] == field_id), None)
        if field is None:
            raise HTTPException(status_code=404, detail=f"未找到字段: {field_id}")
        values = []
        for v in field["values"]:
            evidences = []
            for e in v["evidences"]:
                evidences.append({
                    "evidence_id": e["id"],
                    "text": e["text"],
                    "raw_start": e["start"],
                    "raw_end": e["end"],
                    "normalized_start": e["start"],
                    "normalized_end": e["end"],
                    "role": e["role"],
                    "match_method": e["match_method"],
                    "confidence": e["confidence"],
                    "context_before": "",
                    "context_after": "",
                })
            values.append({
                "value_id": v["value_id"],
                "raw_value": v["raw_value"],
                "normalized_value": v["normalized_value"],
                "amount_type": v.get("amount_type"),
                "evidences": evidences,
            })
        return JSONResponse(content={
            "code": 200,
            "data": {
                "field_id": field_id,
                "field_label": field["field_label"],
                "support_level": field["support_level"],
                "field_status": field["field_status"],
                "clean_raw_text": mock["clean_raw_text"],
                "values": values,
            },
            "msg": "ok",
        })
    field = None
    for f in ann.get("fields", []):
        if f.get("field_name") == field_id:
            field = f
            break
    if field is None:
        raise HTTPException(status_code=404, detail=f"未找到字段: {field_id}")
    values = []
    for vi, v in enumerate(field.get("values", [])):
        evidences = []
        for ei, e in enumerate(v.get("acceptable_evidence_spans", [])):
            evidences.append({
                "evidence_id": f"{field_id}_{vi}_{ei}",
                "text": e.get("text", ""),
                "raw_start": e.get("start", 0),
                "raw_end": e.get("end", 0),
                "normalized_start": e.get("start", 0),
                "normalized_end": e.get("end", 0),
                "role": e.get("role", "primary"),
                "match_method": e.get("match_method", "exact"),
                "confidence": e.get("confidence", 0.9),
                "context_before": e.get("context_before", ""),
                "context_after": e.get("context_after", ""),
            })
        values.append({
            "value_id": f"{field_id}_{vi}",
            "raw_value": v.get("raw_value", ""),
            "normalized_value": v.get("normalized_value", ""),
            "amount_type": v.get("amount_type"),
            "evidences": evidences,
        })
    return JSONResponse(content={
        "code": 200,
        "data": {
            "field_id": field_id,
            "field_label": FIELD_LABELS.get(field_id, field_id),
            "support_level": field.get("support_level", "unsupported"),
            "field_status": field.get("gold_status", "present"),
            "clean_raw_text": raw or "",
            "values": values,
        },
        "msg": "ok",
    })
