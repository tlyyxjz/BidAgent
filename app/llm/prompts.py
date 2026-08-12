"""LLM 意图解析 Prompt 模板。

命题 4 个示例：
1. {最近1个月}的{安徽省}区域内的{服务器}招标信息都有哪些
2. {2026年3月份}的{上海}区域内的{充电桩}招标信息都有哪些
3. {最近3个月}的{上海}区域内的{充电桩}招标信息都有哪些，请汇总后每天9:00发送给我
4. {2026年4月份}{上海}的{充电桩}招标信息都有哪些，请汇总后今天9:00发送给我

GPT-5.6 Sol 升级重点：Few-shot + 频率解析（"每天9:00"→cron / "今天9:00"→一次性触发）。
"""

from __future__ import annotations

import json

# 意图解析 System Prompt
INTENT_SYSTEM_PROMPT = """你是一个招投标信息查询意图解析助手。将用户的自然语言查询转化为结构化 JSON 过滤条件。

需要提取的字段：
- topic: 主题/关键词（如 服务器、充电桩、IT设备、医疗器械；也包含机构名/采购单位名，如"北京大学""某医院""某局"，机构名一律放 topic，不放 region）
- region: 地区（仅限省份/城市地名，如 上海、安徽、广东深圳；机构名不得填入 region）
- time_range: 时间范围（格式：7d/30d/3m/1y 分别表示最近 7 天/30 天/3 个月/1 年；或 ISO 日期范围如 2026-03-01~2026-03-31）
- frequency: 频率（用户要求推送的频率，如 "每天9:00" → "0 9 * * *"；"今天9:00" → "once:09:00"；"每周一" → "0 9 * * 1"；无频率留 null）
- trigger_type: 触发类型（"immediate" 立即查询 / "scheduled" 定时订阅，含频率时为 scheduled）
- industry: 行业领域（如 IT设备、医疗器械、建筑工程、办公用品，无法判断留 null）
- keywords: 关键词列表（项目名称中应包含的词，不含 topic 本身）
- notice_types: 公告类型列表（可选值：招标公告/中标公告/预告/变更/询价/谈判）
- min_budget / max_budget: 预算金额区间（数字，单位元）

只返回 JSON，不要任何解释。"""

# Few-shot 示例（命题 4 个示例 + 变体）
INTENT_FEWSHOT_EXAMPLES = [
    {
        "query": "最近1个月的安徽省区域内的服务器招标信息都有哪些",
        "result": {
            "topic": "服务器",
            "region": "安徽",
            "time_range": "1m",
            "frequency": None,
            "trigger_type": "immediate",
            "industry": "IT设备",
            "keywords": ["服务器"],
            "notice_types": ["招标公告"],
            "min_budget": None,
            "max_budget": None,
        },
    },
    {
        "query": "最近3个月的上海区域内的充电桩招标信息都有哪些，请汇总后每天9:00发送给我",
        "result": {
            "topic": "充电桩",
            "region": "上海",
            "time_range": "3m",
            "frequency": "0 9 * * *",
            "trigger_type": "scheduled",
            "industry": None,
            "keywords": ["充电桩"],
            "notice_types": ["招标公告"],
            "min_budget": None,
            "max_budget": None,
        },
    },
    {
        "query": "2026年4月份上海的充电桩招标信息都有哪些，请汇总后今天9:00发送给我",
        "result": {
            "topic": "充电桩",
            "region": "上海",
            "time_range": "2026-04-01~2026-04-30",
            "frequency": "once:09:00",
            "trigger_type": "scheduled",
            "industry": None,
            "keywords": ["充电桩"],
            "notice_types": ["招标公告"],
            "min_budget": None,
            "max_budget": None,
        },
    },
    {
        "query": "北京教育系统的中标公告 最近30天",
        "result": {
            "topic": "教育",
            "region": "北京",
            "time_range": "30d",
            "frequency": None,
            "trigger_type": "immediate",
            "industry": "教育",
            "keywords": ["教育"],
            "notice_types": ["中标公告"],
            "min_budget": None,
            "max_budget": None,
        },
    },
    {
        "query": "广东省医疗设备招标",
        "result": {
            "topic": "医疗设备",
            "region": "广东",
            "time_range": "30d",
            "frequency": None,
            "trigger_type": "immediate",
            "industry": "医疗器械",
            "keywords": ["医疗", "设备"],
            "notice_types": ["招标公告"],
            "min_budget": None,
            "max_budget": None,
        },
    },
]


def build_intent_prompt(query: str) -> str:
    """构建意图解析的 user prompt（含 Few-shot 示例）。

    M-2 修复：使用 json.dumps 输出标准 JSON，避免 Python repr 单引号。
    """
    examples_text = "\n".join(
        f"查询：{ex['query']}\n结果：{json.dumps(ex['result'], ensure_ascii=False)}"
        for ex in INTENT_FEWSHOT_EXAMPLES
    )
    return f"""参考以下示例：

{examples_text}

现在请解析这个查询：
用户查询：{query}

返回 JSON："""
