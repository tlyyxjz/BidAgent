"""Prompt 构建工具函数。

从 extractor.py 拆分而来，包含 prompt 哈希计算和 user prompt 构建函数。
"""
from __future__ import annotations

import hashlib
import json

from app.llm.extractor_prompts import (
    EXTRACTION_FEWSHOT_EXAMPLES,
    EXTRACTION_FEWSHOT_EXAMPLES_NO_EVIDENCE,
    EXTRACTION_SYSTEM_PROMPT,
    EXTRACTION_SYSTEM_PROMPT_NO_EVIDENCE,
)

def compute_prompt_hash(
    system_prompt: str = None,
    fewshot_examples: list = None,
) -> str:
    """计算 prompt 哈希（Sol 要求：prompt 变更需记录 prompt_hash）。

    Args:
        system_prompt: system prompt，默认 EXTRACTION_SYSTEM_PROMPT
        fewshot_examples: few-shot 示例列表，默认 EXTRACTION_FEWSHOT_EXAMPLES

    Returns:
        SHA256 哈希（前 16 字符）
    """
    if system_prompt is None:
        system_prompt = EXTRACTION_SYSTEM_PROMPT
    if fewshot_examples is None:
        fewshot_examples = EXTRACTION_FEWSHOT_EXAMPLES
    content = system_prompt + json.dumps(fewshot_examples, ensure_ascii=False)
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


def build_extraction_prompt_no_evidence(raw_text: str) -> str:
    """构建无证据版本的 user prompt（A 组消融实验专用）。

    Args:
        raw_text: 公告原文

    Returns:
        user prompt 字符串（few-shot 不含 candidate_evidences）
    """
    examples_text = "\n\n".join(
        f"公告原文：\n{ex['raw_text']}\n\n输出 JSON：\n{json.dumps(ex['result'], ensure_ascii=False)}"
        for ex in EXTRACTION_FEWSHOT_EXAMPLES_NO_EVIDENCE
    )
    return f"""参考以下示例：

{examples_text}

现在请抽取这个公告的字段：
公告原文：
{raw_text}

输出 JSON："""
