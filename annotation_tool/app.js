/**
 * BidAgent 金标标注工具 - 核心逻辑
 *
 * 功能：
 * - 左侧展示 clean_raw_text，支持鼠标选中文本（基于 DOM Range 计算绝对 UTF-16 偏移）
 * - 右侧六类核心字段标注，支持六种状态
 * - 多值字段、多段证据、证据角色
 * - 金额类型、lot_id、备注
 * - JSON 导入导出（导出前/导入后完整校验）
 * - localStorage 按文档隔离（document_id 区分草稿 + 文档索引）
 * - noticeType / annotationStatus 同步到本地元数据（不混入 extra="forbid" 的导出 JSON）
 * - 证据高亮回显 + 重叠检测
 * - XSS 防护（textContent / createElement 替代 innerHTML 拼接）
 *
 * 严格对接 backend/schemas.py 中的 AnnotationDocument Schema（extra="forbid"）
 */

(function() {
    'use strict';

    const Schema = window.AnnotationSchema;
    const SampleData = window.SampleData;

    // ========== 常量 ==========

    const STORAGE_KEYS = {
        DOC_INDEX: 'bidagent_annotation_doc_index',
        DRAFT_PREFIX: 'bidagent_annotation_draft_',
        META_PREFIX: 'bidagent_annotation_meta_',
        LAST_ACTIVE_DOC: 'bidagent_annotation_last_active_doc'
    };

    // 文件导入大小限制（5 MB，可配置）
    const MAX_IMPORT_SIZE = 5 * 1024 * 1024;

    // ========== 全局状态 ==========

    const state = {
        rawText: '',
        annotation: null,
        // 文档级元数据（不混入 AnnotationDocument 导出 JSON，因为 extra="forbid"）
        docMeta: {
            noticeType: 'tender',
            annotationStatus: 'pending'
        },
        currentFieldIndex: -1,
        currentValueIndex: -1,
        editingEvidenceIndex: -1,
        saveTimeout: null,
        // P0-2 状态保持：值项折叠状态（ui_id -> true 表示折叠）
        valueCollapsed: {},
        // P0-1 添加新值后自动滚动+聚焦的目标 ui_id
        pendingFocusUiId: null
    };

    // ========== ui_id 管理（P0-2 稳定标识，不混入导出 JSON） ==========
    // 给每个前端值项分配稳定的 ui_id，用于跨 renderFields 保持状态对应关系。
    // ui_id 仅存在于前端内存和 localStorage 草稿中，导出 JSON 时剥离。

    let _uiIdCounter = 0;
    function generateUiId() {
        _uiIdCounter++;
        return 'v_' + Date.now().toString(36) + '_' + _uiIdCounter.toString(36);
    }

    function ensureValueUiId(value) {
        if (!value) return value;
        if (!value.ui_id) {
            value.ui_id = generateUiId();
        }
        return value;
    }

    function ensureAllValuesHaveUiId(annotation) {
        if (!annotation || !annotation.fields) return;
        annotation.fields.forEach(field => {
            if (field.values) {
                field.values.forEach(v => ensureValueUiId(v));
            }
        });
    }

    /**
     * 从导出 JSON / 校验中剥离 ui_id 等 UI 元数据。
     * 返回深拷贝后的纯净 fields 数组（符合 AnnotationDocument Schema extra="forbid"）。
     */
    function stripUiMetadataForExport(fields) {
        return fields.map(f => {
            const fieldClone = {
                field_name: f.field_name,
                gold_status: f.gold_status,
                values: (f.values || []).map(v => {
                    const valueClone = {
                        raw_value: v.raw_value,
                        normalized_value: v.normalized_value,
                        amount_type: v.amount_type,
                        currency: v.currency,
                        original_unit: v.original_unit,
                        tax_status: v.tax_status,
                        lot_id: v.lot_id,
                        acceptable_evidence_spans: (v.acceptable_evidence_spans || []).map(e => ({
                            role: e.role,
                            start: e.start,
                            end: e.end,
                            text: e.text
                        }))
                    };
                    return valueClone;
                }),
                note: f.note || ''
            };
            return fieldClone;
        });
    }

    // ========== 工具函数 ==========

    /**
     * HTML 转义（仅在确需 innerHTML 时使用；用户控制文本优先用 textContent）
     */
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = String(text == null ? '' : text);
        return div.innerHTML;
    }

    /**
     * 换行符规范化为 LF（与 Python normalize_newlines 一致）
     */
    function normalizeNewlines(text) {
        return text.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    }

    /**
     * 计算 SHA256（UTF-8 编码，与 Python compute_sha256 一致）
     */
    async function computeSha256(text) {
        const encoder = new TextEncoder();
        const data = encoder.encode(text);
        const hashBuffer = await crypto.subtle.digest('SHA-256', data);
        const bytes = new Uint8Array(hashBuffer);
        return Array.from(bytes).map(b => b.toString(16).padStart(2, '0')).join('');
    }

    /**
     * 根据原文内容推断公告类型（noticeType）。
     * 推断规则与 fixtures/generate.py 中的 infer_notice_type 完全一致：
     * 1. 包含"中标"且("结果公告"或"中标公告") → award
     * 2. 包含"更正公告" → correction
     * 3. 包含"招标公告"或"招标（采购）" → tender
     * 4. 其他 → other
     */
    function inferNoticeType(rawText) {
        if (!rawText) return 'other';
        if (rawText.indexOf('中标') >= 0 &&
            (rawText.indexOf('结果公告') >= 0 || rawText.indexOf('中标公告') >= 0)) {
            return 'award';
        }
        if (rawText.indexOf('更正公告') >= 0) return 'correction';
        if (rawText.indexOf('招标公告') >= 0 || rawText.indexOf('招标（采购）') >= 0) return 'tender';
        return 'other';
    }

    /**
     * 规范化文件名：去除扩展名，只保留字母/数字/中文/下划线/连字符，其余替换为下划线。
     * 用于生成稳定的 document_id 前缀。
     */
    function sanitizeFileName(fileName) {
        if (!fileName) return 'untitled';
        // 去除路径（兼容 \ 和 /）
        const base = fileName.replace(/^.*[\\\/]/, '');
        // 去除扩展名
        const noExt = base.replace(/\.[^.]+$/, '');
        // 只保留中文/字母/数字/下划线/连字符，其余替换为下划线
        const cleaned = noExt.replace(/[^\u4e00-\u9fa5A-Za-z0-9_-]/g, '_');
        return cleaned || 'untitled';
    }

    /**
     * 根据文件名 + 内容 SHA-256 短摘要生成稳定的 document_id。
     * 规则：sanitized_name + '_' + sha256前12位
     * 相同文件（文件名+内容）再次导入时生成相同 document_id，便于识别为同一文档。
     */
    function generateDocumentId(fileName, contentHash) {
        const name = sanitizeFileName(fileName);
        const shortHash = (contentHash || '').slice(0, 12);
        return name + '_' + shortHash;
    }

    /**
     * 判断标注文档是否包含非空数据（值或证据或备注）。
     * 用于导入新 TXT 前判断是否需要提示用户保存当前草稿。
     * 空白初始化（全 absent、无 values）不算非空。
     */
    function hasNonEmptyAnnotation(annotation) {
        if (!annotation || !annotation.fields) return false;
        return annotation.fields.some(field => {
            if (field.note && field.note.trim()) return true;
            if (field.values && field.values.length > 0) {
                return field.values.some(v =>
                    (v.raw_value && v.raw_value.trim()) ||
                    (v.normalized_value && v.normalized_value.trim()) ||
                    (v.acceptable_evidence_spans && v.acceptable_evidence_spans.length > 0)
                );
            }
            return false;
        });
    }

    /**
     * 为 TXT 导入创建空白标注文档（gold_status = '' 表示"待判断"）。
     * 与 createEmptyAnnotationDocument（absent）不同：
     * - '' 表示用户尚未判断，进度显示 0/6
     * - absent 表示用户明确标记为"不存在"，进度计入已完成
     * 导出时校验会拒绝 '' 状态，强制用户为每个字段选择合法状态。
     */
    function createBlankAnnotationForImport(documentId, annotatorId) {
        const doc = Schema.createEmptyAnnotationDocument(documentId, annotatorId);
        // 覆盖为"待判断"状态（空字符串），与 absent 区分
        doc.fields.forEach(f => { f.gold_status = ''; });
        return doc;
    }

    /**
     * 记住最后活动的 document_id，用于刷新后恢复。
     */
    function setLastActiveDoc(docId) {
        try {
            localStorage.setItem(STORAGE_KEYS.LAST_ACTIVE_DOC, docId || '');
        } catch (e) {
            console.warn('保存最后活动文档失败', e);
        }
    }

    function getLastActiveDoc() {
        try {
            return localStorage.getItem(STORAGE_KEYS.LAST_ACTIVE_DOC) || '';
        } catch (e) {
            return '';
        }
    }

    // ========== 完整校验（导出前 / 导入后） ==========

    /**
     * 对整个标注文档进行完整前端校验。
     * 校验规则与 GLM Pydantic Schema 保持一致，且更严格（不降低要求）。
     *
     * @param {object} annotation - 标注文档
     * @param {string} rawText - 当前原文（clean_raw_text）
     * @returns {{valid: boolean, errors: Array<{field:string, message:string, fieldIndex:number}>}}
     */
    function validateAnnotation(annotation, rawText) {
        const errors = [];

        if (!annotation || typeof annotation !== 'object') {
            return { valid: false, errors: [{ field: 'root', message: '标注文档不是有效对象', fieldIndex: -1 }], firstErrorFieldIndex: -1 };
        }

        // 1. 顶层必填字段
        if (!annotation.document_id) {
            errors.push({ field: 'document_id', message: '文档 ID 不能为空', fieldIndex: -1 });
        }
        if (!annotation.annotator_id) {
            errors.push({ field: 'annotator_id', message: '标注员 ID 不能为空', fieldIndex: -1 });
        }
        if (!annotation.annotation_version) {
            errors.push({ field: 'annotation_version', message: '标注规范版本不能为空', fieldIndex: -1 });
        }

        // 2. fields 必须是非空数组
        if (!Array.isArray(annotation.fields) || annotation.fields.length === 0) {
            errors.push({ field: 'fields', message: 'fields 必须是非空数组', fieldIndex: -1 });
            return { valid: false, errors, firstErrorFieldIndex: -1 };
        }

        // 3. 六类必填字段全部存在
        const presentFieldNames = annotation.fields.map(f => f && f.field_name);
        for (const reqName of Schema.CORE_FIELD_ORDER) {
            if (!presentFieldNames.includes(reqName)) {
                errors.push({ field: reqName, message: '缺少必填字段: ' + reqName, fieldIndex: -1 });
            }
        }

        // 4. 字段名不得重复
        const seenNames = new Set();
        annotation.fields.forEach((f, idx) => {
            if (!f) return;
            if (seenNames.has(f.field_name)) {
                errors.push({ field: f.field_name, message: '字段名重复: ' + f.field_name, fieldIndex: idx });
            }
            seenNames.add(f.field_name);
        });

        // 5. 逐字段校验
        annotation.fields.forEach((field, fieldIndex) => {
            if (!field) {
                errors.push({ field: 'fields[' + fieldIndex + ']', message: '字段对象为空', fieldIndex });
                return;
            }

            // 5.1 状态枚举合法
            const validStatuses = Object.values(Schema.GOLD_STATUS);
            if (!validStatuses.includes(field.gold_status)) {
                errors.push({ field: field.field_name || ('fields[' + fieldIndex + ']'), message: '非法状态枚举: ' + field.gold_status, fieldIndex });
                return;
            }

            // 5.2 present: 至少有一个 value
            if (field.gold_status === Schema.GOLD_STATUS.PRESENT) {
                if (!field.values || field.values.length === 0) {
                    errors.push({ field: field.field_name, message: 'present 状态至少需要一个 value', fieldIndex });
                }
            }

            // 5.3 absent/not_applicable/attachment_only/unreadable: values 必须为空
            const emptyRequiredStatuses = [
                Schema.GOLD_STATUS.ABSENT,
                Schema.GOLD_STATUS.NOT_APPLICABLE,
                Schema.GOLD_STATUS.ATTACHMENT_ONLY,
                Schema.GOLD_STATUS.UNREADABLE
            ];
            if (emptyRequiredStatuses.includes(field.gold_status)) {
                if (field.values && field.values.length > 0) {
                    errors.push({ field: field.field_name, message: field.gold_status + ' 状态 values 必须为空', fieldIndex });
                }
            }

            // 5.4 逐 value 校验
            if (field.values) {
                field.values.forEach((value, valueIndex) => {
                    if (!value) {
                        errors.push({ field: field.field_name, message: 'value[' + valueIndex + '] 为空', fieldIndex });
                        return;
                    }

                    // present: 每个 value 至少有一个 primary 证据
                    if (field.gold_status === Schema.GOLD_STATUS.PRESENT) {
                        const hasPrimary = value.acceptable_evidence_spans &&
                            value.acceptable_evidence_spans.some(e => e && e.role === Schema.EVIDENCE_ROLES.PRIMARY);
                        if (!hasPrimary) {
                            errors.push({ field: field.field_name, message: 'value[' + valueIndex + '] 至少需要一个 primary 证据', fieldIndex });
                        }
                    }

                    // 5.5 逐证据校验
                    if (value.acceptable_evidence_spans) {
                        value.acceptable_evidence_spans.forEach((ev, evIndex) => {
                            if (!ev) {
                                errors.push({ field: field.field_name, message: 'evidence[' + evIndex + '] 为空', fieldIndex });
                                return;
                            }

                            // 证据角色枚举合法
                            const validRoles = Object.values(Schema.EVIDENCE_ROLES);
                            if (!validRoles.includes(ev.role)) {
                                errors.push({ field: field.field_name, message: 'evidence[' + evIndex + '] 非法角色: ' + ev.role, fieldIndex });
                                return;
                            }

                            // start < end
                            if (!(typeof ev.start === 'number' && typeof ev.end === 'number' && ev.start < ev.end)) {
                                errors.push({ field: field.field_name, message: 'evidence[' + evIndex + '] start(' + ev.start + ') 必须 < end(' + ev.end + ')', fieldIndex });
                                return;
                            }

                            // rawText.slice(start, end) === evidence.text
                            const verify = Schema.verifyEvidenceSpan(rawText, ev.start, ev.end, ev.text);
                            if (!verify.valid) {
                                const expectedPreview = (ev.text || '').slice(0, 30);
                                const actualPreview = (verify.actualText || '').slice(0, 30);
                                errors.push({ field: field.field_name, message: 'evidence[' + evIndex + '] 偏移量与原文不匹配: 期望 "' + expectedPreview + '" 实际 "' + actualPreview + '"', fieldIndex });
                            }
                        });
                    }
                });
            }
        });

        const firstErrorFieldIndex = errors.length > 0
            ? (typeof errors[0].fieldIndex === 'number' ? errors[0].fieldIndex : -1)
            : -1;
        return { valid: errors.length === 0, errors, firstErrorFieldIndex };
    }

    // ========== DOM Range 选区偏移计算 ==========

    /**
     * 基于 DOM Range 计算选区在完整 clean_raw_text 中的绝对 UTF-16 偏移。
     * 遍历原文容器内所有文本节点，累计 UTF-16 code unit 长度。
     * 高亮 <span> 拆分文本节点后仍能正确计算。
     *
     * @param {Element} containerEl - 原文容器元素
     * @param {Node} node - Range 的 startContainer/endContainer
     * @param {number} offset - Range 的 startOffset/endOffset
     * @returns {number} 绝对 UTF-16 偏移；找不到时返回 -1
     */
    function getAbsoluteOffsetFromRange(containerEl, node, offset) {
        const walker = document.createTreeWalker(containerEl, NodeFilter.SHOW_TEXT, null);
        let absoluteOffset = 0;
        while (walker.nextNode()) {
            const current = walker.currentNode;
            if (current === node) {
                return absoluteOffset + offset;
            }
            // .length 是 UTF-16 code unit 数量，与 JavaScript String.length 一致
            absoluteOffset += current.nodeValue.length;
        }
        return -1;
    }

    /**
     * 获取鼠标选区的绝对偏移信息（基于 DOM Range，不使用 indexOf）。
     * 同一文本多次出现时也能准确定位到鼠标实际选中的位置。
     */
    function getSelectedTextInfo() {
        const selection = window.getSelection();
        if (!selection || selection.rangeCount === 0 || selection.isCollapsed) {
            return null;
        }

        const textEl = document.getElementById('rawText');
        const range = selection.getRangeAt(0);

        // 确认选中文本完全在 rawText 元素内
        if (!textEl.contains(range.startContainer) || !textEl.contains(range.endContainer)) {
            return null;
        }

        const selectedText = selection.toString();
        if (!selectedText) {
            return null;
        }

        // 基于 DOM Range 计算绝对 UTF-16 偏移
        const start = getAbsoluteOffsetFromRange(textEl, range.startContainer, range.startOffset);
        const end = getAbsoluteOffsetFromRange(textEl, range.endContainer, range.endOffset);

        if (start < 0 || end < 0) {
            console.error('无法计算选区绝对偏移（DOM Range 遍历失败）', { start, end });
            return null;
        }

        // 强制验证：rawText.slice(start, end) === selectedText
        // 无法唯一、准确定位时禁止保存，不静默回退到 indexOf
        const verify = Schema.verifyEvidenceSpan(state.rawText, start, end, selectedText);
        if (!verify.valid) {
            console.error('选区偏移量最终验证失败', {
                start, end,
                selectedText: selectedText.slice(0, 50),
                actualText: verify.actualText.slice(0, 50)
            });
            return null;
        }

        return { start, end, text: selectedText };
    }

    function updateSelectionInfo() {
        const info = getSelectedTextInfo();
        const infoEl = document.getElementById('selectionInfo');
        if (info) {
            const preview = info.text.length > 30 ? info.text.slice(0, 30) + '…' : info.text;
            infoEl.textContent = '已选中 [' + info.start + ', ' + info.end + ') 共 ' + (info.end - info.start) + ' 字符：' + preview;
        } else {
            infoEl.textContent = '未选中文本';
        }
    }

    // ========== localStorage 按文档隔离 ==========

    function getDraftKey(docId) {
        return STORAGE_KEYS.DRAFT_PREFIX + (docId || 'default');
    }

    function getMetaKey(docId) {
        return STORAGE_KEYS.META_PREFIX + (docId || 'default');
    }

    function loadDocIndex() {
        try {
            const raw = localStorage.getItem(STORAGE_KEYS.DOC_INDEX);
            return raw ? JSON.parse(raw) : [];
        } catch (e) {
            console.warn('文档索引解析失败', e);
            return [];
        }
    }

    function saveDocIndex(index) {
        try {
            localStorage.setItem(STORAGE_KEYS.DOC_INDEX, JSON.stringify(index));
        } catch (e) {
            console.warn('保存文档索引失败', e);
        }
    }

    function updateDocIndex(docId, title, annotationStatus) {
        if (!docId) return;
        const index = loadDocIndex();
        const existing = index.find(d => d.document_id === docId);
        const entry = {
            document_id: docId,
            title: title || '(无原文)',
            updated_at: new Date().toISOString(),
            annotation_status: annotationStatus || 'pending'
        };
        if (existing) {
            Object.assign(existing, entry);
        } else {
            index.push(entry);
        }
        saveDocIndex(index);
    }

    function removeFromDocIndex(docId) {
        const index = loadDocIndex();
        const filtered = index.filter(d => d.document_id !== docId);
        saveDocIndex(filtered);
    }

    function saveDocMeta(docId, meta) {
        if (!docId) return;
        try {
            localStorage.setItem(getMetaKey(docId), JSON.stringify(meta));
        } catch (e) {
            console.warn('保存文档元数据失败', e);
        }
    }

    function loadDocMeta(docId) {
        try {
            const raw = localStorage.getItem(getMetaKey(docId));
            return raw ? JSON.parse(raw) : null;
        } catch (e) {
            return null;
        }
    }

    // ========== 初始化 ==========

    function init() {
        // 优先恢复最后活动的文档，没有则使用 sample-001
        const lastActiveDoc = getLastActiveDoc();
        const initialDocId = lastActiveDoc || 'sample-001';

        // 尝试从 localStorage 恢复该文档的草稿
        const saved = loadFromStorage(initialDocId);
        if (saved && saved.annotation) {
            state.annotation = saved.annotation;
            state.rawText = saved.rawText || '';
        } else if (initialDocId === 'sample-001') {
            // 使用示例数据初始化
            state.annotation = JSON.parse(JSON.stringify(SampleData.SAMPLE_ANNOTATION));
            state.rawText = SampleData.SAMPLE_RAW_TEXT;
        } else {
            // 最后活动文档无草稿，回退到 sample-001
            state.annotation = JSON.parse(JSON.stringify(SampleData.SAMPLE_ANNOTATION));
            state.rawText = SampleData.SAMPLE_RAW_TEXT;
            setLastActiveDoc('sample-001');
        }

        // P0-2: 为所有 value 分配 ui_id（草稿中可能缺失）
        ensureAllValuesHaveUiId(state.annotation);
        // 重置折叠状态（新文档加载时不保留旧折叠）
        state.valueCollapsed = {};

        // 默认 noticeType：优先使用 SAMPLE_NOTICE_TYPE（根据原文推断）
        const defaultNoticeType = (typeof SampleData !== 'undefined' && SampleData.SAMPLE_NOTICE_TYPE)
            ? SampleData.SAMPLE_NOTICE_TYPE : 'tender';

        // 加载文档元数据（noticeType / annotationStatus）
        const meta = loadDocMeta(initialDocId);
        state.docMeta = meta || { noticeType: defaultNoticeType, annotationStatus: 'pending' };

        // 初始化当前字段索引为第一个字段
        state.currentFieldIndex = 0;

        // 同步表单值
        syncDocInfoFromState();

        // 渲染
        renderText();
        renderFields();
        updateProgress();

        // 更新文档状态显示
        updateDocStatusDisplay(
            initialDocId === 'sample-001' ? 'sample-001.txt' : '',
            initialDocId, '', !!saved
        );

        // 绑定事件
        bindEvents();
    }

    function syncDocInfoFromState() {
        document.getElementById('documentId').value = state.annotation.document_id || '';
        document.getElementById('annotatorId').value = state.annotation.annotator_id || '';
        document.getElementById('annotationVersion').value = state.annotation.annotation_version || '1.0';
        document.getElementById('noticeType').value = state.docMeta.noticeType || 'tender';
        document.getElementById('annotationStatus').value = state.docMeta.annotationStatus || 'pending';
    }

    // ========== 文档切换 ==========

    /**
     * 切换文档：保存当前草稿，加载目标文档草稿（如不存在则新建空文档）。
     * 文档 B 不得恢复文档 A 的草稿。
     */
    function switchDocument(newDocId) {
        const oldDocId = state.annotation.document_id || 'default';
        if (newDocId === oldDocId) return;

        // 切换前保存当前草稿
        saveToStorage();

        // 尝试加载目标文档的草稿
        const saved = loadFromStorage(newDocId);
        let restoredFromDraft = false;
        if (saved && saved.annotation) {
            state.annotation = saved.annotation;
            state.rawText = saved.rawText || '';
            restoredFromDraft = true;
        } else {
            // 新文档，从空白开始（不携带文档 A 的内容）
            const annotatorId = state.annotation.annotator_id || 'A';
            state.annotation = Schema.createEmptyAnnotationDocument(newDocId, annotatorId);
            state.rawText = '';
        }

        // 加载目标文档的元数据
        const meta = loadDocMeta(newDocId);
        state.docMeta = meta || { noticeType: 'tender', annotationStatus: 'pending' };

        // 重置编辑状态
        state.currentFieldIndex = 0;
        state.currentValueIndex = -1;
        state.editingEvidenceIndex = -1;
        // P0-2: 重置折叠状态和待聚焦 ui_id
        state.valueCollapsed = {};
        state.pendingFocusUiId = null;

        // P0-2: 为所有 value 分配 ui_id
        ensureAllValuesHaveUiId(state.annotation);

        // 清除重叠提示
        const overlapWarning = document.getElementById('overlapWarning');
        if (overlapWarning) overlapWarning.style.display = 'none';

        // 重新渲染
        syncDocInfoFromState();
        renderText();
        renderFields();
        updateProgress();

        // 更新文档状态显示 + 记住最后活动文档
        updateDocStatusDisplay('', newDocId, '', restoredFromDraft);
        setLastActiveDoc(newDocId);
    }

    // ========== 文本渲染和证据高亮 ==========

    /**
     * 渲染原文，并对已标注的证据片段进行高亮回显。
     * 检测证据重叠并提示用户。
     */
    function renderText() {
        const textEl = document.getElementById('rawText');
        // 清空（不用 innerHTML，避免残留）
        while (textEl.firstChild) {
            textEl.removeChild(textEl.firstChild);
        }

        if (!state.rawText) {
            document.getElementById('charCount').textContent = '字符数：0';
            return;
        }

        // 收集所有证据片段用于高亮
        const highlights = [];
        state.annotation.fields.forEach(field => {
            if (field.values) {
                field.values.forEach(value => {
                    if (value.acceptable_evidence_spans) {
                        value.acceptable_evidence_spans.forEach(ev => {
                            if (ev && typeof ev.start === 'number' && typeof ev.end === 'number') {
                                highlights.push({
                                    start: ev.start,
                                    end: ev.end,
                                    role: ev.role,
                                    fieldName: field.field_name
                                });
                            }
                        });
                    }
                });
            }
        });

        // 检测证据重叠
        const sortedForOverlap = [...highlights].sort((a, b) => a.start - b.start);
        let hasOverlap = false;
        const overlapPairs = [];
        for (let i = 1; i < sortedForOverlap.length; i++) {
            if (sortedForOverlap[i].start < sortedForOverlap[i - 1].end) {
                hasOverlap = true;
                overlapPairs.push({
                    a: sortedForOverlap[i - 1],
                    b: sortedForOverlap[i]
                });
            }
        }

        const overlapWarning = document.getElementById('overlapWarning');
        if (overlapWarning) {
            if (hasOverlap) {
                overlapWarning.textContent = '⚠️ 检测到证据重叠，请检查偏移量（' + overlapPairs.length + ' 处重叠）';
                overlapWarning.style.display = 'block';
            } else {
                overlapWarning.style.display = 'none';
            }
        }

        // 渲染文本 + 高亮
        if (highlights.length === 0) {
            textEl.textContent = state.rawText;
        } else {
            // 按起始偏移排序
            highlights.sort((a, b) => a.start - b.start);

            let lastEnd = 0;
            const fragment = document.createDocumentFragment();

            for (const hl of highlights) {
                // 跳过越界的证据
                if (hl.start < 0 || hl.end > state.rawText.length || hl.start >= hl.end) continue;

                // 高亮之前的普通文本
                if (hl.start > lastEnd) {
                    fragment.appendChild(document.createTextNode(state.rawText.slice(lastEnd, hl.start)));
                }
                // 高亮 span
                const span = document.createElement('span');
                span.className = 'evidence-highlight evidence-role-' + hl.role;
                span.textContent = state.rawText.slice(hl.start, hl.end);
                fragment.appendChild(span);
                lastEnd = Math.max(lastEnd, hl.end);
            }
            // 剩余文本
            if (lastEnd < state.rawText.length) {
                fragment.appendChild(document.createTextNode(state.rawText.slice(lastEnd)));
            }

            textEl.appendChild(fragment);
        }

        document.getElementById('charCount').textContent = '字符数：' + state.rawText.length;
    }

    // ========== 字段渲染 ==========

    function renderFields() {
        // 1. 渲染六字段紧凑导航
        renderFieldsNav();

        // 2. 渲染当前选中字段的编辑器（一次只显示一个）
        const container = document.getElementById('fieldsContainer');

        // P0-2 状态保持：保存滚动位置和当前字段索引
        const savedScrollTop = container.scrollTop;
        const savedFieldIndex = state.currentFieldIndex;

        while (container.firstChild) {
            container.removeChild(container.firstChild);
        }

        // 只在无效时回退到 0，不主动重置有效值（P0-2 修复）
        if (state.currentFieldIndex < 0 || state.currentFieldIndex >= state.annotation.fields.length) {
            state.currentFieldIndex = 0;
        }

        const currentField = state.annotation.fields[state.currentFieldIndex];
        if (currentField) {
            const card = createFieldCard(currentField, state.currentFieldIndex);
            container.appendChild(card);
        }

        // P0-2 状态保持：恢复滚动位置（仅在字段未变时）
        if (savedFieldIndex === state.currentFieldIndex) {
            container.scrollTop = savedScrollTop;
        }

        // P0-1 添加新值后自动滚动+聚焦
        if (state.pendingFocusUiId) {
            const targetItem = container.querySelector('.value-item[data-ui-id="' + CSS.escape(state.pendingFocusUiId) + '"]');
            if (targetItem) {
                targetItem.classList.add('value-just-added');
                targetItem.scrollIntoView({ behavior: 'smooth', block: 'center' });
                const rawInput = targetItem.querySelector('input[data-field-key="raw_value"]');
                if (rawInput) rawInput.focus();
            }
            state.pendingFocusUiId = null;
        }
    }

    /**
     * 渲染六字段紧凑导航：每项显示名称、状态、值数量、完成状态。
     * 未选字段只显示概要信息；点击切换当前编辑字段。
     */
    function renderFieldsNav() {
        const nav = document.getElementById('fieldsNav');
        if (!nav) return;
        while (nav.firstChild) {
            nav.removeChild(nav.firstChild);
        }

        state.annotation.fields.forEach((field, fieldIndex) => {
            const item = document.createElement('button');
            item.type = 'button';
            item.className = 'field-nav-item';
            item.dataset.fieldIndex = fieldIndex;
            if (fieldIndex === state.currentFieldIndex) {
                item.classList.add('active');
                item.setAttribute('aria-current', 'true');
            }

            // 字段名称
            const nameSpan = document.createElement('span');
            nameSpan.className = 'nav-field-name';
            nameSpan.textContent = Schema.FIELD_DISPLAY_NAMES[field.field_name] || field.field_name;
            item.appendChild(nameSpan);

            // 状态徽章
            const statusBadge = document.createElement('span');
            statusBadge.className = 'status-badge status-' + field.gold_status;
            statusBadge.textContent = Schema.STATUS_DISPLAY_NAMES[field.gold_status] || field.gold_status;
            item.appendChild(statusBadge);

            // 值数量 + 完成状态
            const metaSpan = document.createElement('span');
            metaSpan.className = 'nav-field-meta';
            const valueCount = (field.values && field.values.length) || 0;
            const isComplete = isFieldComplete(field);
            metaSpan.textContent = valueCount + ' 值' + (isComplete ? ' · ✓' : '');
            item.appendChild(metaSpan);

            item.addEventListener('click', () => {
                switchToField(fieldIndex);
            });

            nav.appendChild(item);
        });
    }

    /**
     * 判断字段是否完成标注（用于导航项显示完成状态）。
     */
    function isFieldComplete(field) {
        if (!field) return false;
        const presentStatuses = [Schema.GOLD_STATUS.PRESENT, Schema.GOLD_STATUS.AMBIGUOUS];
        if (presentStatuses.includes(field.gold_status)) {
            return field.values && field.values.length > 0 &&
                field.values.some(v => v.acceptable_evidence_spans &&
                    v.acceptable_evidence_spans.some(e => e && e.role === Schema.EVIDENCE_ROLES.PRIMARY));
        }
        // 非 present 状态（absent/not_applicable 等）也算标记完成
        return field.gold_status !== '' && field.gold_status !== Schema.GOLD_STATUS.PRESENT;
    }

    function createFieldCard(field, fieldIndex) {
        const card = document.createElement('div');
        card.className = 'field-card';
        card.dataset.fieldIndex = fieldIndex;

        // 头部
        const header = document.createElement('div');
        header.className = 'field-card-header';

        const titleDiv = document.createElement('div');
        titleDiv.className = 'field-title';

        const nameSpan = document.createElement('span');
        nameSpan.className = 'field-name';
        nameSpan.textContent = Schema.FIELD_DISPLAY_NAMES[field.field_name] || field.field_name;

        const statusBadge = document.createElement('span');
        statusBadge.className = 'status-badge status-' + field.gold_status;
        statusBadge.textContent = Schema.STATUS_DISPLAY_NAMES[field.gold_status] || field.gold_status;

        titleDiv.appendChild(nameSpan);
        titleDiv.appendChild(statusBadge);

        const statusSelect = document.createElement('select');
        statusSelect.className = 'field-status-select';
        Object.values(Schema.GOLD_STATUS).forEach(status => {
            const opt = document.createElement('option');
            opt.value = status;
            opt.textContent = Schema.STATUS_DISPLAY_NAMES[status];
            if (status === field.gold_status) opt.selected = true;
            statusSelect.appendChild(opt);
        });
        statusSelect.addEventListener('change', (e) => {
            changeFieldStatus(fieldIndex, e.target.value);
        });

        header.appendChild(titleDiv);
        header.appendChild(statusSelect);

        // 主体
        const body = document.createElement('div');
        body.className = 'field-card-body';

        // 值列表（present / ambiguous 状态显示）
        if (field.gold_status === Schema.GOLD_STATUS.PRESENT || field.gold_status === Schema.GOLD_STATUS.AMBIGUOUS) {
            const valuesList = document.createElement('div');
            valuesList.className = 'values-list';

            field.values.forEach((value, valueIndex) => {
                valuesList.appendChild(createValueItem(field, fieldIndex, value, valueIndex));
            });

            // 添加值按钮
            const addBtn = document.createElement('button');
            addBtn.className = 'add-value-btn';
            addBtn.textContent = '+ 添加字段值';
            addBtn.addEventListener('click', () => addFieldValue(fieldIndex));
            valuesList.appendChild(addBtn);

            body.appendChild(valuesList);
        }

        // 备注
        const noteSection = document.createElement('div');
        noteSection.className = 'note-section';

        const noteLabel = document.createElement('label');
        noteLabel.textContent = '备注';
        noteSection.appendChild(noteLabel);

        const noteTextarea = document.createElement('textarea');
        noteTextarea.value = field.note || '';
        noteTextarea.placeholder = '不确定项说明...';
        noteTextarea.addEventListener('input', (e) => {
            field.note = e.target.value;
            scheduleAutoSave();
        });
        noteSection.appendChild(noteTextarea);

        body.appendChild(noteSection);

        card.appendChild(header);
        card.appendChild(body);

        return card;
    }

    function createValueItem(field, fieldIndex, value, valueIndex) {
        const item = document.createElement('div');
        item.className = 'value-item';

        // P0-2 稳定标识：data-ui-id 用于跨 renderFields 保持状态
        ensureValueUiId(value);
        item.setAttribute('data-ui-id', value.ui_id);

        // P0-2 折叠状态恢复
        if (state.valueCollapsed[value.ui_id]) {
            item.classList.add('collapsed');
        }

        // 值头部
        const header = document.createElement('div');
        header.className = 'value-header';

        // P0-2 折叠/展开按钮
        const collapseBtn = document.createElement('button');
        collapseBtn.className = 'value-collapse-btn';
        collapseBtn.type = 'button';
        collapseBtn.title = '展开/折叠';
        collapseBtn.textContent = state.valueCollapsed[value.ui_id] ? '▶' : '▼';
        collapseBtn.addEventListener('click', () => {
            const isCollapsed = item.classList.toggle('collapsed');
            state.valueCollapsed[value.ui_id] = isCollapsed;
            collapseBtn.textContent = isCollapsed ? '▶' : '▼';
        });
        header.appendChild(collapseBtn);

        const idxSpan = document.createElement('span');
        idxSpan.className = 'value-index';
        idxSpan.textContent = '值 ' + (valueIndex + 1);

        const actionsDiv = document.createElement('div');
        actionsDiv.className = 'value-actions';

        const delBtn = document.createElement('button');
        delBtn.className = 'btn btn-danger btn-small';
        delBtn.textContent = '删除';
        delBtn.addEventListener('click', () => removeFieldValue(fieldIndex, valueIndex));
        actionsDiv.appendChild(delBtn);

        header.appendChild(idxSpan);
        header.appendChild(actionsDiv);

        // 值字段
        const fieldsDiv = document.createElement('div');
        fieldsDiv.className = 'value-fields';

        // raw_value
        fieldsDiv.appendChild(createValueField('raw_value', '原始值', value.raw_value, 'text', (v) => {
            value.raw_value = v;
            scheduleAutoSave();
        }, true));

        // normalized_value
        fieldsDiv.appendChild(createValueField('normalized_value', '归一化值', value.normalized_value || '', 'text', (v) => {
            value.normalized_value = v || null;
            scheduleAutoSave();
        }));

        // 金额类型字段（仅 amount 字段）
        if (field.field_name === Schema.CORE_FIELD_NAMES.AMOUNT) {
            // amount_type
            const amtTypeDiv = document.createElement('div');
            amtTypeDiv.className = 'value-field';
            const amtLabel = document.createElement('label');
            amtLabel.textContent = '金额类型';
            const amtSelect = document.createElement('select');
            Object.values(Schema.AMOUNT_TYPES).forEach(type => {
                const opt = document.createElement('option');
                opt.value = type;
                opt.textContent = Schema.AMOUNT_TYPE_DISPLAY_NAMES[type];
                if (type === value.amount_type) opt.selected = true;
                amtSelect.appendChild(opt);
            });
            amtSelect.addEventListener('change', (e) => {
                value.amount_type = e.target.value || null;
                scheduleAutoSave();
            });
            amtTypeDiv.appendChild(amtLabel);
            amtTypeDiv.appendChild(amtSelect);
            fieldsDiv.appendChild(amtTypeDiv);

            // currency
            fieldsDiv.appendChild(createValueField('currency', '币种', value.currency || '', 'text', (v) => {
                value.currency = v || null;
                scheduleAutoSave();
            }));

            // original_unit
            fieldsDiv.appendChild(createValueField('original_unit', '原始单位', value.original_unit || '', 'text', (v) => {
                value.original_unit = v || null;
                scheduleAutoSave();
            }));

            // tax_status
            const taxDiv = document.createElement('div');
            taxDiv.className = 'value-field';
            const taxLabel = document.createElement('label');
            taxLabel.textContent = '含税状态';
            const taxSelect = document.createElement('select');
            ['', 'included', 'excluded', 'unknown'].forEach(status => {
                const opt = document.createElement('option');
                opt.value = status;
                opt.textContent = status ? status : '(空)';
                if (status === value.tax_status) opt.selected = true;
                taxSelect.appendChild(opt);
            });
            taxSelect.addEventListener('change', (e) => {
                value.tax_status = e.target.value || null;
                scheduleAutoSave();
            });
            taxDiv.appendChild(taxLabel);
            taxDiv.appendChild(taxSelect);
            fieldsDiv.appendChild(taxDiv);
        }

        // lot_id
        fieldsDiv.appendChild(createValueField('lot_id', '分包ID', value.lot_id || '', 'text', (v) => {
            value.lot_id = v || null;
            scheduleAutoSave();
        }));

        // 证据列表
        const evidenceSection = document.createElement('div');
        evidenceSection.className = 'evidence-section';

        const evHeader = document.createElement('div');
        evHeader.className = 'evidence-header';
        const evLabel = document.createElement('span');
        evLabel.className = 'evidence-label';
        evLabel.textContent = '合法证据片段（' + value.acceptable_evidence_spans.length + '）';
        evHeader.appendChild(evLabel);
        evidenceSection.appendChild(evHeader);

        const evList = document.createElement('div');
        evList.className = 'evidence-list';
        value.acceptable_evidence_spans.forEach((ev, evIndex) => {
            evList.appendChild(createEvidenceItem(ev, evIndex, fieldIndex, valueIndex));
        });
        evidenceSection.appendChild(evList);

        // 添加证据按钮
        const addEvBtn = document.createElement('button');
        addEvBtn.className = 'add-evidence-btn';
        addEvBtn.textContent = '+ 添加选中的文本为证据';
        addEvBtn.addEventListener('click', () => addEvidenceFromSelection(fieldIndex, valueIndex));
        evidenceSection.appendChild(addEvBtn);

        item.appendChild(header);
        item.appendChild(fieldsDiv);
        item.appendChild(evidenceSection);

        return item;
    }

    function createValueField(key, label, value, type, onChange, fullWidth) {
        const div = document.createElement('div');
        div.className = 'value-field' + (fullWidth ? ' full-width' : '');

        const lbl = document.createElement('label');
        lbl.textContent = label;

        const input = document.createElement('input');
        input.type = type;
        input.value = value;
        // P0-1: data-field-key 用于 addFieldValue 后自动聚焦 raw_value
        input.setAttribute('data-field-key', key);
        input.addEventListener('input', (e) => onChange(e.target.value));

        div.appendChild(lbl);
        div.appendChild(input);
        return div;
    }

    function createEvidenceItem(evidence, evIndex, fieldIndex, valueIndex) {
        const item = document.createElement('div');
        item.className = 'evidence-item';
        // data 属性用于 focusEvidenceInText 定位
        item.setAttribute('data-ev-index', evIndex);
        item.setAttribute('data-field', fieldIndex);
        item.setAttribute('data-value', valueIndex);
        item.title = '点击定位到原文位置';

        // 角色标签
        const roleTag = document.createElement('span');
        roleTag.className = 'evidence-role-tag evidence-role-' + evidence.role;
        roleTag.textContent = evidence.role;
        item.appendChild(roleTag);

        // 文本预览（用 textContent，防 XSS）
        const textSpan = document.createElement('span');
        textSpan.className = 'evidence-text-preview';
        textSpan.textContent = evidence.text;
        textSpan.title = evidence.text;
        item.appendChild(textSpan);

        // 偏移量
        const offsetSpan = document.createElement('span');
        offsetSpan.className = 'evidence-offset';
        offsetSpan.textContent = '[' + evidence.start + ', ' + evidence.end + ')';
        item.appendChild(offsetSpan);

        // 哈希/slice 失效检测：若失效，添加失效标记
        const verify = Schema.verifyEvidenceSpan(state.rawText, evidence.start, evidence.end, evidence.text);
        if (!verify.valid) {
            item.classList.add('evidence-invalid');
            const invalidTag = document.createElement('span');
            invalidTag.className = 'evidence-invalid-tag';
            invalidTag.textContent = '证据已失效';
            item.appendChild(invalidTag);
        }

        // 点击证据项（非按钮区域）→ 滚动并高亮原文
        item.addEventListener('click', (e) => {
            // 点击编辑/删除按钮时不触发定位
            if (e.target.closest('button')) return;
            focusEvidenceInText(evIndex, fieldIndex, valueIndex);
        });

        // 操作按钮
        const actions = document.createElement('div');
        actions.className = 'evidence-item-actions';

        const editBtn = document.createElement('button');
        editBtn.className = 'icon-btn';
        editBtn.textContent = '✎';
        editBtn.title = '编辑';
        editBtn.addEventListener('click', () => openEvidenceModal(fieldIndex, valueIndex, evIndex));
        actions.appendChild(editBtn);

        const delBtn = document.createElement('button');
        delBtn.className = 'icon-btn delete';
        delBtn.textContent = '×';
        delBtn.title = '删除';
        delBtn.addEventListener('click', () => removeEvidence(fieldIndex, valueIndex, evIndex));
        actions.appendChild(delBtn);

        item.appendChild(actions);

        return item;
    }

    // ========== 字段操作 ==========

    function changeFieldStatus(fieldIndex, newStatus) {
        const field = state.annotation.fields[fieldIndex];
        field.gold_status = newStatus;

        // present / ambiguous 状态允许有值
        // 其他状态（absent / not_applicable / attachment_only / unreadable）必须清空 values
        if (newStatus !== Schema.GOLD_STATUS.PRESENT && newStatus !== Schema.GOLD_STATUS.AMBIGUOUS) {
            field.values = [];
        }

        // present 时确保至少有一个空值
        if (newStatus === Schema.GOLD_STATUS.PRESENT && field.values.length === 0) {
            const emptyVal = Schema.createEmptyValue();
            ensureValueUiId(emptyVal);
            field.values.push(emptyVal);
        }

        renderFields();
        updateProgress();
        scheduleAutoSave();
    }

    function addFieldValue(fieldIndex) {
        const field = state.annotation.fields[fieldIndex];
        const newValue = Schema.createEmptyValue();
        ensureValueUiId(newValue);
        field.values.push(newValue);
        // P0-1: 设置待聚焦 ui_id，renderFields 后自动滚动+展开+聚焦
        state.pendingFocusUiId = newValue.ui_id;
        // 确保新值不折叠
        if (state.valueCollapsed[newValue.ui_id]) {
            delete state.valueCollapsed[newValue.ui_id];
        }
        renderFields();
        scheduleAutoSave();
    }

    function removeFieldValue(fieldIndex, valueIndex) {
        const field = state.annotation.fields[fieldIndex];
        // P0-2: 记录被删值的 ui_id，清理折叠状态
        const removedUiId = field.values[valueIndex] ? field.values[valueIndex].ui_id : null;
        if (field.values.length <= 1) {
            if (!confirm('至少需要保留一个值。确定要删除吗？这将把字段状态改为"不存在"。')) {
                return;
            }
            field.values = [];
            field.gold_status = Schema.GOLD_STATUS.ABSENT;
        } else {
            field.values.splice(valueIndex, 1);
        }
        // 清理被删值的折叠状态
        if (removedUiId && state.valueCollapsed[removedUiId]) {
            delete state.valueCollapsed[removedUiId];
        }
        // P0-2: 不改变 currentFieldIndex，保持当前字段
        renderFields();
        renderText(); // 更新高亮
        updateProgress();
        scheduleAutoSave();
    }

    // ========== 证据操作 ==========

    /**
     * 上下文预览长度：前后各 20 字符（UTF-16 code unit）。
     */
    const CONTEXT_RADIUS = 20;

    /**
     * 证据非阻塞质量提示词表：
     * - 金额字段：若选中文字及其上下文窗口内不含任一金额类型词，提示"证据可能不完整"。
     * - 企业字段：若不含任一角色词，提示"补充上下文（采购人/中标人等）"。
     * - 日期字段：若不含任一日期类型词，提示"补充上下文（发布/截止/开标）"。
     * 仅提示，不阻止保存，不强制固定格式。
     */
    const EVIDENCE_QUALITY_KEYWORDS = {
        amount: {
            field: 'amount',
            label: '金额证据',
            words: ['预算', '限价', '中标', '成交', '合同', '单价', '金额', '报价', '估算', '控制价', '最高限价', '成交价', '合同价'],
            suggestion: '未检测到"预算/限价/中标/成交/合同/单价"等金额类型词，证据可能不完整。建议用「向左/向右扩展1字」或「重新选择」补齐类型词与单位。'
        },
        enterprise: {
            field: 'enterprise',
            label: '企业证据',
            words: ['采购人', '中标人', '供应商', '代理机构', '投标人', '申请人', '承包方', '甲方', '乙方', '受让方', '出让方'],
            suggestion: '未检测到"采购人/中标人/供应商/代理机构"等角色词，建议补充上下文以明确主体角色。'
        },
        date: {
            field: 'date',
            label: '日期证据',
            words: ['发布', '截止', '开标', '公示', '公告', '报名', '递交', '开启', '中标', '成交', '签订', '履行'],
            suggestion: '未检测到"发布/截止/开标"等日期类型词，建议补充上下文以明确日期语义。'
        }
    };

    /**
     * 根据字段名推断证据质量检查类别。
     * project_identifier / winner_name / purchaser_name → enterprise
     * amount → amount
     * publish_date / bid_deadline → date
     * 其他 → null（不检查）
     */
    function inferEvidenceCategory(fieldName) {
        if (!fieldName) return null;
        if (fieldName === 'amount') return 'amount';
        if (fieldName === 'purchaser_name' || fieldName === 'winner_name' || fieldName === 'project_identifier') return 'enterprise';
        if (fieldName === 'publish_date' || fieldName === 'bid_deadline') return 'date';
        return null;
    }

    /**
     * 检查证据在"选中文字 + 前后 CONTEXT_RADIUS 字符"窗口内是否含有类型词。
     * @returns {{missing: boolean, category: string, suggestion: string} | null}
     */
    function checkEvidenceQuality(fieldName, start, end) {
        const cat = inferEvidenceCategory(fieldName);
        if (!cat) return null;
        const rule = EVIDENCE_QUALITY_KEYWORDS[cat];
        if (!rule) return null;

        const ctxStart = Math.max(0, start - CONTEXT_RADIUS);
        const ctxEnd = Math.min(state.rawText.length, end + CONTEXT_RADIUS);
        const window = state.rawText.slice(ctxStart, ctxEnd);

        const missing = !rule.words.some(w => window.indexOf(w) >= 0);
        return { missing, category: cat, suggestion: rule.suggestion, label: rule.label };
    }

    /**
     * 打开证据预览弹窗。用户选中文本后点击「添加证据」触发。
     * 弹窗显示：选中文字 + 前后 20 字上下文 + start/end + slice 验证 + 非阻塞质量提示。
     * 支持「向左扩展1字 / 向右扩展1字 / 重新选择」。
     * 支持选择证据角色（primary / qualifier 等），便于一个字段保存多段证据。
     */
    function addEvidenceFromSelection(fieldIndex, valueIndex) {
        const selection = getSelectedTextInfo();
        if (!selection) {
            alert('请先在左侧原文中选中一段文本。\n\n如果已选中文本仍提示此错误，可能是选区跨出了原文区域，或偏移量验证不通过。');
            return;
        }

        // 验证
        const verify = Schema.verifyEvidenceSpan(state.rawText, selection.start, selection.end, selection.text);
        if (!verify.valid) {
            alert('偏移量验证失败，请重新选择文本');
            return;
        }

        // 记录目标字段/值索引，供预览弹窗的扩展/重选/保存使用
        state.currentFieldIndex = fieldIndex;
        state.currentValueIndex = valueIndex;
        state.editingEvidenceIndex = -1; // -1 表示新增（非编辑）

        // 默认角色 primary
        document.getElementById('previewRole').value = Schema.EVIDENCE_ROLES.PRIMARY;

        renderEvidencePreview(selection.start, selection.end, selection.text);
        document.getElementById('evidencePreviewModal').classList.remove('hidden');
    }

    /**
     * 渲染证据预览弹窗内容：偏移量、上下文、slice 验证、质量提示。
     * XSS 防护：所有用户控制文本通过 textContent / createElement 渲染。
     */
    function renderEvidencePreview(start, end, text) {
        // 偏移量
        document.getElementById('previewStart').value = start;
        document.getElementById('previewEnd').value = end;
        document.getElementById('previewLen').value = end - start;

        // 选中文字
        document.getElementById('previewText').value = text;

        // 上下文：前 20 字 + 选中（高亮）+ 后 20 字
        const ctxStart = Math.max(0, start - CONTEXT_RADIUS);
        const ctxEnd = Math.min(state.rawText.length, end + CONTEXT_RADIUS);
        const prefix = state.rawText.slice(ctxStart, start);
        const suffix = state.rawText.slice(end, ctxEnd);

        const ctxEl = document.getElementById('previewContext');
        while (ctxEl.firstChild) ctxEl.removeChild(ctxEl.firstChild);

        if (prefix) {
            const preSpan = document.createElement('span');
            preSpan.className = 'preview-prefix';
            preSpan.textContent = prefix;
            ctxEl.appendChild(preSpan);
        }
        const selSpan = document.createElement('span');
        selSpan.className = 'preview-selected';
        selSpan.textContent = text;
        ctxEl.appendChild(selSpan);
        if (suffix) {
            const sufSpan = document.createElement('span');
            sufSpan.className = 'preview-suffix';
            sufSpan.textContent = suffix;
            ctxEl.appendChild(sufSpan);
        }

        // slice 验证
        const verify = Schema.verifyEvidenceSpan(state.rawText, start, end, text);
        const verifyEl = document.getElementById('previewVerify');
        while (verifyEl.firstChild) verifyEl.removeChild(verifyEl.firstChild);
        const vIcon = document.createElement('span');
        vIcon.className = 'verify-icon';
        const vMsg = document.createElement('span');
        if (verify.valid) {
            verifyEl.className = 'verify-status';
            vIcon.textContent = '✓';
            vMsg.textContent = 'slice 验证通过：rawText.slice(' + start + ', ' + end + ') === 选中文字';
        } else {
            verifyEl.className = 'verify-status error';
            vIcon.textContent = '✗';
            vMsg.textContent = 'slice 验证失败：实际文本为 "' + (verify.actualText || '').slice(0, 50) + '..."';
        }
        verifyEl.appendChild(vIcon);
        verifyEl.appendChild(vMsg);

        // 非阻塞质量提示（金额/企业/日期）
        renderEvidenceQualityHint(start, end);
    }

    /**
     * 渲染证据非阻塞质量提示。
     * 只提示，不阻止保存。
     */
    function renderEvidenceQualityHint(start, end) {
        const fieldName = state.annotation && state.annotation.fields[state.currentFieldIndex]
            ? state.annotation.fields[state.currentFieldIndex].field_name
            : null;
        const result = checkEvidenceQuality(fieldName, start, end);

        const hintEl = document.getElementById('previewQualityHint');
        while (hintEl.firstChild) hintEl.removeChild(hintEl.firstChild);

        if (!result || !result.missing) {
            hintEl.classList.add('hidden');
            return;
        }

        hintEl.classList.remove('hidden');
        const title = document.createElement('div');
        title.className = 'quality-hint-title';
        title.textContent = '⚠️ ' + result.label + ' 质量提示（非阻塞，可继续保存）';
        hintEl.appendChild(title);

        const sugg = document.createElement('div');
        sugg.className = 'quality-hint-suggestion';
        sugg.textContent = result.suggestion;
        hintEl.appendChild(sugg);
    }

    /**
     * 向左扩展 1 字：start -= 1（不低于 0），text 同步更新。
     * 扩展后重新校验 slice 一致性。
     */
    function expandEvidenceLeft() {
        let start = parseInt(document.getElementById('previewStart').value) || 0;
        let end = parseInt(document.getElementById('previewEnd').value) || 0;
        if (start <= 0) {
            alert('已到达原文开头，无法继续向左扩展');
            return;
        }
        start -= 1;
        const newText = state.rawText.slice(start, end);
        renderEvidencePreview(start, end, newText);
    }

    /**
     * 向右扩展 1 字：end += 1（不超过 rawText.length），text 同步更新。
     */
    function expandEvidenceRight() {
        let start = parseInt(document.getElementById('previewStart').value) || 0;
        let end = parseInt(document.getElementById('previewEnd').value) || 0;
        if (end >= state.rawText.length) {
            alert('已到达原文末尾，无法继续向右扩展');
            return;
        }
        end += 1;
        const newText = state.rawText.slice(start, end);
        renderEvidencePreview(start, end, newText);
    }

    /**
     * 重新选择：关闭预览弹窗，等待用户在原文中重新选中文本后再次点击「添加证据」。
     * 不保存当前预览内容。
     */
    function reselectEvidence() {
        closeEvidencePreview();
        // 提示用户重新选择
        const infoEl = document.getElementById('selectionInfo');
        if (infoEl) {
            infoEl.textContent = '请在左侧原文中重新选中文本，再点击「添加证据」';
        }
        // 清除当前选区
        const sel = window.getSelection();
        if (sel) sel.removeAllRanges();
    }

    function closeEvidencePreview() {
        document.getElementById('evidencePreviewModal').classList.add('hidden');
        // P0-2 修复：不重置 currentFieldIndex / currentValueIndex，
        // 否则后续 renderFields() 会跳回第一个字段。
        // 只清理编辑证据的临时索引。
        state.editingEvidenceIndex = -1;
    }

    /**
     * 确认保存证据（来自预览弹窗）。
     * 保存前再次校验 slice，不通过禁止保存。
     * 保存后非阻塞提示用户证据已添加（不使用 alert，避免打断流程）。
     */
    function confirmSaveEvidence() {
        if (state.currentFieldIndex < 0 || state.currentValueIndex < 0) {
            alert('内部错误：未记录目标字段/值索引');
            return;
        }

        const start = parseInt(document.getElementById('previewStart').value) || 0;
        const end = parseInt(document.getElementById('previewEnd').value) || 0;
        const text = document.getElementById('previewText').value;
        const role = document.getElementById('previewRole').value;

        // 强制 slice 验证
        const verify = Schema.verifyEvidenceSpan(state.rawText, start, end, text);
        if (!verify.valid) {
            alert('slice 验证失败，无法保存。必须满足 rawText.slice(start, end) === 选中文字。\n\n实际文本：' + (verify.actualText || '').slice(0, 50) + '...');
            return;
        }

        const field = state.annotation.fields[state.currentFieldIndex];
        const value = field.values[state.currentValueIndex];

        const newEvidence = {
            role: role,
            start: start,
            end: end,
            text: text
        };

        // 新增 vs 编辑
        if (state.editingEvidenceIndex >= 0) {
            value.acceptable_evidence_spans[state.editingEvidenceIndex] = newEvidence;
        } else {
            value.acceptable_evidence_spans.push(newEvidence);
        }

        closeEvidencePreview();
        renderFields();
        renderText(); // 更新高亮
        scheduleAutoSave();
    }

    function removeEvidence(fieldIndex, valueIndex, evIndex) {
        const field = state.annotation.fields[fieldIndex];
        const value = field.values[valueIndex];
        value.acceptable_evidence_spans.splice(evIndex, 1);
        renderFields();
        renderText(); // 更新高亮
        scheduleAutoSave();
    }

    /**
     * 点击已保存证据：滚动到对应原文位置并临时高亮。
     * 如果 slice 不一致（原文已变化），显示"证据已失效"，不展示错误高亮。
     */
    function focusEvidenceInText(evIndex, fieldIndex, valueIndex) {
        const field = state.annotation.fields[fieldIndex];
        const value = field.values[valueIndex];
        const evidence = value.acceptable_evidence_spans[evIndex];
        if (!evidence) return;

        // 哈希/slice 失效检测
        const verify = Schema.verifyEvidenceSpan(state.rawText, evidence.start, evidence.end, evidence.text);
        const itemEl = document.querySelector('.evidence-item[data-ev-index="' + evIndex + '"][data-field="' + fieldIndex + '"][data-value="' + valueIndex + '"]');

        if (!verify.valid) {
            // 证据已失效：标记但不展示错误高亮
            if (itemEl) {
                itemEl.classList.add('evidence-invalid');
                // 移除旧的失效标签
                const oldTag = itemEl.querySelector('.evidence-invalid-tag');
                if (!oldTag) {
                    const tag = document.createElement('span');
                    tag.className = 'evidence-invalid-tag';
                    tag.textContent = '证据已失效';
                    itemEl.appendChild(tag);
                }
            }
            alert('证据已失效：rawText.slice(start, end) !== evidence.text\n\n可能原因：原文已被重新导入或修改。请删除该证据后重新选择。\n\n实际文本：' + (verify.actualText || '').slice(0, 50) + '...');
            return;
        }

        // 移除失效标记（如已恢复）
        if (itemEl) {
            itemEl.classList.remove('evidence-invalid');
            const tag = itemEl.querySelector('.evidence-invalid-tag');
            if (tag) tag.remove();
        }

        // 滚动到对应原文位置并临时高亮
        const textEl = document.getElementById('rawText');
        const highlights = textEl.querySelectorAll('.evidence-highlight');
        let targetSpan = null;
        // 通过 textContent 匹配（避免高亮 span 被拆分后偏移变化）
        for (const hl of highlights) {
            if (hl.textContent === evidence.text) {
                targetSpan = hl;
                break;
            }
        }
        if (targetSpan) {
            targetSpan.scrollIntoView({ behavior: 'smooth', block: 'center' });
            targetSpan.classList.add('evidence-flash');
            setTimeout(() => targetSpan.classList.remove('evidence-flash'), 1200);
        } else {
            // 高亮 span 未找到（可能因为重叠渲染被合并），回退到容器滚动
            textEl.scrollTop = Math.max(0, (evidence.start / state.rawText.length) * textEl.scrollHeight - textEl.clientHeight / 2);
        }
    }

    // ========== 证据编辑弹窗 ==========

    function openEvidenceModal(fieldIndex, valueIndex, evIndex) {
        state.currentFieldIndex = fieldIndex;
        state.currentValueIndex = valueIndex;
        state.editingEvidenceIndex = evIndex;

        const field = state.annotation.fields[fieldIndex];
        const value = field.values[valueIndex];
        const evidence = value.acceptable_evidence_spans[evIndex];

        document.getElementById('evidenceRole').value = evidence.role;
        document.getElementById('evidenceStart').value = evidence.start;
        document.getElementById('evidenceEnd').value = evidence.end;
        document.getElementById('evidenceText').value = evidence.text;

        updateEvidenceVerification();
        document.getElementById('evidenceModal').classList.remove('hidden');
    }

    function closeEvidenceModal() {
        document.getElementById('evidenceModal').classList.add('hidden');
        // P0-2 修复：不重置 currentFieldIndex / currentValueIndex，
        // 否则后续 renderFields() 会跳回第一个字段。
        state.editingEvidenceIndex = -1;
    }

    /**
     * 更新证据偏移量验证状态。
     * XSS 修复：使用 createElement + textContent，不拼接 innerHTML。
     */
    function updateEvidenceVerification() {
        const start = parseInt(document.getElementById('evidenceStart').value) || 0;
        const end = parseInt(document.getElementById('evidenceEnd').value) || 0;
        const text = document.getElementById('evidenceText').value;

        const verify = Schema.verifyEvidenceSpan(state.rawText, start, end, text);
        const statusEl = document.getElementById('evidenceVerify');

        // 清空旧内容
        while (statusEl.firstChild) {
            statusEl.removeChild(statusEl.firstChild);
        }

        const icon = document.createElement('span');
        icon.className = 'verify-icon';

        const msg = document.createElement('span');

        if (verify.valid) {
            statusEl.className = 'verify-status';
            icon.textContent = '✓';
            msg.textContent = '偏移量验证通过：text.slice(start, end) === evidence_text';
        } else {
            statusEl.className = 'verify-status error';
            icon.textContent = '✗';
            // 用 textContent 防止 XSS，实际文本可能包含恶意内容
            const actualPreview = (verify.actualText || '').slice(0, 50);
            msg.textContent = '验证失败：实际文本为 "' + actualPreview + '..."';
        }

        statusEl.appendChild(icon);
        statusEl.appendChild(msg);
    }

    function saveEvidence() {
        if (state.editingEvidenceIndex < 0) return;

        const start = parseInt(document.getElementById('evidenceStart').value) || 0;
        const end = parseInt(document.getElementById('evidenceEnd').value) || 0;
        const text = document.getElementById('evidenceText').value;

        // 强制验证，不通过禁止保存
        const verify = Schema.verifyEvidenceSpan(state.rawText, start, end, text);
        if (!verify.valid) {
            // 用 textContent 拼接 alert 消息（alert 不解析 HTML，天然安全）
            const actualPreview = (verify.actualText || '').slice(0, 50);
            alert('偏移量验证失败，无法保存。必须满足 text.slice(start, end) === evidence_text。\n\n实际文本：' + actualPreview + '...');
            return;
        }

        const field = state.annotation.fields[state.currentFieldIndex];
        const value = field.values[state.currentValueIndex];
        const evidence = value.acceptable_evidence_spans[state.editingEvidenceIndex];

        evidence.role = document.getElementById('evidenceRole').value;
        evidence.start = start;
        evidence.end = end;
        evidence.text = text;

        closeEvidenceModal();
        renderFields();
        renderText(); // 更新高亮
        scheduleAutoSave();
    }

    // ========== 导入导出 ==========

    /**
     * 导入 TXT 原文：创建一个新的标注文档，而非只替换 rawText。
     *
     * 关键行为（P0 修复：避免跨公告数据污染）：
     * 1. 文件大小校验、空文本校验（不覆盖当前文档）
     * 2. 读取并规范化换行符为 LF
     * 3. 计算 SHA-256，生成稳定 document_id（文件名 + hash 前 12 位）
     *    - 相同文件再次导入生成相同 document_id，识别为同一文档
     * 4. 若当前文档有未保存修改（含值/证据/备注），弹窗询问是否保存后再导入
     * 5. 若新 document_id 已有草稿，询问是否恢复草稿；否则创建空白标注
     * 6. 完整重置 state：annotation / rawText / docMeta / currentFieldIndex / 校验错误 / 高亮
     * 7. localStorage 按新 document_id 隔离
     * 8. 六字段初始化为 absent 空状态（createEmptyAnnotationDocument），不保留旧公告任何数据
     * 9. 重新推断 noticeType
     * 10. 更新页面文档状态显示（文件名 / document_id / 内容哈希 / 是否恢复草稿）
     */
    function importTextFile(file) {
        // 1. 文件大小限制
        if (file.size > MAX_IMPORT_SIZE) {
            alert('文件过大 (' + (file.size / 1024 / 1024).toFixed(2) + ' MB)，最大允许 ' + (MAX_IMPORT_SIZE / 1024 / 1024) + ' MB\n\n当前文档未被修改。');
            return;
        }

        // 2. 若当前文档有未保存修改，提示用户（不得静默丢弃）
        //    判断依据：state.annotation 存在非空值/证据/备注，且自上次保存后有改动
        //    这里采用简化判断：只要有非空标注数据就提示（保守策略，避免误丢）
        const oldDocId = state.annotation ? state.annotation.document_id : '';
        const hasData = hasNonEmptyAnnotation(state.annotation);
        if (hasData) {
            const saveFirst = confirm(
                '检测到当前文档 "' + oldDocId + '" 已有标注数据。\n\n' +
                '是否保存当前草稿后再导入新公告？\n\n' +
                '• 点击"确定"：保存当前草稿后继续导入\n' +
                '• 点击"取消"：放弃导入，保留当前文档'
            );
            if (!saveFirst) {
                return; // 用户取消，不导入，不修改当前状态
            }
            // 保存当前草稿（按旧 document_id 隔离）
            saveToStorage();
        }

        const reader = new FileReader();
        reader.onload = async function(e) {
            const text = normalizeNewlines(e.target.result);

            // 3. 空文本校验（不覆盖当前文档）
            if (!text || !text.trim()) {
                alert('导入失败：文件内容为空。\n\n当前文档未被修改。');
                return;
            }

            // 4. 计算 SHA-256，生成稳定 document_id
            const contentHash = await computeSha256(text);
            const newDocId = generateDocumentId(file.name, contentHash);

            // 5. 检查该 document_id 是否已有草稿
            const existingDraft = loadFromStorage(newDocId);
            const annotatorId = (state.annotation && state.annotation.annotator_id) || 'A';
            let restoredFromDraft = false;

            if (existingDraft && existingDraft.annotation) {
                // 已有草稿，询问是否恢复
                const restore = confirm(
                    '检测到该文档已存在草稿：\n' +
                    '  document_id: ' + newDocId + '\n' +
                    '  文件名: ' + file.name + '\n\n' +
                    '是否恢复已有草稿？\n\n' +
                    '• 点击"确定"：恢复草稿（包含原标注、证据、状态）\n' +
                    '• 点击"取消"：创建空白标注（草稿将被覆盖）'
                );
                if (restore) {
                    state.annotation = existingDraft.annotation;
                    state.rawText = existingDraft.rawText || text;
                    const existingMeta = loadDocMeta(newDocId);
                    state.docMeta = existingMeta || { noticeType: inferNoticeType(state.rawText), annotationStatus: 'pending' };
                    restoredFromDraft = true;
                } else {
                    // 创建空白标注，覆盖草稿
                    state.annotation = createBlankAnnotationForImport(newDocId, annotatorId);
                    state.rawText = text;
                    state.docMeta = { noticeType: inferNoticeType(text), annotationStatus: 'pending' };
                }
            } else {
                // 新文档，创建空白标注（gold_status='' 表示"待判断"，进度 0/6）
                state.annotation = createBlankAnnotationForImport(newDocId, annotatorId);
                state.rawText = text;
                state.docMeta = { noticeType: inferNoticeType(text), annotationStatus: 'pending' };
            }

            // 6. 完整重置编辑状态
            state.currentFieldIndex = 0;
            state.currentValueIndex = -1;
            state.editingEvidenceIndex = -1;
            // P0-2: 重置折叠状态和待聚焦 ui_id
            state.valueCollapsed = {};
            state.pendingFocusUiId = null;
            // P0-2: 为所有 value 分配 ui_id
            ensureAllValuesHaveUiId(state.annotation);

            // 7. 清除重叠提示
            const overlapWarning = document.getElementById('overlapWarning');
            if (overlapWarning) overlapWarning.style.display = 'none';

            // 8. 同步表单 + 渲染
            syncDocInfoFromState();
            renderText();
            renderFields();
            updateProgress();

            // 9. 保存新文档草稿（按新 document_id 隔离）+ 记住最后活动文档
            saveToStorage();
            setLastActiveDoc(newDocId);

            // 10. 更新文档状态显示
            updateDocStatusDisplay(file.name, newDocId, contentHash, restoredFromDraft);

            const msg = restoredFromDraft
                ? '文本导入成功（已恢复草稿）\n\ndocument_id: ' + newDocId + '\n内容哈希: ' + contentHash.slice(0, 12) + '...'
                : '文本导入成功（已创建新文档，字段已重置为空状态）\n\ndocument_id: ' + newDocId + '\n内容哈希: ' + contentHash.slice(0, 12) + '...';
            alert(msg);
        };
        reader.onerror = function() {
            alert('文件读取失败。当前文档未被修改。');
        };
        reader.readAsText(file, 'UTF-8');
    }

    /**
     * 更新页面文档状态显示区：文件名 / document_id / 内容哈希 / 是否恢复草稿。
     */
    function updateDocStatusDisplay(fileName, docId, contentHash, restoredFromDraft) {
        const el = document.getElementById('docStatusInfo');
        if (!el) return;
        const nameSpan = el.querySelector('.doc-status-name');
        const idSpan = el.querySelector('.doc-status-id');
        const hashSpan = el.querySelector('.doc-status-hash');
        const restoredSpan = el.querySelector('.doc-status-restored');
        if (nameSpan) nameSpan.textContent = fileName || '(未导入文件)';
        if (idSpan) idSpan.textContent = docId || state.annotation.document_id || '(无)';
        if (hashSpan) hashSpan.textContent = contentHash ? contentHash.slice(0, 12) + '...' : '(未计算)';
        if (restoredSpan) {
            restoredSpan.textContent = restoredFromDraft ? '是' : '否';
            restoredSpan.className = 'doc-status-restored ' + (restoredFromDraft ? 'restored-yes' : 'restored-no');
        }
    }

    /**
     * 导入 JSON 标注文件。
     * 校验失败时不覆盖当前草稿。
     */
    function importJsonFile(file) {
        // 文件大小限制
        if (file.size > MAX_IMPORT_SIZE) {
            alert('文件过大 (' + (file.size / 1024 / 1024).toFixed(2) + ' MB)，最大允许 ' + (MAX_IMPORT_SIZE / 1024 / 1024) + ' MB');
            return;
        }

        const reader = new FileReader();
        reader.onload = function(e) {
            let data;
            try {
                data = JSON.parse(e.target.result);
            } catch (err) {
                // 损坏 JSON 明确报错，不覆盖当前草稿
                alert('JSON 解析失败：' + err.message + '\n\n文件可能已损坏。当前草稿未被修改。');
                return;
            }

            // 完整校验（校验失败不覆盖当前草稿）
            const result = validateAnnotation(data, state.rawText);
            if (!result.valid) {
                const errorMessages = result.errors.map(err => '[' + err.field + '] ' + err.message);
                alert('JSON 校验失败，已拒绝导入：\n\n' + errorMessages.join('\n') + '\n\n当前草稿未被修改。');
                return;
            }

            // annotation_version 兼容性提示
            if (data.annotation_version && data.annotation_version !== Schema.ANNOTATION_VERSION) {
                const proceed = confirm(
                    '标注版本不兼容：\n' +
                    '  文件版本: ' + data.annotation_version + '\n' +
                    '  当前支持版本: ' + Schema.ANNOTATION_VERSION + '\n\n' +
                    '是否仍要导入？'
                );
                if (!proceed) return;
            }

            // 校验通过，更新 state 和 localStorage
            state.annotation = data;
            state.currentFieldIndex = 0;
            state.currentValueIndex = -1;
            state.editingEvidenceIndex = -1;
            // P0-2: 重置折叠状态，为导入的 value 分配 ui_id
            state.valueCollapsed = {};
            state.pendingFocusUiId = null;
            ensureAllValuesHaveUiId(state.annotation);
            syncDocInfoFromState();
            renderFields();
            renderText(); // 更新高亮
            updateProgress();
            scheduleAutoSave();
            alert('JSON 导入成功');
        };
        reader.onerror = function() {
            alert('文件读取失败。当前草稿未被修改。');
        };
        reader.readAsText(file, 'UTF-8');
    }

    /**
     * 导出 JSON 标注文件。
     * 导出前执行完整校验，失败时禁止导出并显示具体错误。
     * 只导出 AnnotationDocument Schema 允许的字段（extra="forbid"），不混入 noticeType/annotationStatus。
     */
    function exportJson() {
        // 从表单同步最新值
        state.annotation.annotation_time = new Date().toISOString();
        state.annotation.document_id = document.getElementById('documentId').value;
        state.annotation.annotator_id = document.getElementById('annotatorId').value;

        // 导出前最终校验
        const result = validateAnnotation(state.annotation, state.rawText);
        if (!result.valid) {
            const errorMessages = result.errors.map(err => '[' + err.field + '] ' + err.message);
            alert('导出校验失败，请修正以下错误：\n\n' + errorMessages.join('\n'));

            // 自动切换到首个错误字段
            if (result.firstErrorFieldIndex >= 0) {
                flashFieldError(result.firstErrorFieldIndex);
            }
            return;
        }

        // 只导出 AnnotationDocument Schema 允许的字段（extra="forbid"）
        // noticeType / annotationStatus 不混入导出 JSON
        // P0-2: 剥离 ui_id 等 UI 元数据，避免破坏 Schema 校验
        const exportData = {
            document_id: state.annotation.document_id,
            annotator_id: state.annotation.annotator_id,
            annotation_version: state.annotation.annotation_version,
            annotation_time: state.annotation.annotation_time,
            fields: stripUiMetadataForExport(state.annotation.fields)
        };

        const jsonStr = JSON.stringify(exportData, null, 2);
        const blob = new Blob([jsonStr], { type: 'application/json' });
        const url = URL.createObjectURL(blob);

        const a = document.createElement('a');
        a.href = url;
        a.download = 'annotation_' + (state.annotation.document_id || 'export') + '_' + Date.now() + '.json';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    /**
     * 重置标注：只清除当前文档的草稿，不影响其他文档。
     */
    function resetAnnotation() {
        const docId = state.annotation.document_id || 'default';
        if (!confirm('确定要重置文档 "' + docId + '" 的所有标注吗？\n\n此操作只清除当前文档的草稿，不影响其他文档。操作不可撤销。')) {
            return;
        }
        const annotatorId = state.annotation.annotator_id || 'A';
        state.annotation = Schema.createEmptyAnnotationDocument(docId, annotatorId);
        state.rawText = '';
        state.docMeta = { noticeType: 'tender', annotationStatus: 'pending' };
        state.currentFieldIndex = 0;
        state.currentValueIndex = -1;
        state.editingEvidenceIndex = -1;
        // P0-2: 重置折叠状态和待聚焦 ui_id
        state.valueCollapsed = {};
        state.pendingFocusUiId = null;
        ensureAllValuesHaveUiId(state.annotation);

        // 清除重叠提示
        const overlapWarning = document.getElementById('overlapWarning');
        if (overlapWarning) overlapWarning.style.display = 'none';

        syncDocInfoFromState();
        renderText();
        renderFields();
        updateProgress();
        clearStorage(docId);
        updateDocStatusDisplay('', docId, '', false);
        setLastActiveDoc(docId);
    }

    /**
     * 切换当前编辑字段（点击导航项或校验失败时调用）。
     * 切换后只显示目标字段的编辑器。
     */
    function switchToField(fieldIndex) {
        if (fieldIndex < 0 || fieldIndex >= state.annotation.fields.length) return;
        if (fieldIndex === state.currentFieldIndex) return;

        state.currentFieldIndex = fieldIndex;
        renderFieldsNav();
        const container = document.getElementById('fieldsContainer');
        while (container.firstChild) {
            container.removeChild(container.firstChild);
        }
        const card = createFieldCard(state.annotation.fields[fieldIndex], fieldIndex);
        container.appendChild(card);
    }

    /**
     * 校验失败时高亮目标字段卡片（不切换，仅闪烁）。
     */
    function flashFieldError(fieldIndex) {
        if (fieldIndex < 0) return;
        // 切换到错误字段
        switchToField(fieldIndex);
        const card = document.querySelector('.field-card');
        if (card) {
            card.classList.add('field-card-error');
            setTimeout(() => card.classList.remove('field-card-error'), 3000);
        }
    }

    // ========== 自动保存（按文档隔离） ==========

    function scheduleAutoSave() {
        if (state.saveTimeout) {
            clearTimeout(state.saveTimeout);
        }
        state.saveTimeout = setTimeout(() => {
            saveToStorage();
            flashSaveIndicator();
        }, 500);
    }

    /**
     * 保存当前文档草稿到 localStorage（按 document_id 隔离）。
     * 同时保存文档元数据（noticeType / annotationStatus）和更新文档索引。
     */
    function saveToStorage() {
        const docId = state.annotation.document_id || 'default';
        const data = {
            rawText: state.rawText,
            annotation: state.annotation,
            savedAt: new Date().toISOString()
        };
        try {
            localStorage.setItem(getDraftKey(docId), JSON.stringify(data));

            // 保存文档元数据（noticeType / annotationStatus）
            saveDocMeta(docId, state.docMeta);

            // 更新文档索引
            const title = state.rawText ? state.rawText.slice(0, 50) : '(无原文)';
            updateDocIndex(docId, title, state.docMeta.annotationStatus);
        } catch (e) {
            console.warn('自动保存失败', e);
        }
    }

    function loadFromStorage(docId) {
        try {
            const raw = localStorage.getItem(getDraftKey(docId));
            if (!raw) return null;
            return JSON.parse(raw);
        } catch (e) {
            return null;
        }
    }

    /**
     * 只清除当前文档的草稿、元数据和索引项（不影响其他文档）。
     */
    function clearStorage(docId) {
        localStorage.removeItem(getDraftKey(docId));
        localStorage.removeItem(getMetaKey(docId));
        removeFromDocIndex(docId);
    }

    function flashSaveIndicator() {
        const indicator = document.getElementById('autoSaveIndicator');
        indicator.textContent = '✓ 已保存';
        setTimeout(() => {
            indicator.textContent = '● 自动保存已启用（仅保存在当前浏览器本机）';
        }, 1000);
    }

    // ========== 进度统计 ==========

    function updateProgress() {
        let completed = 0;
        state.annotation.fields.forEach(field => {
            if (field.gold_status === Schema.GOLD_STATUS.PRESENT && field.values.length > 0) {
                // present 且有值，且至少有一个 primary 证据
                const hasPrimary = field.values.some(v =>
                    v.acceptable_evidence_spans.some(e => e.role === Schema.EVIDENCE_ROLES.PRIMARY)
                );
                if (hasPrimary) completed++;
            } else if (field.gold_status !== Schema.GOLD_STATUS.PRESENT && field.gold_status !== '') {
                // 非 present 状态也算标记完成
                completed++;
            }
        });

        document.getElementById('progressText').textContent =
            completed + ' / ' + state.annotation.fields.length + ' 已完成';
    }

    // ========== 事件绑定 ==========

    function bindEvents() {
        // 文本选择
        document.getElementById('rawText').addEventListener('mouseup', updateSelectionInfo);
        document.getElementById('rawText').addEventListener('keyup', updateSelectionInfo);

        // 文档 ID 变化：触发文档切换（保存当前草稿，加载目标文档）
        document.getElementById('documentId').addEventListener('change', (e) => {
            const newDocId = e.target.value.trim();
            if (!newDocId) {
                alert('文档 ID 不能为空');
                e.target.value = state.annotation.document_id;
                return;
            }
            if (newDocId === state.annotation.document_id) return;

            const proceed = confirm('切换文档将保存当前草稿并加载目标文档。确定要切换到 "' + newDocId + '" 吗？');
            if (!proceed) {
                e.target.value = state.annotation.document_id;
                return;
            }
            switchDocument(newDocId);
        });

        // 标注员 ID 变化（同文档内修改）
        document.getElementById('annotatorId').addEventListener('input', () => {
            state.annotation.annotator_id = document.getElementById('annotatorId').value;
            scheduleAutoSave();
        });

        // 公告类型（保存到本地元数据，不混入导出 JSON）
        document.getElementById('noticeType').addEventListener('change', (e) => {
            state.docMeta.noticeType = e.target.value;
            scheduleAutoSave();
        });

        // 标注状态（保存到本地元数据，不混入导出 JSON）
        document.getElementById('annotationStatus').addEventListener('change', (e) => {
            state.docMeta.annotationStatus = e.target.value;
            scheduleAutoSave();
        });

        // 工具栏按钮
        document.getElementById('btnImportText').addEventListener('click', () => {
            const input = document.createElement('input');
            input.type = 'file';
            input.accept = '.txt';
            input.className = 'hidden-file-input';
            input.onchange = (e) => {
                if (e.target.files[0]) importTextFile(e.target.files[0]);
            };
            input.click();
        });

        document.getElementById('btnImportJson').addEventListener('click', () => {
            const input = document.createElement('input');
            input.type = 'file';
            input.accept = '.json';
            input.className = 'hidden-file-input';
            input.onchange = (e) => {
                if (e.target.files[0]) importJsonFile(e.target.files[0]);
            };
            input.click();
        });

        document.getElementById('btnExportJson').addEventListener('click', exportJson);
        document.getElementById('btnReset').addEventListener('click', resetAnnotation);

        // 证据弹窗
        document.getElementById('evidenceStart').addEventListener('input', updateEvidenceVerification);
        document.getElementById('evidenceEnd').addEventListener('input', updateEvidenceVerification);
        document.getElementById('evidenceText').addEventListener('input', updateEvidenceVerification);

        // 点击弹窗外部关闭
        document.getElementById('evidenceModal').addEventListener('click', (e) => {
            if (e.target.id === 'evidenceModal') {
                closeEvidenceModal();
            }
        });

        // ESC 关闭弹窗
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                closeEvidenceModal();
            }
        });
    }

    // ========== 暴露到全局（供 HTML 调用和测试访问） ==========
    window.closeEvidenceModal = closeEvidenceModal;
    window.saveEvidence = saveEvidence;
    // 证据预览弹窗（HTML onclick 调用）
    window.expandEvidenceLeft = expandEvidenceLeft;
    window.expandEvidenceRight = expandEvidenceRight;
    window.reselectEvidence = reselectEvidence;
    window.closeEvidencePreview = closeEvidencePreview;
    window.confirmSaveEvidence = confirmSaveEvidence;
    // 暴露 App 对象（state 供测试读取；函数供测试触发）
    window.App = {
        state: state,
        validateAnnotation: validateAnnotation,
        saveToStorage: saveToStorage,
        loadFromStorage: loadFromStorage,
        clearStorage: clearStorage,
        switchDocument: switchDocument,
        renderText: renderText,
        renderFields: renderFields,
        getSelectedTextInfo: getSelectedTextInfo,
        getAbsoluteOffsetFromRange: getAbsoluteOffsetFromRange,
        importTextFile: importTextFile,
        importJsonFile: importJsonFile,
        exportJson: exportJson,
        resetAnnotation: resetAnnotation,
        inferNoticeType: inferNoticeType,
        sanitizeFileName: sanitizeFileName,
        generateDocumentId: generateDocumentId,
        hasNonEmptyAnnotation: hasNonEmptyAnnotation,
        createBlankAnnotationForImport: createBlankAnnotationForImport,
        updateDocStatusDisplay: updateDocStatusDisplay,
        computeSha256: computeSha256,
        setLastActiveDoc: setLastActiveDoc,
        getLastActiveDoc: getLastActiveDoc,
        switchToField: switchToField,
        // 证据质量控制（P0 增量）
        addEvidenceFromSelection: addEvidenceFromSelection,
        renderEvidencePreview: renderEvidencePreview,
        expandEvidenceLeft: expandEvidenceLeft,
        expandEvidenceRight: expandEvidenceRight,
        reselectEvidence: reselectEvidence,
        closeEvidencePreview: closeEvidencePreview,
        confirmSaveEvidence: confirmSaveEvidence,
        focusEvidenceInText: focusEvidenceInText,
        checkEvidenceQuality: checkEvidenceQuality,
        inferEvidenceCategory: inferEvidenceCategory,
        CONTEXT_RADIUS: CONTEXT_RADIUS,
        MAX_IMPORT_SIZE: MAX_IMPORT_SIZE,
        // P0-1/P0-2 状态保持
        addFieldValue: addFieldValue,
        removeFieldValue: removeFieldValue,
        removeEvidence: removeEvidence,
        ensureValueUiId: ensureValueUiId,
        ensureAllValuesHaveUiId: ensureAllValuesHaveUiId,
        stripUiMetadataForExport: stripUiMetadataForExport,
        generateUiId: generateUiId
    };

    // ========== 启动 ==========
    document.addEventListener('DOMContentLoaded', init);

})();
