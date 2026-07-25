"""W2-01 LLM 字段抽取 prompt + 调用逻辑。

对应总规划 v4.1 第六章 6.1 工作流程「LLM 输出字段值和候选证据列表」。

需求：
1. 修改 LLM 抽取 prompt，要求模型同时输出：
   - 六类核心字段值
   - 每个字段值对应的候选证据文本片段（1～3 段）
   - 证据角色标注（primary / context / qualifier）
2. 输出格式为标准 JSON，符合 backend/schemas.py 中的 Schema
3. 候选证据文本必须是原文中的连续片段，不得改写
4. 记录模型标识、参数、token、延迟

约束：
- 不修改六类字段定义
- 不修改字段状态枚举
- prompt 变更需记录 prompt_hash
- 失败时记录错误，不静默丢弃
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Optional

import httpx

from app.config import settings
from app.llm.extraction_schemas import (
    CORE_FIELD_NAMES,
    EXTRACTION_AMOUNT_TYPES,
    EXTRACTION_EVIDENCE_ROLES,
    EXTRACTION_FIELD_STATUSES,
    CandidateEvidence,
    ExtractionResult,
    FieldExtraction,
)
from app.utils.logger import get_logger

logger = get_logger("llm_extractor")

# ========== W2-01 抽取 System Prompt ==========

EXTRACTION_SYSTEM_PROMPT = """你是一个政府采购公告字段抽取助手。从公告原文中抽取六类核心字段，并为每个字段提供候选证据。

需要抽取的六类核心字段：
1. project_identifier：项目编号（招标编号、政府采购计划编号）
2. purchaser_name：采购人（招标人、项目业主）
3. winner_name：中标人（中标公司、成交供应商）
4. amount：金额及类型（预算金额/控制价/中标金额/合同金额/单价）
5. publish_date：发布日期（公告发布时间）
6. bid_deadline：投标截止日期（投标文件递交截止时间）

输出要求：
1. 每个字段必须输出 field_status：
   - present：字段存在且有值
   - absent：字段不存在（如招标公告没有中标人）
   - ambiguous：字段存在但含义模糊
   - multi_value：多值字段（如多分包、多中标人）

2. 每个字段必须提供候选证据（1～3 段）：
   - evidence_text：原文中的连续片段，不得改写
   - role：证据角色
     * primary：主证据（直接证明字段值）
     * context：上下文证据（提供背景信息）
     * qualifier：限定条件证据（如金额类型、币种）

3. amount 字段必须输出：
   - amount_type：金额类型（budget/ceiling/award/contract/unit_price）
   - currency：货币（CNY/USD/EUR）

4. 多值字段（multi_value）：
   - 如多分包金额，每个分包单独输出一条
   - 如多中标人，每个中标人单独输出一条

输出格式为标准 JSON：
{
  "fields": [
    {
      "field_name": "project_identifier",
      "field_status": "present",
      "raw_value": "ZFCG-2026-001",
      "amount_type": null,
      "currency": null,
      "lot_id": null,
      "candidate_evidences": [
        {"evidence_text": "一、项目编号：ZFCG-2026-001", "role": "primary"}
      ]
    }
  ]
}

约束：
- 候选证据文本必须是原文中的连续片段，不得改写、不得翻译、不得概括
- 字段不存在的字段也要输出，field_status=absent
- 不得编造原文中不存在的内容
- 只返回 JSON，不要任何解释"""

# Few-shot 示例（Sol 要求：LLM few-shot 必须用 json.dumps 输出标准 JSON）
EXTRACTION_FEWSHOT_EXAMPLES = [
    {
        "raw_text": "招标公告\n项目编号：ZFCG-2026-001\n项目名称：政府采购服务器项目\n预算金额：100.00万元\n采购人：某机关单位\n投标截止时间：2026年8月1日 09:00\n发布日期：2026年7月15日",
        "result": {
            "fields": [
                {
                    "field_name": "project_identifier",
                    "field_status": "present",
                    "raw_value": "ZFCG-2026-001",
                    "amount_type": None,
                    "currency": None,
                    "lot_id": None,
                    "candidate_evidences": [
                        {"evidence_text": "项目编号：ZFCG-2026-001", "role": "primary"}
                    ],
                },
                {
                    "field_name": "purchaser_name",
                    "field_status": "present",
                    "raw_value": "某机关单位",
                    "amount_type": None,
                    "currency": None,
                    "lot_id": None,
                    "candidate_evidences": [
                        {"evidence_text": "采购人：某机关单位", "role": "primary"}
                    ],
                },
                {
                    "field_name": "winner_name",
                    "field_status": "absent",
                    "raw_value": None,
                    "amount_type": None,
                    "currency": None,
                    "lot_id": None,
                    "candidate_evidences": [],
                },
                {
                    "field_name": "amount",
                    "field_status": "present",
                    "raw_value": "100.00万元",
                    "amount_type": "budget",
                    "currency": "CNY",
                    "lot_id": None,
                    "candidate_evidences": [
                        {"evidence_text": "预算金额：100.00万元", "role": "primary"}
                    ],
                },
                {
                    "field_name": "publish_date",
                    "field_status": "present",
                    "raw_value": "2026年7月15日",
                    "amount_type": None,
                    "currency": None,
                    "lot_id": None,
                    "candidate_evidences": [
                        {"evidence_text": "发布日期：2026年7月15日", "role": "primary"}
                    ],
                },
                {
                    "field_name": "bid_deadline",
                    "field_status": "present",
                    "raw_value": "2026年8月1日 09:00",
                    "amount_type": None,
                    "currency": None,
                    "lot_id": None,
                    "candidate_evidences": [
                        {"evidence_text": "投标截止时间：2026年8月1日 09:00", "role": "primary"}
                    ],
                },
            ]
        },
    }
]


def compute_prompt_hash() -> str:
    """计算 prompt 哈希（Sol 要求：prompt 变更需记录 prompt_hash）。

    Returns:
        SHA256 哈希（前 16 字符）
    """
    content = EXTRACTION_SYSTEM_PROMPT + json.dumps(
        EXTRACTION_FEWSHOT_EXAMPLES, ensure_ascii=False
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def build_extraction_prompt(raw_text: str) -> str:
    """构建字段抽取的 user prompt（含 Few-shot 示例）。

    Args:
        raw_text: 公告原文

    Returns:
        user prompt 字符串
    """
    examples_text = "\n\n".join(
        f"公告原文：\n{ex['raw_text']}\n\n输出 JSON：\n{json.dumps(ex['result'], ensure_ascii=False)}"
        for ex in EXTRACTION_FEWSHOT_EXAMPLES
    )
    return f"""参考以下示例：

{examples_text}

现在请抽取这个公告的字段：
公告原文：
{raw_text}

输出 JSON："""


# ========== 响应解析与校验 ==========


def _validate_extraction(data: dict[str, Any]) -> None:
    """校验 LLM 输出是否符合 Schema。

    Raises:
        ValueError: 校验失败
    """
    if "fields" not in data:
        raise ValueError("LLM 输出缺少 fields 字段")

    fields = data["fields"]
    if not isinstance(fields, list):
        raise ValueError("fields 必须是列表")

    if len(fields) == 0:
        raise ValueError("fields 不能为空")

    for i, field_data in enumerate(fields):
        if "field_name" not in field_data:
            raise ValueError(f"fields[{i}] 缺少 field_name")

        field_name = field_data["field_name"]
        if field_name not in CORE_FIELD_NAMES:
            raise ValueError(
                f"fields[{i}] 非法 field_name: {field_name}，合法值: {CORE_FIELD_NAMES}"
            )

        field_status = field_data.get("field_status", "present")
        if field_status not in EXTRACTION_FIELD_STATUSES:
            raise ValueError(
                f"fields[{i}] 非法 field_status: {field_status}，合法值: {EXTRACTION_FIELD_STATUSES}"
            )

        # amount 字段的 amount_type 校验
        if field_name == "amount":
            amount_type = field_data.get("amount_type")
            if amount_type and amount_type not in EXTRACTION_AMOUNT_TYPES:
                raise ValueError(
                    f"fields[{i}] 非法 amount_type: {amount_type}，合法值: {EXTRACTION_AMOUNT_TYPES}"
                )

        # 候选证据校验
        evidences = field_data.get("candidate_evidences", [])
        for j, ev in enumerate(evidences):
            if "evidence_text" not in ev:
                raise ValueError(
                    f"fields[{i}].candidate_evidences[{j}] 缺少 evidence_text"
                )
            role = ev.get("role", "primary")
            if role not in EXTRACTION_EVIDENCE_ROLES:
                raise ValueError(
                    f"fields[{i}].candidate_evidences[{j}] 非法 role: {role}"
                )


def parse_extraction_response(
    data: dict[str, Any], model_id: str, latency_ms: int, total_tokens: int = 0
) -> ExtractionResult:
    """解析 LLM 抽取响应。

    Args:
        data: LLM 返回的 JSON dict
        model_id: 模型标识
        latency_ms: 延迟毫秒
        total_tokens: 总 token 数

    Returns:
        ExtractionResult

    Raises:
        ValueError: 解析或校验失败
    """
    _validate_extraction(data)

    fields = []
    for field_data in data["fields"]:
        evidences = [
            CandidateEvidence(
                evidence_text=ev["evidence_text"],
                role=ev.get("role", "primary"),
            )
            for ev in field_data.get("candidate_evidences", [])
        ]
        field_ext = FieldExtraction(
            field_name=field_data["field_name"],
            field_status=field_data.get("field_status", "present"),
            raw_value=field_data.get("raw_value"),
            amount_type=field_data.get("amount_type"),
            currency=field_data.get("currency"),
            lot_id=field_data.get("lot_id"),
            candidate_evidences=evidences,
        )
        fields.append(field_ext)

    return ExtractionResult(
        fields=fields,
        model_id=model_id,
        prompt_hash=compute_prompt_hash(),
        total_tokens=total_tokens,
        latency_ms=latency_ms,
    )


# ========== LLM 调用 ==========


async def call_extraction_llm(raw_text: str) -> ExtractionResult:
    """调用 LLM 抽取字段 + 候选证据。

    优先级：LLM API → 抛异常（W2-01 不做关键词降级，由调用方处理）

    Args:
        raw_text: 公告原文

    Returns:
        ExtractionResult

    Raises:
        RuntimeError: LLM 调用失败
        ValueError: 响应解析失败
    """
    if not settings.DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY not configured")

    start_time = time.perf_counter()
    model_id = settings.LLM_MODEL

    headers = {
        "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.LLM_MODEL,
        "messages": [
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": build_extraction_prompt(raw_text)},
        ],
        "temperature": 0.1,
        "max_tokens": 2000,
        "response_format": {"type": "json_object"},
    }

    try:
        async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{settings.DEEPSEEK_BASE_URL}/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        content = data["choices"][0]["message"]["content"]
        parsed_json = json.loads(content)
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        total_tokens = data.get("usage", {}).get("total_tokens", 0)

        result = parse_extraction_response(parsed_json, model_id, latency_ms, total_tokens)

        logger.info(
            "LLM extraction success model={} fields={} tokens={} latency={}ms",
            model_id,
            len(result.fields),
            result.total_tokens,
            result.latency_ms,
        )
        return result

    except Exception as exc:
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        logger.error(
            "LLM extraction failed model={} latency={}ms error={}",
            model_id,
            latency_ms,
            str(exc),
        )
        # Sol 要求：失败时记录错误，不静默丢弃
        return ExtractionResult(
            fields=[],
            model_id=model_id,
            prompt_hash=compute_prompt_hash(),
            total_tokens=0,
            latency_ms=latency_ms,
            error=str(exc),
        )
