/**
 * BidAgent 金标标注工具 - 核心逻辑
 * 
 * 功能：
 * - 左侧展示 clean_raw_text，支持鼠标选中文本
 * - 右侧六类核心字段标注，支持六种状态
 * - 多值字段、多段证据、证据角色
 * - 金额类型、lot_id、备注
 * - JSON 导入导出、localStorage 自动保存
 * 
 * 严格对接 backend/schemas.py 中的 AnnotationDocument Schema
 */

(function() {
    'use strict';

    const Schema = window.AnnotationSchema;
    const SampleData = window.SampleData;

    // ========== 全局状态 ==========
    const state = {
        rawText: '',
        annotation: null,
        currentFieldIndex: -1,
        currentValueIndex: -1,
        editingEvidenceIndex: -1,
        autoSaveKey: 'bidagent_annotation_draft',
        saveTimeout: null
    };

    // ========== 初始化 ==========

    function init() {
        // 尝试从 localStorage 恢复
        const saved = loadFromStorage();
        if (saved && saved.annotation) {
            state.annotation = saved.annotation;
            state.rawText = saved.rawText || '';
        } else {
            // 使用示例数据初始化
            state.annotation = JSON.parse(JSON.stringify(SampleData.SAMPLE_ANNOTATION));
            state.rawText = SampleData.SAMPLE_RAW_TEXT;
        }

        // 同步表单值
        syncDocInfoFromState();

        // 渲染
        renderText();
        renderFields();
        updateProgress();

        // 绑定事件
        bindEvents();
    }

    function syncDocInfoFromState() {
        document.getElementById('documentId').value = state.annotation.document_id || '';
        document.getElementById('annotatorId').value = state.annotation.annotator_id || '';
        document.getElementById('annotationVersion').value = state.annotation.annotation_version || '1.0';
    }

    // ========== 文本渲染和选择 ==========

    function renderText() {
        const textEl = document.getElementById('rawText');
        textEl.textContent = state.rawText;
        document.getElementById('charCount').textContent = `字符数：${state.rawText.length}`;
    }

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

        // 方法一：pre 元素只有一个文本子节点时，直接用偏移量
        const textNode = textEl.firstChild;
        if (textNode && textNode.nodeType === Node.TEXT_NODE && 
            range.startContainer === textNode && range.endContainer === textNode) {
            const start = range.startOffset;
            const end = range.endOffset;
            // 强制验证
            const verify = Schema.verifyEvidenceSpan(state.rawText, start, end, selectedText);
            if (verify.valid) {
                return { start, end, text: selectedText };
            }
            // 验证不通过，降级到方法二
        }

        // 方法二：字符串精确匹配（兜底方案，确保 100% 正确）
        // 从选区起点位置向前搜索最近的匹配
        const approxStart = range.startOffset;
        let start = -1;
        
        // 先尝试从近似位置向前找
        let searchPos = Math.max(0, approxStart - 100);
        while (searchPos <= approxStart) {
            const idx = state.rawText.indexOf(selectedText, searchPos);
            if (idx === -1 || idx > approxStart) break;
            start = idx;
            searchPos = idx + 1;
        }
        
        // 如果向前没找到，向后找第一个
        if (start === -1) {
            start = state.rawText.indexOf(selectedText);
        }

        if (start === -1) {
            console.error('选中文本在原文中找不到匹配', { selectedText });
            return null;
        }

        const end = start + selectedText.length;

        // 最终强制验证，不通过绝不返回
        const finalVerify = Schema.verifyEvidenceSpan(state.rawText, start, end, selectedText);
        if (!finalVerify.valid) {
            console.error('偏移量最终验证失败', { start, end, selectedText, actual: finalVerify.actualText });
            return null;
        }

        return { start, end, text: selectedText };
    }

    function updateSelectionInfo() {
        const info = getSelectedTextInfo();
        const infoEl = document.getElementById('selectionInfo');
        if (info) {
            infoEl.textContent = `已选中 [${info.start}, ${info.end}) 共 ${info.end - info.start} 字符`;
        } else {
            infoEl.textContent = '未选中文本';
        }
    }

    // ========== 字段渲染 ==========

    function renderFields() {
        const container = document.getElementById('fieldsContainer');
        container.innerHTML = '';

        state.annotation.fields.forEach((field, fieldIndex) => {
            const card = createFieldCard(field, fieldIndex);
            container.appendChild(card);
        });
    }

    function createFieldCard(field, fieldIndex) {
        const card = document.createElement('div');
        card.className = 'field-card';

        // 头部
        const header = document.createElement('div');
        header.className = 'field-card-header';

        const titleDiv = document.createElement('div');
        titleDiv.className = 'field-title';

        const nameSpan = document.createElement('span');
        nameSpan.className = 'field-name';
        nameSpan.textContent = Schema.FIELD_DISPLAY_NAMES[field.field_name] || field.field_name;

        const statusBadge = document.createElement('span');
        statusBadge.className = `status-badge status-${field.gold_status}`;
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

        // 值头部
        const header = document.createElement('div');
        header.className = 'value-header';

        const idxSpan = document.createElement('span');
        idxSpan.className = 'value-index';
        idxSpan.textContent = `值 ${valueIndex + 1}`;

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
        evLabel.textContent = `合法证据片段（${value.acceptable_evidence_spans.length}）`;
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
        input.addEventListener('input', (e) => onChange(e.target.value));

        div.appendChild(lbl);
        div.appendChild(input);
        return div;
    }

    function createEvidenceItem(evidence, evIndex, fieldIndex, valueIndex) {
        const item = document.createElement('div');
        item.className = 'evidence-item';

        // 角色标签
        const roleTag = document.createElement('span');
        roleTag.className = `evidence-role-tag evidence-role-${evidence.role}`;
        roleTag.textContent = evidence.role;
        item.appendChild(roleTag);

        // 文本预览
        const textSpan = document.createElement('span');
        textSpan.className = 'evidence-text-preview';
        textSpan.textContent = evidence.text;
        textSpan.title = evidence.text;
        item.appendChild(textSpan);

        // 偏移量
        const offsetSpan = document.createElement('span');
        offsetSpan.className = 'evidence-offset';
        offsetSpan.textContent = `[${evidence.start}, ${evidence.end})`;
        item.appendChild(offsetSpan);

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
            field.values.push(Schema.createEmptyValue());
        }

        renderFields();
        updateProgress();
        scheduleAutoSave();
    }

    function addFieldValue(fieldIndex) {
        const field = state.annotation.fields[fieldIndex];
        field.values.push(Schema.createEmptyValue());
        renderFields();
        scheduleAutoSave();
    }

    function removeFieldValue(fieldIndex, valueIndex) {
        const field = state.annotation.fields[fieldIndex];
        if (field.values.length <= 1) {
            if (!confirm('至少需要保留一个值。确定要删除吗？这将把字段状态改为"不存在"。')) {
                return;
            }
            field.values = [];
            field.gold_status = Schema.GOLD_STATUS.ABSENT;
        } else {
            field.values.splice(valueIndex, 1);
        }
        renderFields();
        updateProgress();
        scheduleAutoSave();
    }

    // ========== 证据操作 ==========

    function addEvidenceFromSelection(fieldIndex, valueIndex) {
        const selection = getSelectedTextInfo();
        if (!selection) {
            alert('请先在左侧原文中选中一段文本');
            return;
        }

        // 验证
        const verify = Schema.verifyEvidenceSpan(state.rawText, selection.start, selection.end, selection.text);
        if (!verify.valid) {
            alert('偏移量验证失败，请重新选择文本');
            return;
        }

        const field = state.annotation.fields[fieldIndex];
        const value = field.values[valueIndex];

        const newEvidence = {
            role: Schema.EVIDENCE_ROLES.PRIMARY,
            start: selection.start,
            end: selection.end,
            text: selection.text
        };

        value.acceptable_evidence_spans.push(newEvidence);
        renderFields();
        scheduleAutoSave();
    }

    function removeEvidence(fieldIndex, valueIndex, evIndex) {
        const field = state.annotation.fields[fieldIndex];
        const value = field.values[valueIndex];
        value.acceptable_evidence_spans.splice(evIndex, 1);
        renderFields();
        scheduleAutoSave();
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
        state.currentFieldIndex = -1;
        state.currentValueIndex = -1;
        state.editingEvidenceIndex = -1;
    }

    function updateEvidenceVerification() {
        const start = parseInt(document.getElementById('evidenceStart').value) || 0;
        const end = parseInt(document.getElementById('evidenceEnd').value) || 0;
        const text = document.getElementById('evidenceText').value;

        const verify = Schema.verifyEvidenceSpan(state.rawText, start, end, text);
        const statusEl = document.getElementById('evidenceVerify');

        if (verify.valid) {
            statusEl.className = 'verify-status';
            statusEl.innerHTML = '<span class="verify-icon">✓</span><span>偏移量验证通过：text.slice(start, end) === evidence_text</span>';
        } else {
            statusEl.className = 'verify-status error';
            statusEl.innerHTML = `<span class="verify-icon">✗</span><span>验证失败：实际文本为 "${verify.actualText.slice(0, 50)}..."</span>`;
        }
    }

    function saveEvidence() {
        if (state.editingEvidenceIndex < 0) return;

        const start = parseInt(document.getElementById('evidenceStart').value) || 0;
        const end = parseInt(document.getElementById('evidenceEnd').value) || 0;
        const text = document.getElementById('evidenceText').value;

        // 强制验证，不通过禁止保存
        const verify = Schema.verifyEvidenceSpan(state.rawText, start, end, text);
        if (!verify.valid) {
            alert('偏移量验证失败，无法保存。必须满足 text.slice(start, end) === evidence_text。\n\n实际文本：' + verify.actualText.slice(0, 50) + '...');
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
        scheduleAutoSave();
    }

    // ========== 导入导出 ==========

    function importTextFile(file) {
        const reader = new FileReader();
        reader.onload = function(e) {
            state.rawText = e.target.result;
            renderText();
            scheduleAutoSave();
            alert('文本导入成功');
        };
        reader.readAsText(file, 'UTF-8');
    }

    function importJsonFile(file) {
        const reader = new FileReader();
        reader.onload = function(e) {
            try {
                const data = JSON.parse(e.target.result);
                // 验证基本结构
                if (!data.fields || !Array.isArray(data.fields)) {
                    throw new Error('缺少 fields 字段');
                }
                state.annotation = data;
                syncDocInfoFromState();
                renderFields();
                updateProgress();
                scheduleAutoSave();
                alert('JSON 导入成功');
            } catch (err) {
                alert('JSON 导入失败：' + err.message);
            }
        };
        reader.readAsText(file, 'UTF-8');
    }

    function exportJson() {
        // 更新 annotation_time
        state.annotation.annotation_time = new Date().toISOString();
        state.annotation.document_id = document.getElementById('documentId').value;
        state.annotation.annotator_id = document.getElementById('annotatorId').value;

        const jsonStr = JSON.stringify(state.annotation, null, 2);
        const blob = new Blob([jsonStr], { type: 'application/json' });
        const url = URL.createObjectURL(blob);

        const a = document.createElement('a');
        a.href = url;
        a.download = `annotation_${state.annotation.document_id || 'export'}_${Date.now()}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    function resetAnnotation() {
        if (!confirm('确定要重置所有标注吗？此操作不可撤销。')) {
            return;
        }
        state.annotation = Schema.createEmptyAnnotationDocument('new-doc', 'A');
        state.rawText = '';
        syncDocInfoFromState();
        renderText();
        renderFields();
        updateProgress();
        clearStorage();
    }

    // ========== 自动保存 ==========

    function scheduleAutoSave() {
        if (state.saveTimeout) {
            clearTimeout(state.saveTimeout);
        }
        state.saveTimeout = setTimeout(() => {
            saveToStorage();
            flashSaveIndicator();
        }, 500);
    }

    function saveToStorage() {
        const data = {
            rawText: state.rawText,
            annotation: state.annotation,
            savedAt: new Date().toISOString()
        };
        try {
            localStorage.setItem(state.autoSaveKey, JSON.stringify(data));
        } catch (e) {
            console.warn('自动保存失败', e);
        }
    }

    function loadFromStorage() {
        try {
            const raw = localStorage.getItem(state.autoSaveKey);
            if (!raw) return null;
            return JSON.parse(raw);
        } catch (e) {
            return null;
        }
    }

    function clearStorage() {
        localStorage.removeItem(state.autoSaveKey);
    }

    function flashSaveIndicator() {
        const indicator = document.getElementById('autoSaveIndicator');
        indicator.textContent = '✓ 已保存';
        setTimeout(() => {
            indicator.textContent = '● 自动保存已启用';
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
            `${completed} / ${state.annotation.fields.length} 已完成`;
    }

    // ========== 事件绑定 ==========

    function bindEvents() {
        // 文本选择
        document.getElementById('rawText').addEventListener('mouseup', updateSelectionInfo);
        document.getElementById('rawText').addEventListener('keyup', updateSelectionInfo);

        // 文档信息变化
        document.getElementById('documentId').addEventListener('input', () => {
            state.annotation.document_id = document.getElementById('documentId').value;
            scheduleAutoSave();
        });
        document.getElementById('annotatorId').addEventListener('input', () => {
            state.annotation.annotator_id = document.getElementById('annotatorId').value;
            scheduleAutoSave();
        });

        // 工具栏按钮
        document.getElementById('btnImportText').addEventListener('click', () => {
            const input = document.createElement('input');
            input.type = 'file';
            input.accept = '.txt,.json';
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

    // ========== 暴露到全局（供 HTML 调用） ==========
    window.closeEvidenceModal = closeEvidenceModal;
    window.saveEvidence = saveEvidence;

    // ========== 启动 ==========
    document.addEventListener('DOMContentLoaded', init);

})();
