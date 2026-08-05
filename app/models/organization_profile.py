"""供应商公开活动画像生成（从 organization.py 拆出）。

对应总规划 v4.1 第四章 4.5 + 第八章「基础组织实体活动画像」。

职责：
- SupplierProfile 数据类：供应商画像结构
- build_supplier_profile：基于历史中标记录生成供应商画像
"""
from __future__ import annotations

from dataclasses import dataclass


# ========== 供应商画像生成 ==========

@dataclass
class SupplierProfile:
    """供应商公开活动画像（v4.1 第四章 4.5 + 第八章）。

    基于历史中标记录生成。
    """
    organization_id: str
    normalized_name: str
    # 中标总次数
    win_count: int = 0
    # 累计中标金额（元）
    total_win_amount: float = 0.0
    # 主要采购人（中标项目的采购人）
    main_purchasers: list = None
    # 主要代理机构
    main_agencies: list = None
    # 业务领域（基于中标项目名称聚类）
    business_areas: list = None
    # 活跃地区
    active_regions: list = None
    # 首次中标时间
    first_win_date: str = ""
    # 最近中标时间
    last_win_date: str = ""
    # 画像生成时间
    profile_generated_at: str = ""

    def __post_init__(self):
        if self.main_purchasers is None:
            self.main_purchasers = []
        if self.main_agencies is None:
            self.main_agencies = []
        if self.business_areas is None:
            self.business_areas = []
        if self.active_regions is None:
            self.active_regions = []


def build_supplier_profile(
    organization_id: str,
    normalized_name: str,
    win_records: list,
) -> SupplierProfile:
    """生成供应商画像。

    Args:
        organization_id: 组织 ID
        normalized_name: 规范化名称
        win_records: 中标记录列表，每个元素是 dict:
            {
                "win_amount": float,
                "purchaser_name": str,
                "agency_name": str,
                "project_name": str,
                "region": str,
                "win_date": str,
            }

    Returns:
        SupplierProfile
    """
    profile = SupplierProfile(
        organization_id=organization_id,
        normalized_name=normalized_name,
        win_count=len(win_records),
    )

    if not win_records:
        from datetime import datetime, timezone
        profile.profile_generated_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        return profile

    amounts = []
    purchasers = []
    agencies = []
    regions = []
    dates = []

    for rec in win_records:
        # 金额
        try:
            amt = float(rec.get("win_amount", 0) or 0)
            amounts.append(amt)
        except (ValueError, TypeError):
            pass
        # 采购人
        if rec.get("purchaser_name"):
            purchasers.append(rec["purchaser_name"])
        # 代理机构
        if rec.get("agency_name"):
            agencies.append(rec["agency_name"])
        # 地区
        if rec.get("region"):
            regions.append(rec["region"])
        # 日期
        if rec.get("win_date"):
            dates.append(rec["win_date"])

    # 累计金额
    profile.total_win_amount = sum(amounts) if amounts else 0.0

    # 主要采购人/代理机构（按出现频次 top 5）
    from collections import Counter
    profile.main_purchasers = [name for name, _ in Counter(purchasers).most_common(5)]
    profile.main_agencies = [name for name, _ in Counter(agencies).most_common(5)]
    profile.active_regions = [name for name, _ in Counter(regions).most_common(5)]

    # 首次/最近中标时间
    if dates:
        dates.sort()
        profile.first_win_date = dates[0]
        profile.last_win_date = dates[-1]

    from datetime import datetime, timezone
    profile.profile_generated_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

    return profile
