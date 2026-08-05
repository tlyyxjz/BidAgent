"""证据模块共用枚举常量（W2-05 / v4.1）。

从 `app.models.evidence` 拆出，集中存放 Sol 要求的各类枚举定义。
对应总规划 v4.1 第四章 4.8 / 4.9 + 第六章 6.1 / 6.2。
"""

from __future__ import annotations

# 抽取支持度（Sol 第四章 4.9 + 第六章 6.2）
SUPPORT_LEVELS = {
    "direct": "直接证据（原文精确出现）",
    "equivalent": "等价证据（规范化后匹配）",
    "inferred": "推导证据（L3/L4 匹配或确定性校验推导）",
    "unsupported": "无依据",
    "contradicted": "冲突证据",
}

# 字段状态（不修改现有枚举，W2-05 只用）
FIELD_STATUSES = {
    "present": "字段存在且有值",
    "absent": "字段不存在",
    "ambiguous": "字段存在但含义模糊",
    "multi_value": "多值字段",
}

# 证据角色（Sol 第四章 4.9）
EVIDENCE_ROLES = {
    "primary": "主证据",
    "context": "上下文证据",
    "qualifier": "限定条件证据",
    "derivation_input": "推导输入证据",
    "contradiction": "冲突证据",
}

# 匹配方法（W2-03 五级降级）
MATCH_METHODS = {
    "exact": "L1 精确匹配",
    "stripped": "L2 去空白匹配",
    "no_punct": "L3 去标点匹配",
    "substring": "L4 核心子串匹配",
    "not_found": "L5 未匹配",
}

# 交叉验证状态（v4.1 §4.8 6 态 enum）
CROSS_VERIFY_STATUSES = {
    "independent": "独立来源（不同平台不同发布主体）",
    "consistent_unknown": "一致但来源未知（同平台不同页面）",
    "same_origin": "同源转载（同一原始来源的不同转载）",
    "version_difference": "版本差异（同来源不同时间版本）",
    "conflict": "冲突（不同来源字段值不一致）",
    "single_source": "单源（仅一个来源，未交叉验证）",
}

# 来源质量类别（v4.1 §4.6 6 类）
SOURCE_QUALITY_TYPES = {
    "official_original": "官方原始（政府平台首发）",
    "official_repost": "官方转载（政府平台间转载）",
    "authorized_original": "授权原始（被授权的商业平台首发）",
    "commercial_repost": "商业转载（商业平台转载官方信息）",
    "index_only": "仅索引（仅提供索引链接，无正文）",
    "unknown": "未知",
}

# 字段类型（v4.1 §4.8）
FIELD_TYPES = {
    "amount": "金额类型",
    "date": "日期类型",
    "organization": "组织类型",
    "identifier": "标识符类型",
    "fact": "事实类型",
    "text": "文本类型",
}

# 六类核心字段（Sol 要求：不修改字段定义）
CORE_FIELDS = {
    "project_identifier": "项目编号",
    "purchaser_name": "采购人",
    "winner_name": "中标人",
    "amount": "金额及类型",
    "publish_date": "发布日期",
    "bid_deadline": "投标截止日期",
}

__all__ = [
    "CORE_FIELDS",
    "CROSS_VERIFY_STATUSES",
    "EVIDENCE_ROLES",
    "FIELD_STATUSES",
    "FIELD_TYPES",
    "MATCH_METHODS",
    "SOURCE_QUALITY_TYPES",
    "SUPPORT_LEVELS",
]
