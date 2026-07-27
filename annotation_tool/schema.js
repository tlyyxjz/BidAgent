/**
 * BidAgent 标注 Schema 常量
 * 与 backend/enums.py、backend/schemas.py 严格对齐
 * 
 * 注意：本文件只定义常量，不包含业务逻辑
 */

// ========== 六类正式核心字段名 ==========
// 对应 backend/enums.py -> CoreFieldName
const CORE_FIELD_NAMES = {
    PROJECT_IDENTIFIER: 'project_identifier',
    PURCHASER_NAME: 'purchaser_name',
    WINNER_NAME: 'winner_name',
    AMOUNT: 'amount',
    PUBLISH_DATE: 'publish_date',
    BID_DEADLINE: 'bid_deadline'
};

// 字段顺序（用于UI展示）
const CORE_FIELD_ORDER = [
    CORE_FIELD_NAMES.PROJECT_IDENTIFIER,
    CORE_FIELD_NAMES.PURCHASER_NAME,
    CORE_FIELD_NAMES.WINNER_NAME,
    CORE_FIELD_NAMES.AMOUNT,
    CORE_FIELD_NAMES.PUBLISH_DATE,
    CORE_FIELD_NAMES.BID_DEADLINE
];

// 字段中文显示名
const FIELD_DISPLAY_NAMES = {
    [CORE_FIELD_NAMES.PROJECT_IDENTIFIER]: '项目编号',
    [CORE_FIELD_NAMES.PURCHASER_NAME]: '采购人名称',
    [CORE_FIELD_NAMES.WINNER_NAME]: '中标人名称',
    [CORE_FIELD_NAMES.AMOUNT]: '金额及金额类型',
    [CORE_FIELD_NAMES.PUBLISH_DATE]: '发布日期',
    [CORE_FIELD_NAMES.BID_DEADLINE]: '投标截止日期'
};

// 字段类型
const FIELD_TYPES = {
    [CORE_FIELD_NAMES.PROJECT_IDENTIFIER]: 'identifier',
    [CORE_FIELD_NAMES.PURCHASER_NAME]: 'organization',
    [CORE_FIELD_NAMES.WINNER_NAME]: 'organization',
    [CORE_FIELD_NAMES.AMOUNT]: 'amount',
    [CORE_FIELD_NAMES.PUBLISH_DATE]: 'date',
    [CORE_FIELD_NAMES.BID_DEADLINE]: 'date'
};

// ========== 金标字段状态 ==========
// 对应 backend/enums.py -> GoldStatus
const GOLD_STATUS = {
    PRESENT: 'present',
    ABSENT: 'absent',
    NOT_APPLICABLE: 'not_applicable',
    AMBIGUOUS: 'ambiguous',
    ATTACHMENT_ONLY: 'attachment_only',
    UNREADABLE: 'unreadable'
};

// 状态中文显示名
const STATUS_DISPLAY_NAMES = {
    [GOLD_STATUS.PRESENT]: '存在',
    [GOLD_STATUS.ABSENT]: '不存在',
    [GOLD_STATUS.NOT_APPLICABLE]: '不适用',
    [GOLD_STATUS.AMBIGUOUS]: '歧义',
    [GOLD_STATUS.ATTACHMENT_ONLY]: '仅附件',
    [GOLD_STATUS.UNREADABLE]: '无法识别'
};

// 进入主评测分母的状态
const EVALUABLE_STATUSES = [GOLD_STATUS.PRESENT, GOLD_STATUS.ABSENT];

// ========== 证据角色 ==========
// 对应 backend/enums.py -> EvidenceRole
const EVIDENCE_ROLES = {
    PRIMARY: 'primary',
    CONTEXT: 'context',
    QUALIFIER: 'qualifier',
    DERIVATION_INPUT: 'derivation_input',
    CONTRADICTION: 'contradiction'
};

const EVIDENCE_ROLE_DISPLAY_NAMES = {
    [EVIDENCE_ROLES.PRIMARY]: '主证据',
    [EVIDENCE_ROLES.CONTEXT]: '上下文',
    [EVIDENCE_ROLES.QUALIFIER]: '限定条件',
    [EVIDENCE_ROLES.DERIVATION_INPUT]: '推导输入',
    [EVIDENCE_ROLES.CONTRADICTION]: '冲突证据'
};

// ========== 金额类型 ==========
// 对应 backend/enums.py -> AmountType
const AMOUNT_TYPES = {
    BUDGET: 'budget',
    CEILING: 'ceiling',
    AWARD: 'award',
    CONTRACT: 'contract',
    UNIT_PRICE: 'unit_price',
    UNKNOWN: 'unknown'
};

const AMOUNT_TYPE_DISPLAY_NAMES = {
    [AMOUNT_TYPES.BUDGET]: '预算金额',
    [AMOUNT_TYPES.CEILING]: '最高限价',
    [AMOUNT_TYPES.AWARD]: '中标金额',
    [AMOUNT_TYPES.CONTRACT]: '合同金额',
    [AMOUNT_TYPES.UNIT_PRICE]: '单价',
    [AMOUNT_TYPES.UNKNOWN]: '未知'
};

// ========== 含税状态 ==========
const TAX_STATUSES = {
    INCLUDED: 'included',
    EXCLUDED: 'excluded',
    UNKNOWN: 'unknown'
};

// ========== 标注状态（文档级） ==========
const ANNOTATION_STATUSES = {
    PENDING: 'pending',
    DONE: 'done',
    REVIEW: 'review'
};

// ========== 偏移量坐标空间 ==========
const OFFSET_SPACE = 'clean_raw_text';

// ========== 标注规范版本 ==========
const ANNOTATION_VERSION = '1.0';

// ========== 工具函数 ==========

/**
 * 验证证据偏移量是否正确
 * 必须满足 text.slice(start, end) === evidenceText
 * @param {string} text - 完整原文
 * @param {number} start - 起始偏移（含）
 * @param {number} end - 结束偏移（不含）
 * @param {string} evidenceText - 证据文本
 * @returns {{valid: boolean, actualText: string}}
 */
function verifyEvidenceSpan(text, start, end, evidenceText) {
    if (start < 0 || end > text.length || start >= end) {
        return { valid: false, actualText: '' };
    }
    const actual = text.slice(start, end);
    return {
        valid: actual === evidenceText,
        actualText: actual
    };
}

/**
 * 创建空的字段标注对象
 * @param {string} fieldName 
 * @returns {object}
 */
function createEmptyField(fieldName) {
    return {
        field_name: fieldName,
        gold_status: GOLD_STATUS.ABSENT,
        values: [],
        note: ''
    };
}

/**
 * 创建空的字段值对象
 * @returns {object}
 */
function createEmptyValue() {
    return {
        raw_value: '',
        normalized_value: null,
        amount_type: null,
        currency: null,
        original_unit: null,
        tax_status: null,
        lot_id: null,
        acceptable_evidence_spans: []
    };
}

/**
 * 创建空的证据片段对象
 * @returns {object}
 */
function createEmptyEvidenceSpan() {
    return {
        role: EVIDENCE_ROLES.PRIMARY,
        start: 0,
        end: 0,
        text: ''
    };
}

/**
 * 创建空的标注文档
 * @param {string} documentId 
 * @param {string} annotatorId 
 * @returns {object}
 */
function createEmptyAnnotationDocument(documentId, annotatorId) {
    return {
        document_id: documentId || '',
        annotator_id: annotatorId || 'A',
        annotation_version: ANNOTATION_VERSION,
        annotation_time: null,
        fields: CORE_FIELD_ORDER.map(name => createEmptyField(name))
    };
}

// 导出（浏览器环境直接挂到 window）
if (typeof window !== 'undefined') {
    window.AnnotationSchema = {
        CORE_FIELD_NAMES,
        CORE_FIELD_ORDER,
        FIELD_DISPLAY_NAMES,
        FIELD_TYPES,
        GOLD_STATUS,
        STATUS_DISPLAY_NAMES,
        EVALUABLE_STATUSES,
        EVIDENCE_ROLES,
        EVIDENCE_ROLE_DISPLAY_NAMES,
        AMOUNT_TYPES,
        AMOUNT_TYPE_DISPLAY_NAMES,
        TAX_STATUSES,
        ANNOTATION_STATUSES,
        OFFSET_SPACE,
        ANNOTATION_VERSION,
        verifyEvidenceSpan,
        createEmptyField,
        createEmptyValue,
        createEmptyEvidenceSpan,
        createEmptyAnnotationDocument
    };
}
