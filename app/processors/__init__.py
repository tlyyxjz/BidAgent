"""数据处理模块：去重、解析和验证。"""

from app.processors.simhash import compute_simhash, hamming_distance, is_similar

# re-export 子模块关键公开接口（# noqa: F401）
from app.processors.field_validator import (  # noqa: F401
    ValidationResult,
    validate_amount,
    validate_date,
    validate_field,
    validate_project_identifier,
)
from app.processors.normalizer import (  # noqa: F401
    NORMALIZER_VERSION,
    OffsetMapping,
    normalize_text,
)
from app.processors.evidence_repository import (  # noqa: F401
    create_evidence,
    get_field_with_evidence,
    get_tender_fields,
)
from app.processors.evidence_locator import (  # noqa: F401
    EvidenceLocator,
    verify_evidence,
)
from app.processors.hallucination_checker import (  # noqa: F401
    CheckReport,
    check_content,
    extract_facts,
)
from app.processors.source_lineage import (  # noqa: F401
    determine_source_role,
    generate_source_lineage,
)

__all__ = [
    "compute_simhash",
    "hamming_distance",
    "is_similar",
    "ValidationResult",
    "validate_amount",
    "validate_date",
    "validate_field",
    "validate_project_identifier",
    "normalize_text",
    "NORMALIZER_VERSION",
    "OffsetMapping",
    "create_evidence",
    "get_field_with_evidence",
    "get_tender_fields",
    "EvidenceLocator",
    "verify_evidence",
    "check_content",
    "extract_facts",
    "CheckReport",
    "generate_source_lineage",
    "determine_source_role",
]
