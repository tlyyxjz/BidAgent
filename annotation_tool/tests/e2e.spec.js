/**
 * BidAgent W1-05 标注工具 - Playwright 端到端测试
 *
 * 覆盖 12 项真实 DOM 交互场景：
 *  1. present 无 value，禁止导出
 *  2. present value 无 primary，禁止导出
 *  3. 非 present 残留 values，禁止导出
 *  4. 合法数据可以导出
 *  5. 非法 JSON 导入不覆盖当前状态
 *  6. 不同 document_id 草稿隔离
 *  7. XSS 文本不执行
 *  8. 重复文本选择第二处时偏移正确
 *  9. 高亮后继续选择第二段证据
 * 10. noticeType 和 annotationStatus 保存恢复
 * 11. 导出后重新导入数据一致
 * 12. fixture 不被 generate.py 覆盖
 *
 * 沙箱注意：临时文件统一写入 os.tmpdir()，避免受限路径；
 *          测试 12 通过 BIDAGENT_OUTPUT_DIR 环境变量重定向 generate.py 的派生文件输出。
 */
const { test, expect } = require('@playwright/test');
const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const { execSync } = require('child_process');

const TOOL_DIR = path.resolve(__dirname, '..');
const FIXTURES_DIR = path.join(TOOL_DIR, 'fixtures');
const SAMPLE_TEXT_PATH = path.join(FIXTURES_DIR, 'sample-001.txt');

// 临时文件统一放到 os.tmpdir()，避免沙箱对工作目录写入的限制
const TMP_DIR = os.tmpdir();
const TMP_INVALID_JSON = path.join(TMP_DIR, 'bidagent_e2e_invalid.json');
const TMP_ROUNDTRIP_JSON = path.join(TMP_DIR, 'bidagent_e2e_roundtrip.json');
const TMP_META_JSON = path.join(TMP_DIR, 'bidagent_e2e_meta.json');
const TMP_GEN_OUTPUT_DIR = path.join(TMP_DIR, 'bidagent_e2e_genout');

// init() 默认加载 'sample-001' 的草稿，所以所有注入都用 'sample-001' 作为 storage key，
// 但 annotation.document_id 可以是任意值（影响导出文件名）
const STORAGE_DOC_ID = 'sample-001';

function sha256File(filePath) {
  const content = fs.readFileSync(filePath, 'utf-8').replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  return crypto.createHash('sha256').update(content, 'utf8').digest('hex');
}

// 在浏览器中通过 window.AnnotationSchema 构造合法标注
// 偏移量动态查找，确保与 SAMPLE_RAW_TEXT 完全匹配
async function buildValidAnnotation(page, docId) {
  return await page.evaluate((docId) => {
    const Schema = window.AnnotationSchema;
    const rawText = window.SampleData.SAMPLE_RAW_TEXT;
    const now = new Date().toISOString();

    // 动态查找证据偏移量（SAMPLE_RAW_TEXT 中每个证据文本唯一出现）
    function findEv(text) {
      const start = rawText.indexOf(text);
      if (start < 0) throw new Error('证据文本不在 SAMPLE_RAW_TEXT 中: ' + text);
      return { role: 'primary', start, end: start + text.length, text };
    }

    const mkField = (name, value, evidence) => ({
      field_name: name,
      gold_status: 'present',
      values: [{
        raw_value: value,
        normalized_value: value,
        amount_type: null,
        currency: null,
        original_unit: null,
        tax_status: null,
        lot_id: null,
        acceptable_evidence_spans: evidence
      }],
      note: ''
    });

    const amountStart = rawText.indexOf('1285.60万元');
    const qualifierStart = rawText.indexOf('中标（成交）金额');

    return {
      rawText,
      annotation: {
        document_id: docId,
        annotator_id: 'A',
        annotation_version: Schema.ANNOTATION_VERSION,
        annotation_time: now,
        fields: [
          mkField('project_identifier', 'ZFCG-2024-0315', [findEv('ZFCG-2024-0315')]),
          mkField('purchaser_name', '某市大数据管理局', [findEv('某市大数据管理局')]),
          mkField('winner_name', '上海智汇科技有限公司', [findEv('上海智汇科技有限公司')]),
          mkField('amount', '1285.60万元', [
            { role: 'primary', start: amountStart, end: amountStart + '1285.60万元'.length, text: '1285.60万元' },
            { role: 'qualifier', start: qualifierStart, end: qualifierStart + '中标（成交）金额'.length, text: '中标（成交）金额' }
          ]),
          mkField('publish_date', '2024年3月15日', [findEv('2024年3月15日')]),
          mkField('bid_deadline', '2024年3月10日', [findEv('2024年3月10日')])
        ]
      }
    };
  }, docId);
}

// 在浏览器中注入 state 并触发保存
// 注意：init() 固定加载 'sample-001' 草稿，所以注入时必须使用 'sample-001' 作为 storage key
// annotation.document_id 可以是任意值（影响导出文件名和导出数据）
async function injectState(page, annotation, rawText) {
  await page.evaluate(({ annotation, rawText }) => {
    const STORAGE_DOC_ID = 'sample-001';
    const draftKey = 'bidagent_annotation_draft_' + STORAGE_DOC_ID;
    const metaKey = 'bidagent_annotation_meta_' + STORAGE_DOC_ID;
    localStorage.setItem(draftKey, JSON.stringify({
      rawText, annotation, savedAt: new Date().toISOString()
    }));
    localStorage.setItem(metaKey, JSON.stringify({
      noticeType: 'tender',
      annotationStatus: 'pending',
      savedAt: new Date().toISOString()
    }));
  }, { annotation, rawText });
}

// 捕获下一个 dialog 消息（alert/confirm）
function nextDialogMessage(page, timeout = 3000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('dialog timeout')), timeout);
    const handler = d => {
      clearTimeout(timer);
      page.off('dialog', handler);
      const msg = d.message();
      d.accept();
      resolve(msg);
    };
    page.on('dialog', handler);
  });
}

// ============================================================
// 测试套件
// ============================================================
test.describe('BidAgent W1-05 标注工具端到端测试', () => {

  test.beforeEach(async ({ page }) => {
    await page.goto('about:blank');
    const context = page.context();
    await context.clearCookies();
    await page.goto('http://localhost:8765/index.html');
    await page.waitForFunction(() => window.App && window.App.state && window.App.state.annotation, { timeout: 5000 });
    await page.evaluate(() => localStorage.clear());
    await page.reload();
    await page.waitForFunction(() => window.App && window.App.state && window.App.state.annotation, { timeout: 5000 });
  });

  test('1. present 无 value 禁止导出', async ({ page }) => {
    const { rawText, annotation } = await buildValidAnnotation(page, 'e2e-test-001');
    annotation.fields[0].values = [];
    await injectState(page, annotation, rawText);
    await page.reload();
    await page.waitForSelector('#fieldsContainer .field-card');

    const dialogPromise = nextDialogMessage(page);
    await page.click('#btnExportJson');
    const msg = await dialogPromise;

    expect(msg).toContain('校验失败');
    expect(msg).toContain('project_identifier');
  });

  test('2. present value 无 primary 证据禁止导出', async ({ page }) => {
    const { rawText, annotation } = await buildValidAnnotation(page, 'e2e-test-002');
    const ev = annotation.fields[0].values[0].acceptable_evidence_spans[0];
    ev.role = 'context';
    await injectState(page, annotation, rawText);
    await page.reload();
    await page.waitForSelector('#fieldsContainer .field-card');

    const dialogPromise = nextDialogMessage(page);
    await page.click('#btnExportJson');
    const msg = await dialogPromise;

    expect(msg).toContain('校验失败');
    expect(msg).toMatch(/primary/);
  });

  test('3. 非 present 残留 values 禁止导出', async ({ page }) => {
    const { rawText, annotation } = await buildValidAnnotation(page, 'e2e-test-003');
    annotation.fields[1].gold_status = 'absent';
    await injectState(page, annotation, rawText);
    await page.reload();
    await page.waitForSelector('#fieldsContainer .field-card');

    const dialogPromise = nextDialogMessage(page);
    await page.click('#btnExportJson');
    const msg = await dialogPromise;

    expect(msg).toContain('校验失败');
    expect(msg).toContain('purchaser_name');
  });

  test('4. 合法数据可以导出', async ({ page }) => {
    const { rawText, annotation } = await buildValidAnnotation(page, 'e2e-test-004');
    await injectState(page, annotation, rawText);
    await page.reload();
    await page.waitForSelector('#fieldsContainer .field-card');

    const downloadPromise = page.waitForEvent('download', { timeout: 5000 });
    await page.click('#btnExportJson');
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(/annotation.*\.json/);
  });

  test('5. 非法 JSON 导入不覆盖当前状态', async ({ page }) => {
    const { rawText, annotation } = await buildValidAnnotation(page, 'e2e-test-005');
    await injectState(page, annotation, rawText);
    await page.reload();
    await page.waitForSelector('#fieldsContainer .field-card');
    await page.waitForTimeout(300);

    const originalDocId = await page.inputValue('#documentId');

    const invalidJson = JSON.stringify({
      document_id: 'evil',
      annotator_id: 'X',
      annotation_version: '1.0'
    });
    fs.writeFileSync(TMP_INVALID_JSON, invalidJson, 'utf-8');

    const fileChooserPromise = page.waitForEvent('filechooser');
    const dialogPromise = nextDialogMessage(page);
    await page.click('#btnImportJson');
    const fileChooser = await fileChooserPromise;
    await fileChooser.setFiles(TMP_INVALID_JSON);
    const dialogMsg = await dialogPromise;
    try { fs.unlinkSync(TMP_INVALID_JSON); } catch (_) {}

    expect(dialogMsg).toContain('JSON 校验失败');
    expect(dialogMsg).toContain('当前草稿未被修改');

    const currentDocId = await page.inputValue('#documentId');
    expect(currentDocId).toBe(originalDocId);
  });

  test('6. 不同 document_id 草稿隔离', async ({ page }) => {
    const { rawText, annotation } = await buildValidAnnotation(page, 'doc-A');
    const annotationA = JSON.parse(JSON.stringify(annotation));
    annotationA.document_id = 'doc-A';
    annotationA.annotator_id = 'Alice';

    const annotationB = JSON.parse(JSON.stringify(annotation));
    annotationB.document_id = 'doc-B';
    annotationB.annotator_id = 'Bob';

    await page.evaluate(({ annotationA, annotationB, rawText }) => {
      localStorage.setItem('bidagent_annotation_draft_doc-A', JSON.stringify({
        rawText, annotation: annotationA, savedAt: new Date().toISOString()
      }));
      localStorage.setItem('bidagent_annotation_draft_doc-B', JSON.stringify({
        rawText, annotation: annotationB, savedAt: new Date().toISOString()
      }));
    }, { annotationA, annotationB, rawText });

    // 文档切换会弹出 confirm 对话框，自动接受
    page.on('dialog', d => d.accept());

    await page.fill('#documentId', 'doc-A');
    await page.press('#documentId', 'Tab');
    await page.waitForTimeout(800);

    let annotatorId = await page.inputValue('#annotatorId');
    expect(annotatorId).toBe('Alice');

    await page.fill('#documentId', 'doc-B');
    await page.press('#documentId', 'Tab');
    await page.waitForTimeout(800);

    annotatorId = await page.inputValue('#annotatorId');
    expect(annotatorId).toBe('Bob');

    await page.fill('#documentId', 'doc-A');
    await page.press('#documentId', 'Tab');
    await page.waitForTimeout(800);

    annotatorId = await page.inputValue('#annotatorId');
    expect(annotatorId).toBe('Alice');
  });

  test('7. XSS 文本不执行（<img src=x onerror=alert(1)>）', async ({ page }) => {
    const xssPayload = '<img src=x onerror=alert(1)>';

    const baseData = await buildValidAnnotation(page, 'xss-test');
    const rawText = baseData.rawText + '\n' + xssPayload;
    const annotation = baseData.annotation;

    await page.evaluate(({ rawText, annotation }) => {
      const draftKey = 'bidagent_annotation_draft_xss-test';
      localStorage.setItem(draftKey, JSON.stringify({
        rawText, annotation, savedAt: new Date().toISOString()
      }));
    }, { rawText, annotation });

    // 文档切换的 confirm 需要接受；XSS 触发的 alert 需要捕获
    let xssTriggered = false;
    page.on('dialog', d => {
      if (d.type() === 'confirm') {
        d.accept();
      } else {
        xssTriggered = true;
        d.dismiss();
      }
    });

    await page.fill('#documentId', 'xss-test');
    await page.press('#documentId', 'Tab');
    await page.waitForTimeout(1000);

    const imgCount = await page.evaluate(() => {
      const container = document.getElementById('textContainer');
      return container ? container.querySelectorAll('img').length : -1;
    });
    expect(imgCount).toBe(0);

    const textContainsXss = await page.evaluate(() => {
      const container = document.getElementById('textContainer');
      return container && container.textContent.includes('<img src=x onerror=alert(1)>');
    });
    expect(textContainsXss).toBe(true);

    expect(xssTriggered).toBe(false);
  });

  test('8. 重复文本选择第二处时偏移正确', async ({ page }) => {
    const dupText = '甲方：上海智汇科技有限公司。\n经评审，上海智汇科技有限公司中标。\n其他内容。';
    const { annotation } = await buildValidAnnotation(page, 'dup-test');
    await page.evaluate((dupText) => {
      window.__testDupText = dupText;
    }, dupText);

    await page.evaluate(({ annotation, dupText }) => {
      const draftKey = 'bidagent_annotation_draft_dup-test';
      localStorage.setItem(draftKey, JSON.stringify({
        rawText: dupText, annotation, savedAt: new Date().toISOString()
      }));
    }, { annotation, dupText });

    // 文档切换会弹出 confirm 对话框，自动接受
    page.on('dialog', d => d.accept());
    await page.fill('#documentId', 'dup-test');
    await page.press('#documentId', 'Tab');
    await page.waitForTimeout(1000);

    const positions = await page.evaluate(() => {
      const raw = window.App.state.rawText;
      const target = '上海智汇科技有限公司';
      const positions = [];
      let idx = 0;
      while (true) {
        const found = raw.indexOf(target, idx);
        if (found === -1) break;
        positions.push(found);
        idx = found + target.length;
      }
      return positions;
    });

    expect(positions.length).toBeGreaterThanOrEqual(2);

    const startOffset = positions[1];
    const endOffset = startOffset + '上海智汇科技有限公司'.length;

    await page.evaluate(({ startOffset, endOffset }) => {
      const rawEl = document.getElementById('rawText');
      const range = document.createRange();
      const walker = document.createTreeWalker(rawEl, NodeFilter.SHOW_TEXT, null);
      let cumulative = 0;
      let startNode = null, startOff = 0, endNode = null, endOff = 0;
      while (walker.nextNode()) {
        const node = walker.currentNode;
        const len = node.nodeValue.length;
        if (!startNode && cumulative + len >= startOffset) {
          startNode = node;
          startOff = startOffset - cumulative;
        }
        if (!endNode && cumulative + len >= endOffset) {
          endNode = node;
          endOff = endOffset - cumulative;
        }
        cumulative += len;
      }
      range.setStart(startNode, startOff);
      range.setEnd(endNode, endOff);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      rawEl.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
    }, { startOffset, endOffset });

    await page.waitForTimeout(300);

    const selInfo = await page.textContent('#selectionInfo');
    expect(selInfo).toContain('上海智汇科技有限公司');
    expect(selInfo).toContain(`${startOffset}`);

    expect(selInfo).not.toContain(`${positions[0]}`);
  });

  test('9. 高亮后继续选择第二段证据', async ({ page }) => {
    const { rawText, annotation } = await buildValidAnnotation(page, 'highlight-test');
    await injectState(page, annotation, rawText);
    await page.reload();
    await page.waitForSelector('#fieldsContainer .field-card');
    await page.waitForTimeout(500);

    const highlightCount = await page.evaluate(() => {
      const rawEl = document.getElementById('rawText');
      return rawEl.querySelectorAll('.evidence-highlight').length;
    });
    expect(highlightCount).toBeGreaterThan(0);

    const target = '上海智汇科技有限公司';
    const offset = await page.evaluate((target) => {
      return window.App.state.rawText.indexOf(target);
    }, target);

    await page.evaluate(({ offset, target }) => {
      const rawEl = document.getElementById('rawText');
      const range = document.createRange();
      const walker = document.createTreeWalker(rawEl, NodeFilter.SHOW_TEXT, null);
      let cumulative = 0;
      let startNode = null, startOff = 0, endNode = null, endOff = 0;
      const endOffset = offset + target.length;
      while (walker.nextNode()) {
        const node = walker.currentNode;
        const len = node.nodeValue.length;
        if (!startNode && cumulative + len >= offset) {
          startNode = node;
          startOff = offset - cumulative;
        }
        if (!endNode && cumulative + len >= endOffset) {
          endNode = node;
          endOff = endOffset - cumulative;
        }
        cumulative += len;
      }
      range.setStart(startNode, startOff);
      range.setEnd(endNode, endOff);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      rawEl.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
    }, { offset, target });

    await page.waitForTimeout(300);

    const selInfo = await page.textContent('#selectionInfo');
    expect(selInfo).toContain(target);
    expect(selInfo).toContain(`${offset}`);
  });

  test('10. noticeType 和 annotationStatus 保存恢复', async ({ page }) => {
    // 使用 'sample-001' 作为 document_id，确保 saveToStorage 保存的 key 与 init() 加载的 key 一致
    const { rawText, annotation } = await buildValidAnnotation(page, 'sample-001');
    await injectState(page, annotation, rawText);
    await page.reload();
    await page.waitForSelector('#fieldsContainer .field-card');
    await page.waitForTimeout(300);

    await page.selectOption('#noticeType', 'award');
    await page.selectOption('#annotationStatus', 'done');
    await page.waitForTimeout(1000);

    await page.reload();
    await page.waitForSelector('#fieldsContainer .field-card');
    await page.waitForTimeout(300);

    const noticeType = await page.inputValue('#noticeType');
    const annotationStatus = await page.inputValue('#annotationStatus');
    expect(noticeType).toBe('award');
    expect(annotationStatus).toBe('done');

    const downloadPromise = page.waitForEvent('download', { timeout: 5000 });
    await page.click('#btnExportJson');
    const download = await downloadPromise;
    await download.saveAs(TMP_META_JSON);
    const data = JSON.parse(fs.readFileSync(TMP_META_JSON, 'utf-8'));
    try { fs.unlinkSync(TMP_META_JSON); } catch (_) {}

    expect(data.fields.length).toBe(6);
    expect(data.noticeType).toBeUndefined();
    expect(data.annotationStatus).toBeUndefined();
  });

  test('11. 导出后重新导入数据一致', async ({ page }) => {
    const { rawText, annotation } = await buildValidAnnotation(page, 'roundtrip-test');
    await injectState(page, annotation, rawText);
    await page.reload();
    await page.waitForSelector('#fieldsContainer .field-card');
    await page.waitForTimeout(300);

    const downloadPromise = page.waitForEvent('download', { timeout: 5000 });
    await page.click('#btnExportJson');
    const download = await downloadPromise;
    await download.saveAs(TMP_ROUNDTRIP_JSON);

    await page.fill('#annotatorId', 'CHANGED');
    await page.waitForTimeout(800);

    const fileChooserPromise = page.waitForEvent('filechooser');
    await page.click('#btnImportJson');
    const fileChooser = await fileChooserPromise;
    await fileChooser.setFiles(TMP_ROUNDTRIP_JSON);

    const successMsg = await new Promise(resolve => {
      const h = d => { page.off('dialog', h); resolve(d.message()); d.accept(); };
      page.on('dialog', h);
    });
    expect(successMsg).toContain('导入成功');

    const annotatorId = await page.inputValue('#annotatorId');
    expect(annotatorId).toBe('A');
    expect(annotatorId).not.toBe('CHANGED');

    const exportedData = JSON.parse(fs.readFileSync(TMP_ROUNDTRIP_JSON, 'utf-8'));
    expect(exportedData.document_id).toBe('roundtrip-test');
    expect(exportedData.annotator_id).toBe('A');
    expect(exportedData.fields.length).toBe(6);
    try { fs.unlinkSync(TMP_ROUNDTRIP_JSON); } catch (_) {}
  });

  test('12. fixture 不被 generate.py 覆盖', async () => {
    expect(fs.existsSync(SAMPLE_TEXT_PATH)).toBe(true);

    const hashBefore = sha256File(SAMPLE_TEXT_PATH);
    const mtimeBefore = fs.statSync(SAMPLE_TEXT_PATH).mtimeMs;

    const repoRoot = path.resolve(TOOL_DIR, '..');
    try { fs.rmSync(TMP_GEN_OUTPUT_DIR, { recursive: true, force: true }); } catch (_) {}
    execSync('python annotation_tool/fixtures/generate.py', {
      cwd: repoRoot,
      stdio: 'pipe',
      timeout: 30000,
      env: { ...process.env, BIDAGENT_OUTPUT_DIR: TMP_GEN_OUTPUT_DIR }
    });

    const hashAfter = sha256File(SAMPLE_TEXT_PATH);
    const mtimeAfter = fs.statSync(SAMPLE_TEXT_PATH).mtimeMs;

    expect(hashAfter).toBe(hashBefore);
    expect(mtimeAfter).toBe(mtimeBefore);

    try { fs.rmSync(TMP_GEN_OUTPUT_DIR, { recursive: true, force: true }); } catch (_) {}
  });

  // ============================================================
  // 布局验收测试（人工验收反馈固化）
  // ============================================================

  test('13. 六字段导航区存在且独立', async ({ page }) => {
    const navInfo = await page.evaluate(() => {
      const nav = document.getElementById('fieldsNav');
      const container = document.getElementById('fieldsContainer');
      if (!nav || !container) return { exists: false };
      const navRect = nav.getBoundingClientRect();
      const containerRect = container.getBoundingClientRect();
      return {
        exists: true,
        tagName: nav.tagName,
        itemCount: nav.querySelectorAll('.field-nav-item').length,
        navAboveContainer: navRect.bottom <= containerRect.top + 1,
      };
    });

    expect(navInfo.exists).toBe(true);
    expect(navInfo.tagName).toBe('NAV');
    expect(navInfo.itemCount).toBe(6);
    expect(navInfo.navAboveContainer).toBe(true);
  });

  test('14. 只显示一个字段编辑器', async ({ page }) => {
    const cardCount = await page.evaluate(() => {
      return document.getElementById('fieldsContainer').querySelectorAll('.field-card').length;
    });
    expect(cardCount).toBe(1);
  });

  test('15. 点击导航切换字段', async ({ page }) => {
    // 点击第 3 个导航项（索引 2，中标人名称）
    const navItems = await page.$$('.field-nav-item');
    expect(navItems.length).toBeGreaterThanOrEqual(3);
    await navItems[2].click();
    await page.waitForTimeout(500);

    const switchInfo = await page.evaluate(() => {
      const items = Array.from(document.querySelectorAll('.field-nav-item'));
      const activeItem = document.querySelector('.field-nav-item.active');
      const card = document.querySelector('.field-card');
      return {
        activeIndex: activeItem ? items.indexOf(activeItem) : -1,
        cardFieldName: card ? card.querySelector('.field-name')?.textContent : null,
      };
    });

    expect(switchInfo.activeIndex).toBe(2);
    expect(switchInfo.cardFieldName).toContain('中标人');
  });

  test('16. 值项完整显示不被截断', async ({ page }) => {
    // 切换回第 1 个字段
    const navItems = await page.$$('.field-nav-item');
    await navItems[0].click();
    await page.waitForTimeout(500);

    const valueInfo = await page.evaluate(() => {
      const valueItem = document.querySelector('.value-item');
      if (!valueItem) return { exists: false };
      const rect = valueItem.getBoundingClientRect();
      const labels = Array.from(valueItem.querySelectorAll('.value-field label, .evidence-label'))
        .map(l => l.textContent.trim());
      const hasOverflow = valueItem.scrollHeight > valueItem.clientHeight + 2;
      const viewportHeight = window.innerHeight;
      const fullyVisible = rect.top >= 0 && rect.bottom <= viewportHeight;
      return {
        exists: true,
        labels,
        hasOverflow,
        fullyVisible,
        height: rect.height,
      };
    });

    expect(valueInfo.exists).toBe(true);
    expect(valueInfo.labels).toContain('原始值');
    expect(valueInfo.labels).toContain('归一化值');
    expect(valueInfo.hasOverflow).toBe(false);
    expect(valueInfo.fullyVisible).toBe(true);
  });

  test('17. 示例公告类型默认为 award（原文为中标结果公告）', async ({ page }) => {
    const noticeType = await page.inputValue('#noticeType');
    expect(noticeType).toBe('award');
  });

});
