"""W2-09 证据定位指标评测数据类型定义。"""
from __future__ import annotations

from dataclasses import dataclass


DEFAULT_DOCS = [
    "tender_06", "tender_07",
    "award_05", "award_06",
    "correction_04", "correction_05",
    "multi_lot_02",
]

IOU_THRESHOLD = 0.5  # Sol: 稍长上下文仍视为有效证据


@dataclass
class GoldEvidenceSpan:
    start: int
    end: int
    text: str
    role: str


@dataclass
class GoldField:
    field_name: str
    gold_status: str
    evidences: list  # [GoldEvidenceSpan]


@dataclass
class GoldDoc:
    document_id: str
    file: str
    fields: list  # [GoldField]


@dataclass
class FieldMetric:
    doc_id: str
    field_name: str
    gold_status: str
    gold_evidence_count: int
    pred_evidence_count: int
    matched_evidence_count: int  # 与金标匹配的预测证据数
    best_iou: float  # 最佳 IoU (0.0 if no match)
    found: bool  # 系统是否找到至少 1 个匹配证据
    iou_passed: bool  # best_iou >= IOU_THRESHOLD


@dataclass
class DocMetric:
    doc_id: str
    fields_total: int
    fields_present: int  # gold_status == present/multi_value
    fields_found: int  # 系统找到证据的字段数
    evidences_pred: int
    evidences_located: int  # P3: 被 locator 定位到原文的证据数
    evidences_matched: int
    iou_list: list  # P3: 所有被定位证据的 IoU (含 IoU<0.5，未定位不算)
    iou_list_matched: list  # P3: 仅 matched=True (IoU>=阈值) 的 IoU，用于对比
    recall: float  # fields_found / fields_present
    precision: float  # 证据级精确率: evidences_matched / evidences_pred
    iou_avg: float  # P3: sum(iou_list_matched) / evidences_pred (未定位/未匹配算0，反映整体质量)
    iou_avg_matched: float  # P3: mean(iou_list_matched) 仅匹配证据的平均 (原口径，用于对比)


@dataclass
class OverallMetric:
    docs_count: int
    fields_total: int
    fields_present: int
    fields_found: int
    evidences_pred: int
    evidences_located: int  # P3: 被 locator 定位到原文的证据数
    evidences_matched: int
    recall: float  # 证据检出率: fields_found / fields_present
    precision: float  # 证据级精确率: evidences_matched / evidences_pred (与 W2-08 字段级口径不同)
    iou_avg: float  # P3: sum(all_ious_matched) / evidences_pred (未定位/未匹配算0)
    iou_avg_matched: float  # P3: 仅 matched 证据的平均 IoU (原口径，用于对比)
    iou_p50: float
    iou_p95: float
    model_id: str
    prompt_hash: str
    total_tokens: int
    invalid_docs: list  # P0-2: LLM 失败的 doc_id 列表 (向前兼容: 新增字段)
