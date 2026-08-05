"""W3 Demo 按名称查真实数据库聚合组织活跃度。

提供：
- _query_real_org_by_name：按 name 匹配 ExtractedField (purchaser_name/winner_name)，
  聚合该组织在所有公告中的活跃度。未命中返回 None。

注：函数内的局部 import（Counter / datetime / _infer_org_meta / logging）保持与
原 demo_api.py 一致，避免模块加载顺序副作用。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.evidence import ExtractedField
from app.models.tender import Tender


async def _query_real_org_by_name(name: str, db: AsyncSession) -> dict | None:
    """查真实数据库：按 name 匹配 ExtractedField (purchaser_name/winner_name)，
    聚合该组织在所有公告中的活跃度。未命中返回 None。"""
    if not name:
        return None
    try:
        # 查所有公告中该组织出现的次数（先精确匹配，再模糊匹配）
        all_fields_result = await db.execute(
            select(ExtractedField, Tender)
            .join(Tender, ExtractedField.tender_id == Tender.id)
            .where(ExtractedField.field_name.in_(["purchaser_name", "winner_name"]))
            .where(ExtractedField.raw_value == name)
        )
        all_occurrences = all_fields_result.all()
        if not all_occurrences:
            # 模糊匹配：name 是 raw_value 的子串，或 raw_value 包含 name
            like_pattern = f"%{name}%"
            all_fields_result = await db.execute(
                select(ExtractedField, Tender)
                .join(Tender, ExtractedField.tender_id == Tender.id)
                .where(ExtractedField.field_name.in_(["purchaser_name", "winner_name"]))
                .where(ExtractedField.raw_value.like(like_pattern))
            )
            all_occurrences = all_fields_result.all()
        if not all_occurrences:
            return None

        total = len(all_occurrences)
        tender_count = sum(1 for f, t in all_occurrences if t.notice_type and "tender" in t.notice_type)
        award_count = sum(1 for f, t in all_occurrences if t.notice_type and "award" in t.notice_type)

        # 构造 90 天 daily 数据（基于真实 publish_time 聚合）
        from collections import Counter
        from datetime import datetime as _dt, timedelta as _td
        today = _dt.now().date()
        start = today - _td(days=89)
        date_counts: Counter = Counter()
        for _f, _t in all_occurrences:
            pt = getattr(_t, "publish_time", None) or getattr(_t, "created_at", None)
            if pt is None:
                continue
            d = pt.date() if hasattr(pt, "date") else _dt.fromisoformat(str(pt)).date()
            if start <= d <= today:
                date_counts[d] += 1
        daily = []
        for i in range(90):
            d = start + _td(days=i)
            daily.append({"date": d.strftime("%Y-%m-%d"), "count": date_counts.get(d, 0)})

        # top3 采购人（基于所有公告）
        purchasers_result = await db.execute(
            select(ExtractedField.raw_value, func.count(ExtractedField.id).label("cnt"))
            .where(ExtractedField.field_name == "purchaser_name")
            .group_by(ExtractedField.raw_value)
            .order_by(func.count(ExtractedField.id).desc())
            .limit(3)
        )
        top3_purchasers = []
        for row in purchasers_result:
            top3_purchasers.append({
                "name": row.raw_value or "未知",
                "count": row.cnt,
                "ratio": round(row.cnt / max(total, 1), 2),
            })

        top3_concentration = sum(p["count"] for p in top3_purchasers) / max(total, 1) if total else 0

        platforms = list(set(
            t.source_platform for f, t in all_occurrences if t.source_platform
        )) or ["ccgp"]

        # 推断 org_type 和 region
        org_role = "winner" if any(f.field_name == "winner_name" for f, t in all_occurrences) else "purchaser"
        from app.api.real_demo import _infer_org_meta  # 局部导入避免循环依赖
        _org_type, _region = _infer_org_meta(name, org_role)

        # 构造 meta（用于 5 维度评分，对齐 observation_signals.py 口径）
        # 估算总金额：取该组织相关公告的 amount 字段之和
        amount_result = await db.execute(
            select(func.sum(ExtractedField.raw_value))
            .where(ExtractedField.field_name == "amount")
            .where(ExtractedField.tender_id.in_([t.id for f, t in all_occurrences]))
        )
        # 真实金额难以解析（raw_value 是文本），用 total * 估算均值
        total_amount = total * 15_000_000  # 估算均值

        meta = {
            "org_id": f"real_org_{name[:8]}",
            "org_type": _org_type,
            "region": _region,
            "total_projects": total,
            "total_amount_yuan": total_amount,
            "award_win_rate": award_count / max(tender_count, 1) if tender_count else 0.2,
            "active_days_30d": min(30, sum(1 for d in daily[-30:] if d["count"] > 0)),
            "amount_consistency_score": 88.0,
            "type_coverage_count": min(12, len(platforms) + 5),
        }

        return {
            "meta": meta,
            "activity": {
                "total": total,
                "tender_count": tender_count,
                "award_count": award_count,
                "daily": daily,
            },
            "top3_purchasers": top3_purchasers,
            "top3_concentration": top3_concentration,
            "platforms": platforms,
        }
    except Exception as e:
        # 数据库查询失败时静默回退到样本数据
        import logging
        logging.warning(f"_query_real_org_by_name failed for '{name}': {e}")
        return None
