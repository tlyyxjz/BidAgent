"""LLM 意图解析的数据模型。

命题硬要求：识别主题/关键词、区域、时间范围、频率。
保留旧字段兼容（industry/date_range），新增命题字段（topic/time_range/frequency/trigger_type）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ParsedFilters(BaseModel):
    """LLM 解析出的结构化过滤条件。

    命题 4 个示例覆盖 5 个槽位：
    - topic（主题）: 服务器 / 充电桩 / IT设备
    - region（地区）: 安徽 / 上海
    - time_range（时间范围）: 最近1个月 / 2026年3月份 / 最近3个月
    - frequency（频率）: 每天9:00 / 今天9:00 / 每周一
    - trigger_type（触发类型）: immediate / scheduled
    """

    # ==== 命题 5 槽位（GPT-5.6 Sol 升级重点）====
    topic: str | None = Field(None, description="主题/关键词，如 服务器/充电桩/IT设备")
    region: str | None = Field(None, description="地区，如 上海/安徽/广东深圳")
    time_range: str | None = Field(None, description="时间范围，如 7d/30d/3m/1y 或 ISO 日期")
    frequency: str | None = Field(None, description="频率，cron 表达式或自然语言（每天9:00）")
    trigger_type: str = Field("immediate", description="触发类型：immediate/scheduled")

    # ==== 扩展字段（保留兼容，GPT-5.6 Sol 可清理）====
    industry: str | None = Field(None, description="行业（兼容字段）")
    date_range: str | None = Field(None, description="时间范围（兼容字段，同 time_range）")
    keywords: list[str] = Field(default_factory=list, description="关键词列表")
    notice_types: list[str] = Field(default_factory=list, description="公告类型列表")
    min_budget: float | None = Field(None, description="最小预算金额（元）")
    max_budget: float | None = Field(None, description="最大预算金额（元）")

    # ==== 元数据 ====
    raw_query: str = Field(..., description="用户原始查询")


class IntentParseResult(BaseModel):
    """LLM 意图解析的完整结果（含元信息）。"""

    filters: ParsedFilters
    provider: str = "unknown"  # deepseek / dashscope / keyword_fallback
    cached: bool = False
    parse_time_ms: int = 0
