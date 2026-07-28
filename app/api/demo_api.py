"""W3 Demo 数据 API（Mock 接口，供前端静态页面使用）。

提供：
- GET /api/demo/fields/{field_id}     字段证据详情
- GET /api/demo/tenders/{tender_id}/fields  招标字段列表（含证据）
- GET /api/demo/sources/{source_id}/versions  版本历史
- GET /api/demo/organizations/{org_id}  组织实体画像
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/demo", tags=["demo"])

_GOLD_RAW_DIR = Path(
    r"C:\Users\Lenovo\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent"
    r"\work-mode-projects\6a57291a0778ce48bfe693d2\_w2_raw"
)
_GOLD_ANNOT_DIR = Path(
    r"C:\Users\Lenovo\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent"
    r"\work-mode-projects\6a57291a0778ce48bfe693d2\_w2_annotations"
)

FIELD_LABELS = {
    "project_identifier": "项目编号",
    "purchaser_name": "采购人",
    "winner_name": "中标人",
    "amount": "金额",
    "publish_date": "发布日期",
    "bid_deadline": "投标截止日期",
}


def _load_annotation(doc_id: str) -> dict | None:
    import json
    if not _GOLD_ANNOT_DIR.exists():
        return None
    for f in _GOLD_ANNOT_DIR.glob("annotation_*.json"):
        name = f.stem[len("annotation_"):]
        if doc_id == name or doc_id.startswith(name) or name.startswith(doc_id):
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("document_id") == doc_id:
                return data
        except Exception:
            continue
    return None


def _load_raw(doc_id: str) -> str | None:
    if not _GOLD_RAW_DIR.exists():
        return None
    for f in _GOLD_RAW_DIR.glob("*.txt"):
        stem = f.stem
        if doc_id == stem or doc_id.startswith(stem) or stem.startswith(doc_id):
            return f.read_text(encoding="utf-8")
    return None


@router.get("/sources/{source_id}/versions")
async def demo_source_versions(source_id: str) -> JSONResponse:
    """Demo: 版本历史链（change_type + content_sha256 + material 变更）。"""
    base_date = datetime(2026, 7, 15)
    versions = []
    change_types = ["create", "update_content", "update_material", "correction", "reissue"]
    materials = ["招标公告原文", "招标文件附件", "资格预审文件", "补遗文件", "答疑文件"]
    for i in range(6):
        vdate = base_date + timedelta(days=i * 2)
        ct = change_types[i % len(change_types)]
        sha = f"a1b2c3d4e5f678901234567890abcdef{i:02d}"
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
            "material_sha256": f"fedcba0987654321fedcba0987654321{i:02d}",
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


@router.get("/organizations/{org_id}")
async def demo_org_profile(org_id: str) -> JSONResponse:
    """Demo: 组织实体画像（中标活跃度 + Top3 采购人集中度 + 废标关联 + 数据完整性）。"""
    org_name_map = {
        "org_001": "北第三医院",
        "org_002": "中国科学院计算技术研究所",
        "org_003": "北京市教育委员会",
    }
    org_name = org_name_map.get(org_id, f"组织 {org_id}")
    today = datetime(2026, 7, 27)
    days_90 = []
    for i in range(90):
        d = today - timedelta(days=89 - i)
        days_90.append({
            "date": d.strftime("%Y-%m-%d"),
            "count": 2 + (i * 7 % 5),
        })
    top3_purchasers = [
        {"name": "北京大学第三医院", "count": 156, "ratio": 0.45},
        {"name": "北京市海淀区卫健委", "count": 89, "ratio": 0.26},
        {"name": "中国医学科学院", "count": 52, "ratio": 0.15},
    ]
    waste_bids = [
        {"project_name": "医疗设备采购项目", "waste_date": "2026-06-15", "reason": "有效投标人不足3家"},
        {"project_name": "信息化系统建设", "waste_date": "2026-05-20", "reason": "资格审查不通过"},
    ]
    data_completeness = {
        "platforms": ["中国政府采购网", "北京市政府采购网", "全国公共资源交易平台"],
        "time_range": "2025-01-01 至 2026-07-27",
        "total_notices": 347,
        "tender_count": 210,
        "award_count": 120,
        "correction_count": 17,
        "completeness_score": 87.5,
        "missing_fields": ["联系人电话（隐私脱敏）", "代理机构联系方式"],
    }
    return JSONResponse(content={
        "code": 200,
        "data": {
            "org_id": org_id,
            "org_name": org_name,
            "org_type": "医疗机构",
            "region": "北京市海淀区",
            "activity_90d": {
                "total": 42,
                "tender_count": 28,
                "award_count": 14,
                "daily": days_90,
            },
            "top3_purchasers": top3_purchasers,
            "top3_concentration": 0.86,
            "waste_bid_related": waste_bids,
            "waste_bid_count": len(waste_bids),
            "data_completeness": data_completeness,
        },
        "msg": "ok",
    })


MOCK_RAW_TEXT = (
    "北京市政府采购中心\n"
    "医疗设备采购项目招标公告\n"
    "项目编号：BJGPC-2026-0042\n"
    "发布日期：2026年7月20日\n\n"
    "一、项目基本情况\n"
    "项目名称：信息化系统建设及医疗设备采购项目\n"
    "预算金额：人民币壹仟贰佰万元整（¥12,000,000.00）\n"
    "最高限价：1200万元\n"
    "采购需求：本项目采购信息化系统一套及配套医疗设备，具体详见招标文件。\n"
    "合同履行期限：合同签订后90天内完成供货、安装及调试。\n\n"
    "二、申请人的资格要求\n"
    "1. 满足《中华人民共和国政府采购法》第二十二条规定；\n"
    "2. 本项目不接受联合体投标。\n\n"
    "三、获取招标文件\n"
    "时间：2026年7月21日至2026年7月28日\n"
    "地点：北京市政府采购中心网站\n"
    "方式：在线下载\n"
    "售价：0元\n\n"
    "四、提交投标文件截止时间\n"
    "2026年8月15日 09点30分（北京时间）\n"
    "地点：北京市政府采购中心开标大厅\n\n"
    "五、公告期限\n"
    "自本公告发布之日起5个工作日。\n\n"
    "六、其他补充事宜\n"
    "本项目落实节约能源、保护环境等政府采购政策。\n\n"
    "七、对本次招标提出询问，请按以下方式联系\n"
    "1. 采购人信息\n"
    "名称：北京大学第三医院\n"
    "地址：北京市海淀区花园北路49号\n"
    "联系方式：010-82266699\n"
    "2. 采购代理机构信息\n"
    "名称：北京市政府采购中心\n"
    "地址：北京市丰台区玉林西路45号\n"
    "联系方式：010-63398900"
)


def _build_mock_tender_fields(tender_id: str) -> dict:
    """生成 mock 招标字段数据（无本地标注时使用）。"""
    fields = [
        {
            "field_id": "project_identifier",
            "field_name": "project_identifier",
            "field_label": "项目编号",
            "support_level": "supported",
            "field_status": "present",
            "values": [{
                "value_id": "project_identifier_0",
                "raw_value": "BJGPC-2026-0042",
                "normalized_value": "BJGPC-2026-0042",
                "evidences": [{
                    "id": "project_identifier_0_0",
                    "text": "BJGPC-2026-0042",
                    "start": 42,
                    "end": 58,
                    "role": "primary",
                    "match_method": "exact",
                    "confidence": 0.98,
                }],
            }],
        },
        {
            "field_id": "project_name",
            "field_name": "project_name",
            "field_label": "项目名称",
            "support_level": "supported",
            "field_status": "present",
            "values": [{
                "value_id": "project_name_0",
                "raw_value": "信息化系统建设及医疗设备采购项目",
                "normalized_value": "信息化系统建设及医疗设备采购项目",
                "evidences": [
                    {
                        "id": "project_name_0_0",
                        "text": "信息化系统建设及医疗设备采购项目",
                        "start": 84,
                        "end": 108,
                        "role": "primary",
                        "match_method": "exact",
                        "confidence": 0.95,
                    },
                    {
                        "id": "project_name_0_1",
                        "text": "医疗设备采购项目",
                        "start": 14,
                        "end": 24,
                        "role": "context",
                        "match_method": "exact",
                        "confidence": 0.8,
                    },
                ],
            }],
        },
        {
            "field_id": "purchaser_name",
            "field_name": "purchaser_name",
            "field_label": "采购人",
            "support_level": "supported",
            "field_status": "present",
            "values": [{
                "value_id": "purchaser_name_0",
                "raw_value": "北京大学第三医院",
                "normalized_value": "北京大学第三医院",
                "evidences": [
                    {
                        "id": "purchaser_name_0_0",
                        "text": "北京大学第三医院",
                        "start": 486,
                        "end": 495,
                        "role": "primary",
                        "match_method": "exact",
                        "confidence": 0.99,
                    },
                    {
                        "id": "purchaser_name_0_1",
                        "text": "采购人信息\n名称：北京大学第三医院",
                        "start": 480,
                        "end": 498,
                        "role": "context",
                        "match_method": "fuzzy",
                        "confidence": 0.85,
                    },
                ],
            }],
        },
        {
            "field_id": "amount",
            "field_name": "amount",
            "field_label": "金额",
            "support_level": "supported",
            "field_status": "present",
            "values": [
                {
                    "value_id": "amount_0",
                    "raw_value": "12,000,000.00",
                    "normalized_value": "12000000.00",
                    "amount_type": "budget",
                    "evidences": [
                        {
                            "id": "amount_0_0",
                            "text": "12,000,000.00",
                            "start": 130,
                            "end": 141,
                            "role": "primary",
                            "match_method": "exact",
                            "confidence": 0.97,
                        },
                        {
                            "id": "amount_0_1",
                            "text": "壹仟贰佰万元整",
                            "start": 118,
                            "end": 127,
                            "role": "qualifier",
                            "match_method": "exact",
                            "confidence": 0.92,
                        },
                    ],
                },
            ],
        },
        {
            "field_id": "publish_date",
            "field_name": "publish_date",
            "field_label": "发布日期",
            "support_level": "supported",
            "field_status": "present",
            "values": [{
                "value_id": "publish_date_0",
                "raw_value": "2026年7月20日",
                "normalized_value": "2026-07-20",
                "evidences": [{
                    "id": "publish_date_0_0",
                    "text": "2026年7月20日",
                    "start": 63,
                    "end": 73,
                    "role": "primary",
                    "match_method": "exact",
                    "confidence": 0.96,
                }],
            }],
        },
        {
            "field_id": "bid_deadline",
            "field_name": "bid_deadline",
            "field_label": "投标截止日期",
            "support_level": "supported",
            "field_status": "present",
            "values": [{
                "value_id": "bid_deadline_0",
                "raw_value": "2026年8月15日 09点30分",
                "normalized_value": "2026-08-15 09:30:00",
                "evidences": [
                    {
                        "id": "bid_deadline_0_0",
                        "text": "2026年8月15日 09点30分",
                        "start": 298,
                        "end": 316,
                        "role": "primary",
                        "match_method": "exact",
                        "confidence": 0.94,
                    },
                    {
                        "id": "bid_deadline_0_1",
                        "text": "提交投标文件截止时间\n2026年8月15日 09点30分",
                        "start": 285,
                        "end": 320,
                        "role": "context",
                        "match_method": "fuzzy",
                        "confidence": 0.82,
                    },
                ],
            }],
        },
        {
            "field_id": "winner_name",
            "field_name": "winner_name",
            "field_label": "中标人",
            "support_level": "unsupported",
            "field_status": "absent",
            "values": [],
        },
        {
            "field_id": "agency_name",
            "field_name": "agency_name",
            "field_label": "代理机构",
            "support_level": "unsupported",
            "field_status": "rejected",
            "values": [],
        },
    ]
    return {
        "tender_id": tender_id,
        "document_id": tender_id,
        "notice_type": "招标公告",
        "clean_raw_text": MOCK_RAW_TEXT,
        "fields": fields,
    }


@router.get("/tenders/{tender_id}/fields")
async def demo_tender_fields(tender_id: str) -> JSONResponse:
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
async def demo_field_evidence(field_id: str, doc: str = Query("mock_tender")) -> JSONResponse:
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
