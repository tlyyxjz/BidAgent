"""W3-03 证据定位指标评测数据类型定义。"""
from __future__ import annotations

from dataclasses import dataclass


IOU_THRESHOLD = 0.5  # Sol: 稍长上下文仍视为有效证据


@dataclass
class GoldEvidenceSpan:
    start: int  # 相对## marker的偏移
    end: int
    text: str


@dataclass
class GoldField:
    field_name: str
    gold_status: str
    evidences: list  # [GoldEvidenceSpan]


@dataclass
class GoldDoc:
    document_id: str
    file: str
    notice_type: str
    fields: list  # [GoldField]


@dataclass
class FieldMetric:
    doc_id: str
    field_name: str
    gold_status: str
    gold_evidence_count: int
    pred_evidence_count: int
    matched_evidence_count: int
    best_iou: float
    found: bool
    iou_passed: bool


@dataclass
class DocMetric:
    doc_id: str
    notice_type: str
    project_id: str  # 从 LLM 抽取的 project_identifier 作为 Bootstrap CI 分组 key (v4.1 10.10)
    fields_total: int
    fields_present: int
    fields_found: int
    evidences_pred: int
    evidences_located: int
    evidences_matched: int
    iou_list: list
    iou_list_matched: list
    recall: float
    precision: float
    iou_avg: float
    iou_avg_matched: float


@dataclass
class OverallMetric:
    docs_count: int
    fields_total: int
    fields_present: int
    fields_found: int
    evidences_pred: int
    evidences_located: int
    evidences_matched: int
    recall: float
    precision: float
    iou_avg: float
    iou_avg_matched: float
    iou_p50: float
    iou_p95: float
    model_id: str
    prompt_hash: str
    total_tokens: int
    invalid_docs: list
    # 按公告类型细分
    by_type: dict
