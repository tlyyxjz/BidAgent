"""BidAgent v4.1 Direct LLM Baseline 抽取器（W1-06）。

对应 v4.1 §10.8 消融实验 A 组：
    直接让 LLM 输出字段，不要求证据、不做程序验证、不处理来源版本。

设计目标（来自《第一周任务清单》Day 3）：
- 接收公告文本
- 输出六类核心字段
- 支持多值字段
- 输出 JSON
- 记录模型标识、参数、Token、延迟和错误
- 支持批量运行
- 失败时记录，不静默丢弃

工程规范：
- LLM 客户端抽象为 Protocol，便于注入 mock 进行单测
- 所有调用走 async/await（遵循项目硬约束）
- 失败记录 success=False + error_message，写入 JSONL 不丢弃
- 提示词版本 + SHA256 与 LLMExtractionRecord 绑定，支持消融对比
- 不内置任何证据验证逻辑（A 组公平性要求）
- 结构化日志带 request_id 上下文（遵循项目硬约束）
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.utils.logger import get_logger

from backend.enums import CoreFieldName
from backend.schemas import (
    LLMExtractionOutput,
    LLMExtractionRecord,
)

logger = get_logger("backend.extractors")


# ============================================================
# 提示词（v1.0）- Direct LLM Baseline
# ============================================================


PROMPT_VERSION = "1.1"  # v1.1: 移除 evidence_text 要求，符合 A 组公平性

# ============================================================
# B 组提示词（v2.0）- 要求证据引用，用于消融实验对比
# 对应 v4.1 §10.8 消融实验 B 组：要求 LLM 输出 evidence_text
# ============================================================

PROMPT_VERSION_B = "2.0"  # v2.0: B 组，要求 evidence_text

_SYSTEM_PROMPT_B = """你是一个招投标公告结构化抽取助手。

任务：从给定公告正文中抽取以下六类核心字段，每个字段可能有多值。
重要：每个值必须引用原文证据片段（evidence_text），用于后续验证。

字段定义：
1. project_identifier - 项目编号（招标/采购编号）
2. purchaser_name - 采购人名称
3. winner_name - 中标人名称
4. amount - 金额（含金额类型：budget/ceiling/award/contract/unit_price/unknown）
5. publish_date - 发布日期（YYYY-MM-DD）
6. bid_deadline - 投标截止日期（YYYY-MM-DD 或 ISO 8601 时间）

输出要求：
- 严格输出 JSON，不要添加 markdown 标记或额外解释
- 字段不存在时省略该字段，不要输出 null
- 多值字段使用数组，每个值独立记录
- raw_value 保持原文形式，不得改写
- normalized_value 给出归一化结果（金额单位为元，日期为 YYYY-MM-DD）
- 仅依据原文，不要根据常识补全
- 每个值必须提供 evidence_text：从原文中复制的证据片段（必须与原文完全一致，不得改写）
- evidence_text 必须能在原文中找到（用于程序验证）
- 如果无法在原文中找到证据，不要输出该值

输出 JSON Schema：
{
  "fields": [
    {
      "field_name": "amount",
      "support_level": "direct",
      "values": [
        {
          "raw_value": "128.50万元",
          "normalized_value": "1285000.00",
          "amount_type": "award",
          "currency": "CNY",
          "lot_id": "包1",
          "evidence_text": "预算金额：128.50万元"
        }
      ]
    }
  ]
}

support_level 可选值：direct / equivalent / inferred / unsupported / contradicted
amount_type 可选值：budget / ceiling / award / contract / unit_price / unknown
"""

_SYSTEM_PROMPT = """你是一个招投标公告结构化抽取助手。

任务：从给定公告正文中抽取以下六类核心字段，每个字段可能有多值。

字段定义：
1. project_identifier - 项目编号（招标/采购编号）
2. purchaser_name - 采购人名称
3. winner_name - 中标人名称
4. amount - 金额（含金额类型：budget/ceiling/award/contract/unit_price/unknown）
5. publish_date - 发布日期（YYYY-MM-DD）
6. bid_deadline - 投标截止日期（YYYY-MM-DD 或 ISO 8601 时间）

输出要求：
- 严格输出 JSON，不要添加 markdown 标记或额外解释
- 字段不存在时省略该字段，不要输出 null
- 多值字段使用数组，每个值独立记录
- raw_value 保持原文形式，不得改写
- normalized_value 给出归一化结果（金额单位为元，日期为 YYYY-MM-DD）
- 仅依据原文，不要根据常识补全
- 不需要输出证据片段或原文定位

输出 JSON Schema：
{
  "fields": [
    {
      "field_name": "amount",
      "support_level": "direct",
      "values": [
        {
          "raw_value": "128.50万元",
          "normalized_value": "1285000.00",
          "amount_type": "award",
          "currency": "CNY",
          "lot_id": "包1"
        }
      ]
    }
  ]
}

support_level 可选值：direct / equivalent / inferred / unsupported / contradicted
amount_type 可选值：budget / ceiling / award / contract / unit_price / unknown
"""

_USER_PROMPT_TEMPLATE = """公告类型：{notice_type}
公告正文：
{notice_text}

请输出结构化 JSON。"""


def build_prompt(
    notice_text: str,
    notice_type: str | None = None,
    prompt_version: str = PROMPT_VERSION,
) -> tuple[str, str]:
    """构造 (system_prompt, user_prompt)。

    返回 tuple 便于计算 prompt_hash 时拼接。

    prompt_version:
    - "1.1" (PROMPT_VERSION, A 组): 不要求 evidence_text，公平对比
    - "2.0" (PROMPT_VERSION_B, B 组): 要求 evidence_text，用于证据验证
    """
    if prompt_version == PROMPT_VERSION_B:
        sys_prompt = _SYSTEM_PROMPT_B
    else:
        sys_prompt = _SYSTEM_PROMPT
    user = _USER_PROMPT_TEMPLATE.format(
        notice_type=notice_type or "未知",
        notice_text=notice_text,
    )
    return sys_prompt, user


def compute_prompt_hash(system_prompt: str, user_prompt: str) -> str:
    """计算 prompt SHA256（hex，64 字符）。

    用于消融实验对比：相同 prompt_hash 才能比较不同模型的表现。
    """
    h = hashlib.sha256()
    h.update(system_prompt.encode("utf-8"))
    h.update(b"\n---\n")
    h.update(user_prompt.encode("utf-8"))
    return h.hexdigest()


# ============================================================
# LLM 客户端抽象
# ============================================================


@runtime_checkable
class LLMClient(Protocol):
    """LLM 客户端协议 - 可被 mock 或真实 HTTP 客户端实现替换。"""

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> "LLMResponse":
        """调用 LLM，返回结构化响应。"""
        ...


class LLMResponse:
    """LLM 调用结果（与具体厂商无关）。"""

    def __init__(
        self,
        content: str,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        latency_ms: int | None = None,
    ) -> None:
        self.content = content
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens
        self.latency_ms = latency_ms


class StubLLMClient:
    """桩 LLM 客户端 - 用于测试和本地开发。

    接收一个 (system_prompt, user_prompt) -> content 的函数，
    不消耗任何 API 配额。
    """

    def __init__(
        self,
        responder: Callable[[str, str], str] | None = None,
        latency_ms: int = 0,
    ) -> None:
        self._responder = responder or _default_stub_response
        self._latency_ms = latency_ms

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        if self._latency_ms > 0:
            await asyncio.sleep(self._latency_ms / 1000.0)
        content = self._responder(system_prompt, user_prompt)
        return LLMResponse(
            content=content,
            prompt_tokens=len(system_prompt) // 4 + len(user_prompt) // 4,
            completion_tokens=len(content) // 4,
            total_tokens=(len(system_prompt) + len(user_prompt) + len(content)) // 4,
            latency_ms=self._latency_ms,
        )


def _default_stub_response(system_prompt: str, user_prompt: str) -> str:
    """默认桩响应 - 返回符合 Schema 的最小 JSON。

    v1.1: 不再输出 evidence_text，符合 A 组公平性（不要求证据）。
    """
    return json.dumps(
        {
            "fields": [
                {
                    "field_name": CoreFieldName.AMOUNT,
                    "support_level": "direct",
                    "values": [
                        {
                            "raw_value": "1200000.00",
                            "normalized_value": "1200000.00",
                            "amount_type": "budget",
                            "currency": "CNY",
                        }
                    ],
                }
            ]
        },
        ensure_ascii=False,
    )


# ============================================================
# Direct LLM Baseline 抽取器
# ============================================================


class DirectLLMBaseline:
    """Direct LLM Baseline 抽取器（v4.1 §10.8 实验组 A）。

    特征：
    - 直接调用 LLM 输出字段
    - 不要求证据
    - 不做程序验证
    - 不处理来源版本
    - 失败记录保留，不静默丢弃

    用法：
        client = StubLLMClient()
        baseline = DirectLLMBaseline(client=client, model_identifier="stub-1.0")
        record = await baseline.extract_one("doc-001", "公告正文...", "tender")
    """

    def __init__(
        self,
        client: LLMClient,
        model_identifier: str,
        prompt_version: str = PROMPT_VERSION,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        max_retries: int = 0,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries 不能为负")
        self._client = client
        self._model_identifier = model_identifier
        self._prompt_version = prompt_version
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._max_retries = max_retries

    @property
    def model_identifier(self) -> str:
        return self._model_identifier

    @property
    def prompt_version(self) -> str:
        return self._prompt_version

    async def extract_one(
        self,
        document_id: str,
        notice_text: str,
        notice_type: str | None = None,
    ) -> LLMExtractionRecord:
        """抽取单个公告版本。

        流程：
        1. 构造 prompt
        2. 调用 LLM（含重试）
        3. 解析 JSON
        4. 构造 LLMExtractionRecord（含失败记录）
        """
        if not document_id:
            raise ValueError("document_id 不能为空")
        if not notice_text or not notice_text.strip():
            raise ValueError("notice_text 不能为空")

        system_prompt, user_prompt = build_prompt(
            notice_text, notice_type, prompt_version=self._prompt_version
        )
        prompt_hash = compute_prompt_hash(system_prompt, user_prompt)
        started_at = datetime.now()
        logger.info(
            "extract_one start doc={} model={} prompt_hash={} text_len={}",
            document_id,
            self._model_identifier,
            prompt_hash[:12],
            len(notice_text),
        )

        last_error: str | None = None
        last_response: LLMResponse | None = None
        attempts = self._max_retries + 1

        for attempt in range(attempts):
            try:
                response = await self._client.complete(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                )
                last_response = response
                last_error = None
                break
            except Exception as exc:
                last_error = f"attempt {attempt + 1}/{attempts}: {type(exc).__name__}: {exc}"
                last_response = None
                logger.warning(
                    "extract_one retry doc={} attempt={}/{} error={}: {}",
                    document_id,
                    attempt + 1,
                    attempts,
                    type(exc).__name__,
                    exc,
                )
                if attempt < attempts - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))

        finished_at = datetime.now()
        latency_ms = int((finished_at - started_at).total_seconds() * 1000)

        if last_error is not None or last_response is None:
            logger.error(
                "extract_one failed doc={} latency_ms={} error={}",
                document_id,
                latency_ms,
                last_error or "未知错误",
            )
            return LLMExtractionRecord(
                document_id=document_id,
                model_identifier=self._model_identifier,
                prompt_hash=prompt_hash,
                prompt_version=self._prompt_version,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                started_at=started_at,
                finished_at=finished_at,
                latency_ms=latency_ms,
                success=False,
                error_message=last_error or "未知错误",
            )

        # 尝试解析 JSON
        try:
            output = self._parse_response(last_response.content)
        except Exception as exc:
            logger.error(
                "extract_one parse_failed doc={} latency_ms={} error={}: {}",
                document_id,
                latency_ms,
                type(exc).__name__,
                exc,
            )
            return LLMExtractionRecord(
                document_id=document_id,
                model_identifier=self._model_identifier,
                prompt_hash=prompt_hash,
                prompt_version=self._prompt_version,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                started_at=started_at,
                finished_at=finished_at,
                latency_ms=latency_ms,
                prompt_tokens=last_response.prompt_tokens,
                completion_tokens=last_response.completion_tokens,
                total_tokens=last_response.total_tokens,
                success=False,
                error_message=f"JSON 解析失败：{type(exc).__name__}: {exc}",
            )

        logger.info(
            "extract_one success doc={} latency_ms={} tokens={}/{}/{} fields={}",
            document_id,
            latency_ms,
            last_response.prompt_tokens,
            last_response.completion_tokens,
            last_response.total_tokens,
            len(output.fields),
        )
        return LLMExtractionRecord(
            document_id=document_id,
            model_identifier=self._model_identifier,
            prompt_hash=prompt_hash,
            prompt_version=self._prompt_version,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            started_at=started_at,
            finished_at=finished_at,
            latency_ms=latency_ms,
            prompt_tokens=last_response.prompt_tokens,
            completion_tokens=last_response.completion_tokens,
            total_tokens=last_response.total_tokens,
            success=True,
            output=output,
        )

    async def extract_batch(
        self,
        documents: list[tuple[str, str, str | None]],
        concurrency: int = 4,
    ) -> list[LLMExtractionRecord]:
        """批量抽取。

        documents: [(document_id, notice_text, notice_type), ...]
        concurrency: 并发度上限
        失败记录会保留在结果中，不静默丢弃。

        返回顺序与输入顺序一致。
        """
        if concurrency < 1:
            raise ValueError("concurrency 必须 >= 1")
        if not documents:
            return []

        logger.info(
            "extract_batch start count={} concurrency={} model={}",
            len(documents),
            concurrency,
            self._model_identifier,
        )

        semaphore = asyncio.Semaphore(concurrency)

        async def _wrapped(idx: int, doc_id: str, text: str, ntype: str | None) -> tuple[int, LLMExtractionRecord]:
            async with semaphore:
                record = await self.extract_one(doc_id, text, ntype)
                return idx, record

        tasks = [
            _wrapped(i, doc_id, text, ntype)
            for i, (doc_id, text, ntype) in enumerate(documents)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        results.sort(key=lambda x: x[0])
        records = [r for _, r in results]

        success_count = sum(1 for r in records if r.success)
        failure_count = len(records) - success_count
        logger.info(
            "extract_batch done count={} success={} failure={}",
            len(records),
            success_count,
            failure_count,
        )
        return records

    def _parse_response(self, content: str) -> LLMExtractionOutput:
        """解析 LLM 输出为 LLMExtractionOutput。

        宽容处理：
        - 去除 markdown ```json fence
        - 去除前后空白
        - 容忍尾部多余字符（取第一个完整 JSON）
        """
        text = content.strip()
        if text.startswith("```"):
            # 去除 markdown fence
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        # 尝试直接解析
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # 尝试截取第一个 { ... } 块
            start = text.find("{")
            end = text.rfind("}")
            # 修复：end < start 应为 end <= start（单字符场景不可能是合法 JSON）
            if start == -1 or end == -1 or end <= start:
                raise ValueError(f"无法在响应中找到合法 JSON 块：{content[:200]!r}")
            try:
                data = json.loads(text[start : end + 1])
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"JSON 解析失败（截取 {start}:{end + 1}）：{exc} content={content[:200]!r}"
                ) from exc

        # 兼容 LLM 直接返回字段数组（无 fields 包装）
        if isinstance(data, list):
            data = {"fields": data}
        elif isinstance(data, dict) and "fields" not in data:
            # 单字段场景
            data = {"fields": [data]}

        # 通过 Pydantic 严格校验
        return LLMExtractionOutput.model_validate(data)


# ============================================================
# 批量结果持久化（JSONL）
# ============================================================


def save_records_jsonl(
    records: list[LLMExtractionRecord],
    path: str | Path,
) -> int:
    """保存记录到 JSONL 文件。

    每行一条 JSON，便于流式读取和大文件处理。
    失败记录也会写入（success=false），不丢弃。

    返回写入的记录数量。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(record.model_dump_json())
            f.write("\n")
            count += 1
    return count


def load_records_jsonl(path: str | Path) -> list[LLMExtractionRecord]:
    """从 JSONL 文件加载记录。

    跳过无法解析的行并记录警告（不抛异常），
    但返回所有成功解析的记录。
    """
    path = Path(path)
    if not path.exists():
        return []
    records: list[LLMExtractionRecord] = []
    skipped = 0
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(LLMExtractionRecord.model_validate_json(line))
            except Exception as exc:
                # 修复：记录警告日志，便于排查损坏行
                logger.warning(
                    "load_records_jsonl skip corrupted line={} file={} error={}: {}",
                    line_no,
                    path,
                    type(exc).__name__,
                    exc,
                )
                skipped += 1
                continue
    if skipped > 0:
        logger.warning(
            "load_records_jsonl done file={} loaded={} skipped={}",
            path,
            len(records),
            skipped,
        )
    return records


__all__ = [
    "DirectLLMBaseline",
    "LLMClient",
    "LLMResponse",
    "PROMPT_VERSION",
    "PROMPT_VERSION_B",
    "StubLLMClient",
    "build_prompt",
    "compute_prompt_hash",
    "load_records_jsonl",
    "save_records_jsonl",
]
