"""组织实体公开活动观察信号：常量与数据结构（v4.1 第九章）。

从 observation_signals.py 拆分而来，承载：
- 信号名称常量（v4.1 第 9.2 节）
- 严谨表述常量（v4.1 第 9.3 节）
- ObservationSignal / ObservationResult 数据结构
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.models.organization import SupplierProfile


# ========== 信号名称常量（v4.1 第 9.2 节）==========

SIGNAL_AWARD_ACTIVITY = "中标活跃度"
SIGNAL_AWARD_CONCENTRATION = "公开中标集中度"
SIGNAL_CANCELLATION_LINK = "废标公告关联"
SIGNAL_EXPLICIT_REJECTION = "明确投标否决"
SIGNAL_INFO_CONFLICT = "信息冲突观察"
SIGNAL_HIGH_FREQ_COOCCURRENCE = "高频共现提示"  # 选做

# 所有 MVP 信号列表
ALL_MVP_SIGNALS = [
    SIGNAL_AWARD_ACTIVITY,
    SIGNAL_AWARD_CONCENTRATION,
    SIGNAL_CANCELLATION_LINK,
    SIGNAL_EXPLICIT_REJECTION,
    SIGNAL_INFO_CONFLICT,
    SIGNAL_HIGH_FREQ_COOCCURRENCE,
]

# 必做信号（5 个，高频共现为选做）
REQUIRED_SIGNALS = ALL_MVP_SIGNALS[:5]


# ========== 严谨表述常量（v4.1 第 9.3 节）==========

STRICT_TERM_BID_COUNT = "公开公告中观察到的投标出现次数"
FORBIDDEN_TERM_BID_COUNT = "企业实际投标次数"

STRICT_TERM_CONCENTRATION = "当前覆盖公开中标记录中的采购人集中度"
FORBIDDEN_TERM_CONCENTRATION = "企业客户集中度"

COOCCURRENCE_DISCLAIMER = (
    "高频共现可能由行业集中度、区域市场和项目准入条件等多种因素造成。"
    "仅凭共现不能判断企业关联关系或围标行为。"
)


# ========== 数据结构 ==========

@dataclass
class ObservationSignal:
    """单个观察信号结果。"""
    signal_name: str          # 信号名称（v4.1 第 9.2 节）
    observed_value: float     # 观察到的数值
    observation_period: str   # 观察时间范围描述
    coverage_note: str        # 覆盖说明（v4.1 第 9.4 节）
    details: dict = field(default_factory=dict)  # 详细数据
    disclaimer: str = ""      # 严谨表述免责声明（v4.1 第 9.3 节）


@dataclass
class ObservationResult:
    """组织实体公开活动观察信号汇总结果。"""
    organization_id: str
    normalized_name: str
    # 数据完整性展示（v4.1 第 9.4 节）
    coverage_platforms: list = field(default_factory=list)      # 覆盖平台
    coverage_time_range: str = ""                               # 覆盖时间
    valid_notice_count: int = 0                                 # 有效公告数量
    bidder_list_notice_count: int = 0                          # 包含投标人名单的公告数量
    entity_resolution_status: str = "unresolved"               # 企业消歧状态
    possible_omissions: str = ""                               # 可能的遗漏
    signal_caliber: str = ""                                   # 信号计算口径
    # 六个 MVP 信号
    signals: list = field(default_factory=list)  # list[ObservationSignal]
    # 供应商画像
    profile: Optional[SupplierProfile] = None
    # 分析时间
    analyzed_at: str = ""
    # 总结
    summary: str = ""
