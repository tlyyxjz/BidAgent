"""BidAgent v4.1 标注 JSON Schema 与衍生数据契约（W1-04）。

Pydantic v2 实现，对应《金标数据标注手册》第七章 JSON 示例和 v4.1 第十章评测体系。

包含三组 Schema：
1. **金标标注 Schema**：AnnotationDocument / AnnotatedField / FieldValue / EvidenceSpan
   - 用于人工标注（双人独立 + 仲裁）和系统评测真值
2. **LLM 抽取输出 Schema**：LLMExtractionOutput / LLMExtractionRecord
   - 用于 W1-06 Direct LLM Baseline 的结构化输出契约
3. **评测指标 Schema**：FieldMetrics / EvaluationSummary
   - 用于 W1-07 基础评测脚本的结果导出

工程规范：
- 严格模式（extra='forbid'）防止标注员私加字段
- 字段名、状态、角色均从 backend.enums 引用常量
- 偏移量坐标空间固定为 clean_raw_text，半开区间 [start, end)
- 金额类型仅在 amount 字段下使用
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.enums import (
    AmountType,
    CoreFieldName,
    EvidenceRole,
    GoldStatus,
    SupportLevel,
    TaxStatus,
)


# ============================================================
# 第一组：金标标注 Schema
# ============================================================


class EvidenceSpan(BaseModel):
    """证据原文片段 - 合法证据区间。

    坐标空间固定为 clean_raw_text，半开区间 [start, end)。
    必须满足 clean_raw_text[start:end] == text。
    """
    model_config = ConfigDict(extra="forbid")

    role: Literal[
        EvidenceRole.PRIMARY,
        EvidenceRole.CONTEXT,
        EvidenceRole.QUALIFIER,
        EvidenceRole.DERIVATION_INPUT,
        EvidenceRole.CONTRADICTION,
    ] = Field(..., description="证据角色")
    start: int = Field(..., ge=0, description="起始偏移（含）")
    end: int = Field(..., ge=1, description="结束偏移（不含）")
    text: str = Field(..., min_length=1, description="证据原文片段")

    @model_validator(mode="after")
    def _check_range(self) -> EvidenceSpan:
        if self.end <= self.start:
            raise ValueError(
                f"end({self.end}) 必须大于 start({self.start})，半开区间 [start, end)"
            )
        return self


class FieldValue(BaseModel):
    """字段值 - 单个抽取值的完整记录。

    金额类型字段使用 amount_type / currency / original_unit / tax_status。
    其他类型字段这些字段应为 None。
    """
    model_config = ConfigDict(extra="forbid")

    raw_value: str = Field(..., min_length=1, description="原文形式，不得改写")
    normalized_value: str | None = Field(
        default=None, description="归一化结果（单值主值）"
    )
    amount_type: Literal[
        AmountType.BUDGET,
        AmountType.CEILING,
        AmountType.AWARD,
        AmountType.CONTRACT,
        AmountType.UNIT_PRICE,
        AmountType.UNKNOWN,
    ] | None = Field(default=None, description="金额类型（仅 amount 字段）")
    currency: str | None = Field(
        default=None, max_length=10, description="币种（如 CNY）"
    )
    original_unit: str | None = Field(
        default=None, max_length=20, description="原始单位（如 万元、亿元）"
    )
    tax_status: Literal[
        TaxStatus.INCLUDED, TaxStatus.EXCLUDED, TaxStatus.UNKNOWN,
    ] | None = Field(default=None, description="含税状态（仅 amount 字段）")
    lot_id: str | None = Field(default=None, max_length=100, description="所属分包")
    acceptable_evidence_spans: list[EvidenceSpan] = Field(
        default_factory=list,
        description="合法证据区间列表（金标至少含一个 primary）",
    )

    @model_validator(mode="after")
    def _check_primary_evidence(self) -> FieldValue:
        """金标字段为 present 时至少需要一个 primary 证据。"""
        if self.acceptable_evidence_spans:
            has_primary = any(
                span.role == EvidenceRole.PRIMARY
                for span in self.acceptable_evidence_spans
            )
            if not has_primary:
                raise ValueError(
                    "acceptable_evidence_spans 非空时必须至少包含一个 primary 证据"
                )
        return self


class AnnotatedField(BaseModel):
    """标注字段 - 单个六类核心字段的人工标注。"""
    model_config = ConfigDict(extra="forbid")

    field_name: Literal[
        CoreFieldName.PROJECT_IDENTIFIER,
        CoreFieldName.PURCHASER_NAME,
        CoreFieldName.WINNER_NAME,
        CoreFieldName.AMOUNT,
        CoreFieldName.PUBLISH_DATE,
        CoreFieldName.BID_DEADLINE,
    ] = Field(..., description="字段名（六类核心字段之一）")
    gold_status: Literal[
        GoldStatus.PRESENT,
        GoldStatus.ABSENT,
        GoldStatus.NOT_APPLICABLE,
        GoldStatus.AMBIGUOUS,
        GoldStatus.ATTACHMENT_ONLY,
        GoldStatus.UNREADABLE,
    ] = Field(..., description="字段状态")
    values: list[FieldValue] = Field(
        default_factory=list,
        description="字段值列表（present 时至少 1 个，其他状态可为空）",
    )
    note: str = Field(default="", description="备注（不确定项说明）")

    @model_validator(mode="after")
    def _check_values_consistency(self) -> AnnotatedField:
        """状态与值数量一致性校验。

        已知限制（W1 阶段）：按 v4.1 §10.3，present 字段应至少有一个 primary 证据。
        当前实现未强制要求 evidence_spans，因为 W1 阶段还没有真实金标数据。
        W1-08/W1-09 标注阶段开始后，应启用 strict 模式强制要求。
        """
        if self.gold_status == GoldStatus.PRESENT:
            if not self.values:
                raise ValueError(
                    f"gold_status=present 时 values 不能为空（field={self.field_name}）"
                )
        elif self.gold_status == GoldStatus.ABSENT:
            if self.values:
                raise ValueError(
                    f"gold_status=absent 时 values 必须为空（field={self.field_name}）"
                )
        return self

    @field_validator("note")
    @classmethod
    def _strip_note(cls, v: str) -> str:
        return v.strip() if v else ""


class AnnotationDocument(BaseModel):
    """金标标注文档 - 一份公告版本的完整人工标注。

    对应《金标数据标注手册》第七章 JSON 示例。
    """
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(
        ..., min_length=1, description="对应 NoticeVersion.version_id"
    )
    annotator_id: str = Field(
        ..., min_length=1, description="标注员标识（A / B / arbitrator）"
    )
    annotation_version: str = Field(
        ..., min_length=1, description="标注规范版本（如 1.0）"
    )
    annotation_time: datetime | None = Field(
        default=None, description="标注时间（ISO 8601）"
    )
    fields: list[AnnotatedField] = Field(
        ..., min_length=1, description="字段标注列表"
    )

    @model_validator(mode="after")
    def _check_unique_field_names(self) -> AnnotationDocument:
        """同一文档中同一 field_name 不允许重复出现。"""
        seen: set[str] = set()
        for f in self.fields:
            if f.field_name in seen:
                raise ValueError(
                    f"field_name 重复出现：{f.field_name}"
                )
            seen.add(f.field_name)
        return self

    def get_field(self, field_name: str) -> AnnotatedField | None:
        """按字段名查询标注。"""
        for f in self.fields:
            if f.field_name == field_name:
                return f
        return None


# ============================================================
# 第二组：LLM 抽取输出 Schema（W1-06 Direct LLM Baseline）
# ============================================================


class LLMExtractedValue(BaseModel):
    """LLM 抽取的单一值（原始输出，未经过程序验证）。"""
    model_config = ConfigDict(extra="forbid")

    raw_value: str = Field(..., description="LLM 输出的原始字符串")
    normalized_value: str | None = Field(
        default=None, description="LLM 尝试归一化的结果"
    )
    amount_type: str | None = Field(
        default=None, description="LLM 推断的金额类型"
    )
    currency: str | None = Field(default=None, description="币种")
    lot_id: str | None = Field(default=None, description="分包")
    evidence_text: str | None = Field(
        default=None, description="LLM 引用的证据文本（可能不存在）"
    )

    def to_dict_safe(self) -> dict[str, object]:
        """转换为可序列化字典。"""
        return self.model_dump()


class LLMExtractedField(BaseModel):
    """LLM 抽取的单一字段（包含多个值）。"""
    model_config = ConfigDict(extra="forbid")

    field_name: str = Field(..., description="字段名")
    support_level: Literal[
        SupportLevel.DIRECT,
        SupportLevel.EQUIVALENT,
        SupportLevel.INFERRED,
        SupportLevel.UNSUPPORTED,
        SupportLevel.CONTRADICTED,
    ] = Field(
        default=SupportLevel.UNSUPPORTED,
        description="LLM 自报支持度（仅作参考，不作为最终可信度）",
    )
    values: list[LLMExtractedValue] = Field(
        default_factory=list, description="抽取值列表"
    )


class LLMExtractionOutput(BaseModel):
    """LLM 直接抽取的原始输出（结构化 JSON）。"""
    model_config = ConfigDict(extra="forbid")

    fields: list[LLMExtractedField] = Field(
        default_factory=list, description="抽取字段列表"
    )


class LLMExtractionRecord(BaseModel):
    """LLM 抽取记录 - 含元数据，用于 W1-06 Baseline 运行记录。

    记录模型标识、参数、Token、延迟和错误信息，
    便于后续消融实验和质量追踪。
    """
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(..., description="对应 NoticeVersion.version_id")
    model_identifier: str = Field(..., description="模型标识（如 glm-5.2）")
    prompt_hash: str = Field(..., description="提示词 SHA256")
    prompt_version: str = Field(default="1.0", description="提示词版本")
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1)
    started_at: datetime | None = Field(default=None)
    finished_at: datetime | None = Field(default=None)
    latency_ms: int | None = Field(default=None, ge=0, description="延迟（毫秒）")
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    success: bool = Field(default=True, description="是否成功")
    error_message: str | None = Field(default=None, description="失败时的错误信息")
    output: LLMExtractionOutput | None = Field(
        default=None, description="抽取输出（失败时为 None）"
    )

    @model_validator(mode="after")
    def _check_consistency(self) -> LLMExtractionRecord:
        if not self.success and self.output is not None:
            raise ValueError("success=False 时 output 必须为 None")
        if not self.success and not self.error_message:
            raise ValueError("success=False 时 error_message 必须非空")
        if self.success and self.error_message:
            raise ValueError("success=True 时 error_message 必须为空")
        return self


# ============================================================
# 第三组：评测指标 Schema（W1-07 评测脚本）
# ============================================================


class FieldMetrics(BaseModel):
    """单字段评测指标 - Precision / Recall / F1 / 空值误报率 / 无依据输出率。

    W1-07 新增指标（v2）：
    - unjustified_count: 系统输出值中 evidence_text 为空或无法在原文定位的数量
    - unjustified_rate: 无依据输出比例 = unjustified / system_value_total
    - 多值字段精确指标：precision_multi / recall_multi（按值数比例，非"至少1个匹配"）
    - amount_type_mismatch_count: 金额字段 amount_type 与金标不一致的值数
    """
    model_config = ConfigDict(extra="forbid")

    field_name: str
    # 主评测分母只包含 present 和 absent
    gold_present_count: int = Field(default=0, ge=0, description="金标 present 数")
    gold_absent_count: int = Field(default=0, ge=0, description="金标 absent 数")
    gold_other_count: int = Field(
        default=0, ge=0,
        description="金标其他状态数（not_applicable/ambiguous/...）",
    )
    system_correct_count: int = Field(
        default=0, ge=0, description="系统输出且与金标一致的数量"
    )
    system_output_count: int = Field(
        default=0, ge=0, description="系统输出字段总数（含错误）"
    )
    false_positive_on_absent: int = Field(
        default=0, ge=0,
        description="金标 absent 但系统输出了值（空值误报）",
    )
    # W1-07 v2 新增：无依据输出率（核心指标）
    system_value_total: int = Field(
        default=0, ge=0,
        description="系统输出值总数（所有字段值的原始计数，含重复）",
    )
    unjustified_count: int = Field(
        default=0, ge=0,
        description="无依据输出值数（evidence_text 为空或无法在原文定位）",
    )
    # W1-07 v2 新增：多值字段精确指标
    matched_value_count: int = Field(
        default=0, ge=0,
        description="系统值与金标值匹配的数量（用于精确 P/R）",
    )
    gold_value_total: int = Field(
        default=0, ge=0,
        description="金标值总数（多值字段的值数总和）",
    )
    amount_type_mismatch_count: int = Field(
        default=0, ge=0,
        description="金额字段 amount_type 与金标不一致的值数",
    )

    @property
    def precision(self) -> float:
        """Precision = correct / output_total（字段级）。"""
        if self.system_output_count == 0:
            return 0.0
        return self.system_correct_count / self.system_output_count

    @property
    def recall(self) -> float:
        """Recall = correct / gold_present。"""
        if self.gold_present_count == 0:
            return 0.0
        return self.system_correct_count / self.gold_present_count

    @property
    def f1(self) -> float:
        """F1 = 2*P*R / (P+R)。"""
        p, r = self.precision, self.recall
        if p + r == 0:
            return 0.0
        return 2 * p * r / (p + r)

    @property
    def false_omission_rate_on_absent(self) -> float:
        """空值误报率 = FP_on_absent / gold_absent。"""
        if self.gold_absent_count == 0:
            return 0.0
        return self.false_positive_on_absent / self.gold_absent_count

    @property
    def unjustified_rate(self) -> float:
        """无依据输出率 = unjustified / system_value_total（项目核心指标）。

        衡量 LLM "瞎编"比例：输出了值但 evidence_text 为空或在原文找不到。
        """
        if self.system_value_total == 0:
            return 0.0
        return self.unjustified_count / self.system_value_total

    @property
    def precision_multi(self) -> float:
        """多值字段精确 Precision = matched / system_value_total。"""
        if self.system_value_total == 0:
            return 0.0
        return self.matched_value_count / self.system_value_total

    @property
    def recall_multi(self) -> float:
        """多值字段精确 Recall = matched / gold_value_total。"""
        if self.gold_value_total == 0:
            return 0.0
        return self.matched_value_count / self.gold_value_total

    @property
    def f1_multi(self) -> float:
        """多值字段精确 F1。"""
        p, r = self.precision_multi, self.recall_multi
        if p + r == 0:
            return 0.0
        return 2 * p * r / (p + r)

    def to_dict(self) -> dict[str, object]:
        """导出含派生指标的字典。"""
        return {
            "field_name": self.field_name,
            "gold_present_count": self.gold_present_count,
            "gold_absent_count": self.gold_absent_count,
            "gold_other_count": self.gold_other_count,
            "system_correct_count": self.system_correct_count,
            "system_output_count": self.system_output_count,
            "false_positive_on_absent": self.false_positive_on_absent,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "false_omission_rate_on_absent": round(
                self.false_omission_rate_on_absent, 4
            ),
            # W1-07 v2 新增
            "system_value_total": self.system_value_total,
            "unjustified_count": self.unjustified_count,
            "unjustified_rate": round(self.unjustified_rate, 4),
            "matched_value_count": self.matched_value_count,
            "gold_value_total": self.gold_value_total,
            "precision_multi": round(self.precision_multi, 4),
            "recall_multi": round(self.recall_multi, 4),
            "f1_multi": round(self.f1_multi, 4),
            "amount_type_mismatch_count": self.amount_type_mismatch_count,
        }


class EvaluationSummary(BaseModel):
    """评测汇总 - 跨字段聚合指标。"""
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(..., description="评测运行 ID（ULID 或时间戳）")
    run_at: datetime = Field(default_factory=datetime.now)
    system_identifier: str = Field(..., description="被评测系统标识")
    dataset_split: str = Field(..., description="数据集划分（dev/calibration/test）")
    document_count: int = Field(default=0, ge=0)
    field_metrics: list[FieldMetrics] = Field(default_factory=list)

    @property
    def macro_precision(self) -> float:
        """宏平均 Precision。"""
        if not self.field_metrics:
            return 0.0
        return sum(m.precision for m in self.field_metrics) / len(self.field_metrics)

    @property
    def macro_recall(self) -> float:
        """宏平均 Recall。"""
        if not self.field_metrics:
            return 0.0
        return sum(m.recall for m in self.field_metrics) / len(self.field_metrics)

    @property
    def macro_f1(self) -> float:
        """宏平均 F1。"""
        if not self.field_metrics:
            return 0.0
        return sum(m.f1 for m in self.field_metrics) / len(self.field_metrics)

    def to_dict(self) -> dict[str, object]:
        """导出汇总字典。"""
        return {
            "run_id": self.run_id,
            "run_at": self.run_at.isoformat(),
            "system_identifier": self.system_identifier,
            "dataset_split": self.dataset_split,
            "document_count": self.document_count,
            "macro_precision": round(self.macro_precision, 4),
            "macro_recall": round(self.macro_recall, 4),
            "macro_f1": round(self.macro_f1, 4),
            "fields": [m.to_dict() for m in self.field_metrics],
        }


# ============================================================
# 便捷构造函数
# ============================================================


def make_empty_annotation_document(
    document_id: str,
    annotator_id: str,
    annotation_version: str = "1.0",
) -> AnnotationDocument:
    """构造一份空标注文档（供标注工具初始化）。"""
    return AnnotationDocument(
        document_id=document_id,
        annotator_id=annotator_id,
        annotation_version=annotation_version,
        fields=[
            AnnotatedField(field_name=name, gold_status=GoldStatus.ABSENT)
            for name in CoreFieldName.ALL
        ],
    )


__all__ = [
    "AnnotatedField",
    "AnnotationDocument",
    "EvidenceSpan",
    "EvaluationSummary",
    "FieldMetrics",
    "FieldValue",
    "LLMExtractionOutput",
    "LLMExtractedField",
    "LLMExtractionRecord",
    "LLMExtractedValue",
    "make_empty_annotation_document",
]
