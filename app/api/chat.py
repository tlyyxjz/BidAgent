"""聊天 API（W2-06 Demo 用，Mock 数据）。

用于 Demo 视频展示 6 Agent 协作流程。
注意：本文件仅提供 mock 接口，不调用真实 Agent 流水线。
真实 Agent 接口在 app/api/agents.py。
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    """聊天请求。"""
    message: str


class ChatResponse(BaseModel):
    """聊天响应。"""
    reply: str
    slots: dict[str, str]
    agents: list[dict[str, Any]]


def _mock_parse_slots(message: str) -> dict[str, str]:
    """Mock 5 槽位解析。"""
    slots = {"keyword": "", "region": "", "time_range": "", "industry": "", "notice_type": ""}

    if "上海" in message:
        slots["region"] = "上海市"
    elif "北京" in message:
        slots["region"] = "北京市"
    elif "广东" in message or "广州" in message or "深圳" in message:
        slots["region"] = "广东省"
    elif "浙江" in message or "杭州" in message:
        slots["region"] = "浙江省"
    else:
        slots["region"] = "全国"

    if "7天" in message or "一周" in message:
        slots["time_range"] = "最近 7 天"
    elif "30天" in message or "一个月" in message:
        slots["time_range"] = "最近 30 天"
    elif "今天" in message or "今日" in message:
        slots["time_range"] = "今日"
    else:
        slots["time_range"] = "最近 15 天"

    if "IT" in message or "信息化" in message or "软件" in message:
        slots["keyword"] = "IT / 信息化"
        slots["industry"] = "信息技术"
    elif "医疗" in message or "医院" in message or "设备" in message:
        slots["keyword"] = "医疗设备"
        slots["industry"] = "医疗卫生"
    elif "教育" in message or "学校" in message or "大学" in message:
        slots["keyword"] = "教育系统"
        slots["industry"] = "教育"
    elif "基建" in message or "工程" in message or "建筑" in message:
        slots["keyword"] = "基建工程"
        slots["industry"] = "建筑工程"
    else:
        slots["keyword"] = message[:20]
        slots["industry"] = "综合"

    if "中标" in message or "成交" in message:
        slots["notice_type"] = "中标公告"
    elif "更正" in message:
        slots["notice_type"] = "更正公告"
    else:
        slots["notice_type"] = "招标公告"

    return slots


@router.post("")
async def chat(payload: ChatRequest) -> dict[str, Any]:
    """聊天接口（Mock）。

    返回 5 槽位解析结果 + 6 Agent 状态列表。
    真实流式响应待接入 SSE。
    """
    slots = _mock_parse_slots(payload.message)
    count = random.randint(10, 39)

    agents = [
        {"id": "intent", "name": "意图理解 Agent", "status": "done", "progress": 100},
        {"id": "collector", "name": "数据采集 Agent", "status": "done", "progress": 100},
        {"id": "processor", "name": "清洗抽取 Agent", "status": "done", "progress": 100},
        {"id": "quality", "name": "质量校验 Agent", "status": "done", "progress": 100},
        {"id": "report", "name": "报告生成 Agent", "status": "done", "progress": 100},
        {"id": "delivery", "name": "交付推送 Agent", "status": "done", "progress": 100},
    ]

    reply = (
        f"✅ 已解析你的需求，共找到 {count} 条相关招标公告。\n\n"
        f"📋 5 槽位解析结果：\n"
        f"  关键词：{slots['keyword']}\n"
        f"  地区：{slots['region']}\n"
        f"  时间范围：{slots['time_range']}\n"
        f"  行业：{slots['industry']}\n"
        f"  公告类型：{slots['notice_type']}\n\n"
        f"📄 Word 分析报告已生成，请在右侧下载。"
    )

    return {
        "code": 200,
        "data": ChatResponse(
            reply=reply,
            slots=slots,
            agents=agents,
        ).model_dump(),
        "msg": "ok",
    }
