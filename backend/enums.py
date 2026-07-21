"""BidAgent v4.1 枚举常量。

所有枚举用 String + 常量类实现，避免 SQLAlchemy Enum 的迁移痛点。
值来源于 v4.1 执行定稿版第四章数据模型定义。
"""
from __future__ import annotations


class IndustryCategory:
    """行业大类。"""
    GOODS = "goods"
    SERVICE = "service"
    ENGINEERING = "engineering"
    OTHER = "other"


class ResolutionStatus:
    """实体消歧状态。"""
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


class NoticeType:
    """公告类型。"""
    TENDER = "tender"
    CORRECTION = "correction"
    CLARIFICATION = "clarification"
    AWARD = "award"
    CANCELLATION = "cancellation"
    CONTRACT = "contract"
    OTHER = "other"


class NoticeStatus:
    """公告生命周期状态。"""
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    WITHDRAWN = "withdrawn"


class LegalEntityType:
    """组织法律实体类型。"""
    COMPANY = "company"
    GOVERNMENT_AGENCY = "government_agency"
    PUBLIC_INSTITUTION = "public_institution"
    SOCIAL_ORGANIZATION = "social_organization"
    INDIVIDUAL_BUSINESS = "individual_business"
    OTHER = "other"
    UNKNOWN = "unknown"


class ParticipantRole:
    """公告参与方业务角色。"""
    PURCHASER = "purchaser"
    PROCURING_AGENCY = "procuring_agency"
    BIDDER = "bidder"
    WINNER = "winner"
    CONSORTIUM_MEMBER = "consortium_member"
    SUBCONTRACTOR = "subcontractor"
    OTHER = "other"


class PlatformType:
    """平台类型。"""
    GOVERNMENT = "government"
    AUTHORIZED = "authorized"
    COMMERCIAL = "commercial"
    UNKNOWN = "unknown"


class PublicationRole:
    """页面发布角色。"""
    ORIGINAL = "original"
    OFFICIAL_REPOST = "official_repost"
    COMMERCIAL_REPOST = "commercial_repost"
    INDEX_ONLY = "index_only"
    UNKNOWN = "unknown"


class SourceQuality:
    """来源质量类别（可解释，不用数值评分）。"""
    OFFICIAL_ORIGINAL = "official_original"
    OFFICIAL_REPOST = "official_repost"
    AUTHORIZED_ORIGINAL = "authorized_original"
    COMMERCIAL_REPOST = "commercial_repost"
    INDEX_ONLY = "index_only"
    UNKNOWN = "unknown"


class ChangeType:
    """版本变更类型。"""
    INITIAL = "initial"
    NONE = "none"
    MINOR = "minor"
    MATERIAL = "material"
    WITHDRAWN = "withdrawn"


class FieldType:
    """字段类型。"""
    AMOUNT = "amount"
    DATE = "date"
    ORGANIZATION = "organization"
    IDENTIFIER = "identifier"
    FACT = "fact"
    TEXT = "text"


class AmountType:
    """金额类型。"""
    BUDGET = "budget"
    CEILING = "ceiling"
    AWARD = "award"
    CONTRACT = "contract"
    UNIT_PRICE = "unit_price"
    UNKNOWN = "unknown"


class TaxStatus:
    """含税状态。"""
    INCLUDED = "included"
    EXCLUDED = "excluded"
    UNKNOWN = "unknown"


class SupportLevel:
    """抽取支持度。"""
    DIRECT = "direct"
    EQUIVALENT = "equivalent"
    INFERRED = "inferred"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"


class CrossVerifyStatus:
    """交叉验证状态。"""
    INDEPENDENT = "independent"
    CONSISTENT_UNKNOWN = "consistent_unknown"
    SAME_ORIGIN = "same_origin"
    VERSION_DIFFERENCE = "version_difference"
    CONFLICT = "conflict"
    SINGLE_SOURCE = "single_source"


class DisplayGrade:
    """展示等级（派生结果，需保存规则版本）。"""
    HIGH = "high"
    REVIEW = "review"
    LOW = "low"


class EvidenceRole:
    """证据角色。"""
    PRIMARY = "primary"
    CONTEXT = "context"
    QUALIFIER = "qualifier"
    DERIVATION_INPUT = "derivation_input"
    CONTRADICTION = "contradiction"


# v4.1 第七章：六类正式主评测字段名
class CoreFieldName:
    PROJECT_IDENTIFIER = "project_identifier"
    PURCHASER_NAME = "purchaser_name"
    WINNER_NAME = "winner_name"
    AMOUNT = "amount"
    PUBLISH_DATE = "publish_date"
    BID_DEADLINE = "bid_deadline"

    ALL: tuple[str, ...] = (
        PROJECT_IDENTIFIER,
        PURCHASER_NAME,
        WINNER_NAME,
        AMOUNT,
        PUBLISH_DATE,
        BID_DEADLINE,
    )


# v4.1 第 10.3 节：金标字段状态
class GoldStatus:
    PRESENT = "present"
    ABSENT = "absent"
    NOT_APPLICABLE = "not_applicable"
    AMBIGUOUS = "ambiguous"
    ATTACHMENT_ONLY = "attachment_only"
    UNREADABLE = "unreadable"

    ALL: tuple[str, ...] = (
        PRESENT,
        ABSENT,
        NOT_APPLICABLE,
        AMBIGUOUS,
        ATTACHMENT_ONLY,
        UNREADABLE,
    )

    # 进入主评测分母的状态
    EVALUABLE: tuple[str, ...] = (PRESENT, ABSENT)


# 数据集划分
class DatasetSplit:
    DEV = "dev"
    CALIBRATION = "calibration"
    TEST = "test"
