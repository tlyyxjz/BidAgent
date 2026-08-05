"""LLM 响应解析与校验。

从 extractor.py 拆分而来，包含响应校验、解析和 display_grade 填充逻辑。
"""
from __future__ import annotations

import json
from typing import Any

from app.llm.extraction_schemas import (
    CORE_FIELD_NAMES,
    EXTRACTION_AMOUNT_TYPES,
    EXTRACTION_EVIDENCE_ROLES,
    EXTRACTION_FIELD_STATUSES,
    CandidateEvidence,
    ExtractionResult,
    FieldExtraction,
)
from app.llm.extractor_prompt_builder import compute_prompt_hash
from app.processors.display_grade import compute_display_grade
from app.utils.logger import get_logger

logger = get_logger("llm_extractor")

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
        # W2-10 修复：per-field try/except 容错，单字段失败不影响其他字段
        try:
            evidences = [
                CandidateEvidence(
                    evidence_text=ev["evidence_text"],
                    role=ev.get("role", "primary"),
                )
                for ev in field_data.get("candidate_evidences", [])
            ]
            # W2-10 修复：raw_value 归一化，dict/list 转 JSON 字符串，其他非 str 转 str
            raw_value = field_data.get("raw_value")
            if isinstance(raw_value, (dict, list)):
                raw_value = json.dumps(raw_value, ensure_ascii=False)
            elif raw_value is not None and not isinstance(raw_value, str):
                raw_value = str(raw_value)
            # v4.1 sec 7.2: amount object 4 new keys (LLM outputs, program may override normalized_value)
            normalized_value = field_data.get("normalized_value")
            if isinstance(normalized_value, (dict, list)):
                normalized_value = json.dumps(normalized_value, ensure_ascii=False)
            elif normalized_value is not None and not isinstance(normalized_value, str):
                normalized_value = str(normalized_value)
            field_ext = FieldExtraction(
                field_name=field_data["field_name"],
                field_status=field_data.get("field_status", "present"),
                raw_value=raw_value,
                amount_type=field_data.get("amount_type"),
                currency=field_data.get("currency"),
                lot_id=field_data.get("lot_id"),
                normalized_value=normalized_value,
                original_unit=field_data.get("original_unit"),
                tax_status=field_data.get("tax_status"),
                display_precision=field_data.get("display_precision"),
                candidate_evidences=evidences,
            )
            fields.append(field_ext)
        except Exception as exc:
            logger.warning(
                "parse field failed name={} error={}",
                field_data.get("field_name", "<unknown>"),
                exc,
            )

    return ExtractionResult(
        fields=fields,
        model_id=model_id,
        prompt_hash=compute_prompt_hash(),
        total_tokens=total_tokens,
        latency_ms=latency_ms,
    )


# ========== W3-07 display_grade 接入 ==========

def _populate_display_grades(
    result: ExtractionResult,
    source_role: str = "official_original",
) -> None:
    """原地为 result.fields 计算并写入 display_grade（调用方已设 support_level/cross_verified）.

    说明：
      - 当前 LLM 抽取阶段无法判断来源交叉验证，cross_verified 默认取字段原值。
      - display_grade 只基于当前字段的 support_level + source_role + cross_verified + field_status
        进行纯函数计算，不与数据库绑定。
    """
    for field in result.fields:
        field.display_grade = compute_display_grade(
            support_level=field.support_level,
            source_role=source_role,
            cross_verified=field.cross_verified,
            field_status=field.field_status,
        )
