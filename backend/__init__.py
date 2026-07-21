"""BidAgent v4.1 后端模块。

按 v4.1 执行定稿版分工，GLM 5.2 主责：
- backend/models.py   四层数据模型（10 个实体）
- backend/schemas.py  标注 JSON Schema（Pydantic v2）
- backend/extractors.py  Direct LLM Baseline
- backend/evaluation.py  评测脚本（Precision/Recall/F1/空值误报率）

公共 API 通过 __all__ 显式导出，便于外部调用方稳定 import：
    from backend import DirectLLMBaseline, AnnotationDocument, evaluate_dataset
"""

from backend.enums import (
    AmountType,
    ChangeType,
    CoreFieldName,
    CrossVerifyStatus,
    DatasetSplit,
    DisplayGrade,
    EvidenceRole,
    FieldType,
    GoldStatus,
    IndustryCategory,
    LegalEntityType,
    NoticeStatus,
    NoticeType,
    ParticipantRole,
    PlatformType,
    PublicationRole,
    ResolutionStatus,
    SourceQuality,
    SupportLevel,
    TaxStatus,
)
from backend.bootstrap import (
    BootstrapResult,
    ConfidenceInterval,
    bootstrap_evaluate,
    bootstrap_evaluate_async,
)
from backend.evaluation import (
    DocumentFieldResult,
    FieldStatusStats,
    compute_status_stats,
    evaluate_dataset,
    evaluate_document,
    export_status_stats_csv,
    export_summary_csv,
    export_summary_json,
    normalize_value,
    safe_evaluate_dataset,
    values_match,
)
from backend.extractors import (
    PROMPT_VERSION,
    DirectLLMBaseline,
    LLMClient,
    LLMResponse,
    StubLLMClient,
    build_prompt,
    compute_prompt_hash,
    load_records_jsonl,
    save_records_jsonl,
)
from backend.llm_client import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    LLMAuthError,
    LLMClientError,
    LLMRateLimitError,
    LLMResponseError,
    LLMServerError,
    LLMTimeoutError,
    OpenAICompatibleClient,
)
from backend.migrations import migrate_ba_tables
from backend.models import (
    Evidence,
    ExtractedField,
    FieldEvidenceLink,
    NoticeParticipant,
    NoticeSource,
    NoticeVersion,
    Organization,
    ProjectIdentifier,
    TenderNotice,
    TenderProject,
)
from backend.schemas import (
    AnnotatedField,
    AnnotationDocument,
    EvaluationSummary,
    EvidenceSpan,
    FieldMetrics,
    FieldValue,
    LLMExtractionOutput,
    LLMExtractionRecord,
    LLMExtractedField,
    LLMExtractedValue,
    make_empty_annotation_document,
)

__all__ = [
    # enums
    "AmountType",
    "ChangeType",
    "CoreFieldName",
    "CrossVerifyStatus",
    "DatasetSplit",
    "DisplayGrade",
    "EvidenceRole",
    "FieldType",
    "GoldStatus",
    "IndustryCategory",
    "LegalEntityType",
    "NoticeStatus",
    "NoticeType",
    "ParticipantRole",
    "PlatformType",
    "PublicationRole",
    "ResolutionStatus",
    "SourceQuality",
    "SupportLevel",
    "TaxStatus",
    # models
    "Evidence",
    "ExtractedField",
    "FieldEvidenceLink",
    "NoticeParticipant",
    "NoticeSource",
    "NoticeVersion",
    "Organization",
    "ProjectIdentifier",
    "TenderNotice",
    "TenderProject",
    "migrate_ba_tables",
    # schemas
    "AnnotatedField",
    "AnnotationDocument",
    "EvaluationSummary",
    "EvidenceSpan",
    "FieldMetrics",
    "FieldValue",
    "LLMExtractionOutput",
    "LLMExtractionRecord",
    "LLMExtractedField",
    "LLMExtractedValue",
    "make_empty_annotation_document",
    # extractors
    "PROMPT_VERSION",
    "DirectLLMBaseline",
    "LLMClient",
    "LLMResponse",
    "StubLLMClient",
    "build_prompt",
    "compute_prompt_hash",
    "load_records_jsonl",
    "save_records_jsonl",
    # llm_client
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "LLMAuthError",
    "LLMClientError",
    "LLMRateLimitError",
    "LLMResponseError",
    "LLMServerError",
    "LLMTimeoutError",
    "OpenAICompatibleClient",
    # evaluation
    "DocumentFieldResult",
    "FieldStatusStats",
    "compute_status_stats",
    "evaluate_dataset",
    "evaluate_document",
    "export_status_stats_csv",
    "export_summary_csv",
    "export_summary_json",
    "normalize_value",
    "safe_evaluate_dataset",
    "values_match",
    # bootstrap
    "BootstrapResult",
    "ConfidenceInterval",
    "bootstrap_evaluate",
    "bootstrap_evaluate_async",
]
