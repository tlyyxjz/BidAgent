"""真实数据 Demo API（替代 Mock demo_api.py）.

从 bidagent.db 读取真实数据，按 Turbo 前端页面期望的格式返回.
数据来源：真实 LLM 抽取 + EvidenceLocator 验证（非 Mock）.

路由（与 Turbo HTML 对齐）:
- GET /api/real/tenders/{tender_id}/detail      → notice_detail.html
- GET /api/real/tenders/{tender_id}/versions    → version_history.html
- GET /api/real/tenders/{tender_id}/organization → org_profile.html

拆分说明（保证单文件 ≤300 行，公开接口不变）：
- 本文件保留：router 定义、字段标签/顺序常量、_infer_org_meta、
  _ok/_err 助手、get_tender_detail、list_tenders 端点。
- 版本历史端点 → app/api/real_demo_versions.py
- 组织画像端点 → app/api/real_demo_organization.py
- 子模块在底部 import 时将各自路由注册到 router 上。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import get_db
from app.models.tender import Tender
from app.models.evidence import ExtractedField, Evidence, FieldEvidenceLink

router = APIRouter(prefix="/api/real", tags=["real_demo"])

# 字段中文标签（与 Turbo notice_detail.html FL 对齐）
FIELD_LABELS = {
    "project_identifier": "项目编号",
    "project_name": "项目名称",
    "purchaser_name": "采购人",
    "winner_name": "中标人",
    "amount": "金额",
    "publish_date": "发布日期",
    "bid_deadline": "投标截止日期",
}

# 字段顺序（与 Turbo notice_detail.html FO 对齐）
FIELD_ORDER = [
    "project_identifier", "purchaser_name", "winner_name",
    "amount", "publish_date", "bid_deadline",
]



# 组织元数据推断规则（基于组织名称关键字）
_ORG_TYPE_RULES = [
    ("医院", "医疗机构"), ("临床", "医疗机构"), ("卫生院", "医疗机构"),
    ("大学", "教育机构"), ("学校", "教育机构"), ("学院", "教育机构"),
    ("教育委员会", "政府机构"), ("教育局", "政府机构"),
    ("人民政府", "政府机构"), ("财政局", "政府机构"),
    ("厅", "政府机构"), ("局", "政府机构"), ("委员会", "政府机构"),
    ("中心", "事业单位"), ("院所", "事业单位"),
    ("研究院", "事业单位"), ("研究所", "事业单位"),
    ("公司", "企业"), ("集团", "企业"), ("厂", "企业"),
]
_REGION_RULES = [
    ("北京", "北京"), ("上海", "上海"),
    ("广东", "广东"), ("深圳", "广东"), ("广州", "广东"),
    ("浙江", "浙江"), ("杭州", "浙江"),
    ("江苏", "江苏"), ("南京", "江苏"),
    ("四川", "四川"), ("成都", "四川"),
    ("湖北", "湖北"), ("武汉", "湖北"),
    ("山东", "山东"), ("济南", "山东"),
    ("陕西", "陕西"), ("西安", "陕西"),
    ("福建", "福建"), ("福州", "福建"), ("厦门", "福建"),
    ("天津", "天津"), ("重庆", "重庆"),
    ("辽宁", "辽宁"), ("沈阳", "辽宁"), ("大连", "辽宁"),
    ("河南", "河南"), ("郑州", "河南"),
    ("湖南", "湖南"), ("长沙", "湖南"),
    ("安徽", "安徽"), ("合肥", "安徽"),
    ("河北", "河北"), ("石家庄", "河北"),
    ("江西", "江西"), ("南昌", "江西"),
    ("山西", "山西"), ("太原", "山西"),
    ("云南", "云南"), ("昆明", "云南"),
    ("广西", "广西"), ("南宁", "广西"),
    ("甘肃", "甘肃"), ("兰州", "甘肃"),
    ("贵州", "贵州"), ("贵阳", "贵州"),
    ("海南", "海南"), ("海口", "海南"),
    ("吉林", "吉林"), ("长春", "吉林"),
    ("黑龙江", "黑龙江"), ("哈尔滨", "黑龙江"),
    ("内蒙古", "内蒙古"), ("呼和浩特", "内蒙古"),
    ("宁夏", "宁夏"), ("银川", "宁夏"),
    ("新疆", "新疆"), ("乌鲁木齐", "新疆"),
    ("西藏", "西藏"), ("拉萨", "西藏"),
    ("青海", "青海"), ("西宁", "青海"),
]


def _infer_org_meta(org_name: str, org_role: str) -> tuple[str, str]:
    """基于组织名称推断类型和地区，返回 (org_type, region)."""
    if not org_name:
        return ("未知", "未知")
    org_type = "其他"
    for keyword, t in _ORG_TYPE_RULES:
        if keyword in org_name:
            org_type = t
            break
    if org_type == "其他" and org_role == "winner":
        org_type = "企业"
    elif org_type == "其他" and org_role == "purchaser":
        org_type = "政府机构"
    region = "未知"
    for keyword, r in _REGION_RULES:
        if keyword in org_name:
            region = r
            break
    return (org_type, region)

def _ok(data: dict) -> JSONResponse:
    """统一 {code:200, data, msg} 响应格式（Turbo HTML 期望）."""
    return JSONResponse({"code": 200, "data": data, "msg": "ok"})


def _err(msg: str, code: int = 404) -> JSONResponse:
    return JSONResponse({"code": code, "data": None, "msg": msg}, status_code=code)


@router.get("/tenders/{tender_id}/detail")
async def get_tender_detail(tender_id: int, db: AsyncSession = Depends(get_db)):
    """公告详情 + 字段 + 证据（notice_detail.html 用）.

    返回格式对齐 Turbo 期望:
    {
        clean_raw_text: "...",
        fields: [{
            field_id, field_name, field_label, support_level, field_status,
            values: [{
                value_id, raw_value, normalized_value, amount_type,
                evidences: [{id, text, start, end, role, match_method, confidence}]
            }]
        }]
    }
    """
    tender = (await db.execute(
        select(Tender).where(Tender.id == tender_id)
    )).scalar_one_or_none()
    if not tender:
        return _err(f"公告 {tender_id} 不存在")

    raw_text = tender.core_content or ""

    # 查所有字段
    fields_result = await db.execute(
        select(ExtractedField)
        .where(ExtractedField.tender_id == tender_id)
        .order_by(ExtractedField.id)
    )
    db_fields = fields_result.scalars().all()

    # 按字段名分组（支持多值）
    fields_by_name: dict[str, list[ExtractedField]] = {}
    for f in db_fields:
        fields_by_name.setdefault(f.field_name, []).append(f)

    # 构造响应（按 FIELD_ORDER 排序）
    resp_fields = []
    for fname in FIELD_ORDER:
        if fname not in fields_by_name:
            # 该字段不存在，标记 absent
            resp_fields.append({
                "field_id": fname,
                "field_name": fname,
                "field_label": FIELD_LABELS.get(fname, fname),
                "support_level": "unsupported",
                "field_status": "absent",
                "values": [],
            })
            continue

        field_list = fields_by_name[fname]
        values = []
        for vi, f in enumerate(field_list):
            # 查该字段的证据
            links_result = await db.execute(
                select(FieldEvidenceLink, Evidence)
                .join(Evidence, FieldEvidenceLink.evidence_id == Evidence.id)
                .where(FieldEvidenceLink.field_id == f.id)
                .order_by(FieldEvidenceLink.sequence)
            )
            evidences = []
            for link, ev in links_result:
                evidences.append({
                    "id": f"{f.field_name}_{vi}_{link.sequence}",
                    "text": ev.evidence_text,
                    "start": ev.raw_start,
                    "end": ev.raw_end,
                    "role": link.evidence_role,
                    "match_method": ev.match_method,
                    "confidence": 0.95 if ev.verified else 0.5,
                })

            values.append({
                "value_id": f"{f.field_name}_{vi}",
                "raw_value": f.raw_value or "",
                "normalized_value": f.raw_value or "",
                "amount_type": f.amount_type,
                "evidences": evidences,
            })

        # support_level 映射到 Turbo 口径
        support = field_list[0].support_level or "unsupported"
        resp_fields.append({
            "field_id": fname,
            "field_name": fname,
            "field_label": FIELD_LABELS.get(fname, fname),
            "support_level": support,
            "field_status": field_list[0].field_status or "present",
            "values": values,
        })

    data = {
        "clean_raw_text": raw_text,
        "tender_id": tender.id,
        "project_name": tender.project_name,
        "fields": resp_fields,
    }
    return _ok(data)


@router.get("/tenders")
async def list_tenders(db: AsyncSession = Depends(get_db)):
    """列出所有公告（含 amount/purchaser/publish_date/platform 供检索页展示）.

    批量查询字段避免 N+1，供 search.html 真实数据源使用.
    """
    result = await db.execute(
        select(Tender.id, Tender.project_name, Tender.notice_type,
               Tender.created_at, Tender.source_platform)
        .order_by(Tender.id)
    )
    rows = result.all()
    tender_ids = [r.id for r in rows]

    # 批量查 amount/purchaser_name/publish_date（避免 N+1）
    field_map: dict[int, dict] = {}
    if tender_ids:
        flds = await db.execute(
            select(ExtractedField)
            .where(ExtractedField.tender_id.in_(tender_ids))
            .where(ExtractedField.field_name.in_(["amount", "purchaser_name", "publish_date"]))
            .order_by(ExtractedField.id)
        )
        for f in flds.scalars().all():
            d = field_map.setdefault(f.tender_id, {})
            if f.field_name not in d and f.raw_value:
                d[f.field_name] = f.raw_value

    tenders = []
    for r in rows:
        fm = field_map.get(r.id, {})
        tenders.append({
            "id": r.id,
            "name": r.project_name,
            "type": r.notice_type,
            "amount": fm.get("amount", ""),
            "purchaser": fm.get("purchaser_name", ""),
            "publish_date": fm.get("publish_date", "")
            or (r.created_at.strftime("%Y-%m-%d") if r.created_at else ""),
            "platform": r.source_platform or "",
        })
    return _ok({"tenders": tenders})


# ==== 子模块路由注册 ====
# 在 router 定义之后 import，子模块将各自路由注册到 router 上。
# 顺序：版本历史 → 组织画像（与原 real_demo.py 中的路由顺序保持一致）。
from app.api import real_demo_versions  # noqa: E402,F401
from app.api import real_demo_organization  # noqa: E402,F401

# ==== re-export：保持原有公开接口不变 ====
# 以下函数已拆到子模块实现，但原有 import 路径（from app.api.real_demo import ...）
# 必须继续可用。子模块已在上一步完成 import，此处可安全 re-export。
from app.api.real_demo_versions import get_tender_versions  # noqa: E402,F401
from app.api.real_demo_organization import get_tender_organization  # noqa: E402,F401
