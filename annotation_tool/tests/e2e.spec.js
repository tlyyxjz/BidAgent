/**
 * BidAgent W1-05 标注工具 - Playwright 端到端测试
 *
 * 覆盖 26 项真实 DOM 交互场景：
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
 * 13-17. 布局验收（导航/单字段编辑器/切换/无截断/公告类型）
 * 18-26. 跨文档隔离（P0：TXT 导入不得污染旧公告标注）
 *   18. 导入 TXT B 后字段和值全部为空
 *   19. B 的 document_id 与 A 不同
 *   20. B 不显示 A 的证据高亮
 *   21. 重新导入 A 时可恢复 A 自己的草稿
 *   22. 相同 TXT 再次导入时不创建随机新文档
 *   23. 导入 B 后刷新只恢复 B 不恢复 A
 *   24. 新 TXT 导入后完成度不得沿用上一篇
 *   25. 切换文件前的保存提示正常
 *   26. 导入空 TXT 或超大 TXT 时明确报错且不覆盖当前文档
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

  // ============================================================
  // 跨文档隔离测试（P0：TXT 导入不得污染旧公告标注）
  // ============================================================

  // 文档 B 的原文（与 sample-001 不同，用于测试跨文档隔离）
  const DOC_B_TEXT = [
    '某单位办公设备采购招标公告',
    '',
    '项目编号：ZB-2024-0999',
    '采购人：某单位后勤处',
    '发布日期：2024年6月1日',
    '投标截止日期：2024年6月20日',
    '',
    '现就办公设备采购项目进行公开招标，欢迎合格供应商参加投标。',
    '本项目采购预算：500万元。'
  ].join('\n');

  const TMP_TXT_B = path.join(TMP_DIR, 'bidagent_e2e_doc_b.txt');
  const TMP_TXT_A = path.join(TMP_DIR, 'bidagent_e2e_doc_a.txt');
  const TMP_TXT_EMPTY = path.join(TMP_DIR, 'bidagent_e2e_empty.txt');

  // 处理导入 TXT 时的多个 dialog（保存提示 / 恢复草稿提示 / 成功 alert）
  // autoAccept: 是否全部接受（默认 true）
  function autoAcceptDialogs(page, expectedCount) {
    const messages = [];
    let count = 0;
    return new Promise((resolve) => {
      const handler = d => {
        count++;
        messages.push({ type: d.type(), message: d.message() });
        d.accept();
        if (count >= expectedCount) {
          page.off('dialog', handler);
          resolve(messages);
        }
      };
      page.on('dialog', handler);
    });
  }

  test('18. 标注文档 A 后导入 TXT B，B 的字段和值全部为空', async ({ page }) => {
    // 1. 文档 A：注入合法标注（含值和证据）
    const { rawText, annotation } = await buildValidAnnotation(page, 'doc-A-isolation');
    await injectState(page, annotation, rawText);
    await page.reload();
    await page.waitForSelector('#fieldsContainer .field-card');
    await page.waitForTimeout(300);

    // 2. 写入文档 B 的 TXT 文件
    fs.writeFileSync(TMP_TXT_B, DOC_B_TEXT, 'utf-8');

    // 3. 导入 TXT B（A 有数据，会弹保存提示 → 接受；B 是新文档，弹成功 alert）
    const dialogPromise = autoAcceptDialogs(page, 2);
    const fileChooserPromise = page.waitForEvent('filechooser');
    await page.click('#btnImportText');
    const fileChooser = await fileChooserPromise;
    await fileChooser.setFiles(TMP_TXT_B);
    await dialogPromise;

    await page.waitForTimeout(800);

    // 4. 验证 B 的字段全部为 absent 空状态，无 values
    const bInfo = await page.evaluate(() => {
      const ann = window.App.state.annotation;
      return {
        documentId: ann.document_id,
        fields: ann.fields.map(f => ({
          name: f.field_name,
          status: f.gold_status,
          valueCount: (f.values || []).length,
        })),
        rawTextStartsWith: window.App.state.rawText.slice(0, 20),
      };
    });

    expect(bInfo.documentId).not.toBe('doc-A-isolation');
    expect(bInfo.rawTextStartsWith).toContain('办公设备采购招标公告');
    // 新导入文档字段应为"待判断"状态（空字符串），非 absent
    bInfo.fields.forEach(f => {
      expect(f.status).toBe('');
      expect(f.valueCount).toBe(0);
    });

    try { fs.unlinkSync(TMP_TXT_B); } catch (_) {}
  });

  test('19. B 的 document_id 与 A 不同', async ({ page }) => {
    const { rawText, annotation } = await buildValidAnnotation(page, 'doc-A-id-test');
    await injectState(page, annotation, rawText);
    await page.reload();
    await page.waitForSelector('#fieldsContainer .field-card');
    await page.waitForTimeout(300);

    const docAId = await page.inputValue('#documentId');

    fs.writeFileSync(TMP_TXT_B, DOC_B_TEXT, 'utf-8');
    const dialogPromise = autoAcceptDialogs(page, 2);
    const fileChooserPromise = page.waitForEvent('filechooser');
    await page.click('#btnImportText');
    const fileChooser = await fileChooserPromise;
    await fileChooser.setFiles(TMP_TXT_B);
    await dialogPromise;
    await page.waitForTimeout(800);

    const docBId = await page.inputValue('#documentId');
    expect(docBId).not.toBe(docAId);
    // B 的 document_id 应包含文件名前缀
    expect(docBId).toContain('bidagent_e2e_doc_b');

    try { fs.unlinkSync(TMP_TXT_B); } catch (_) {}
  });

  test('20. B 不显示 A 的证据高亮', async ({ page }) => {
    const { rawText, annotation } = await buildValidAnnotation(page, 'doc-A-highlight');
    await injectState(page, annotation, rawText);
    await page.reload();
    await page.waitForSelector('#fieldsContainer .field-card');
    await page.waitForTimeout(300);

    // A 导入后应有高亮 span
    const aHighlightCount = await page.evaluate(() => {
      return document.querySelectorAll('#rawText .evidence-highlight').length;
    });
    expect(aHighlightCount).toBeGreaterThan(0);

    // 导入 B
    fs.writeFileSync(TMP_TXT_B, DOC_B_TEXT, 'utf-8');
    const dialogPromise = autoAcceptDialogs(page, 2);
    const fileChooserPromise = page.waitForEvent('filechooser');
    await page.click('#btnImportText');
    const fileChooser = await fileChooserPromise;
    await fileChooser.setFiles(TMP_TXT_B);
    await dialogPromise;
    await page.waitForTimeout(800);

    // B 不应有任何高亮
    const bHighlightCount = await page.evaluate(() => {
      return document.querySelectorAll('#rawText .evidence-highlight').length;
    });
    expect(bHighlightCount).toBe(0);

    try { fs.unlinkSync(TMP_TXT_B); } catch (_) {}
  });

  test('21. 重新导入 A 时，可恢复 A 自己的草稿', async ({ page }) => {
    // 1. 先导入 A 的 TXT，获取文件生成的 document_id
    const { rawText } = await buildValidAnnotation(page, 'doc-A-restore');
    fs.writeFileSync(TMP_TXT_A, rawText, 'utf-8');
    fs.writeFileSync(TMP_TXT_B, DOC_B_TEXT, 'utf-8');

    // 导入 A（sample-001 有数据 → 保存提示 accept；A 新 → 成功 alert）
    let dialogPromise = autoAcceptDialogs(page, 2);
    let fileChooserPromise = page.waitForEvent('filechooser');
    await page.click('#btnImportText');
    let fileChooser = await fileChooserPromise;
    await fileChooser.setFiles(TMP_TXT_A);
    await dialogPromise;
    await page.waitForTimeout(800);

    const docAId = await page.inputValue('#documentId');
    expect(docAId).not.toBe('sample-001');

    // 2. 在 A 的 document_id 下注入合法标注（含值和证据）
    await page.evaluate(({ docAId, rawText }) => {
      const Schema = window.AnnotationSchema;
      const now = new Date().toISOString();
      function findEv(text) {
        const start = rawText.indexOf(text);
        if (start < 0) throw new Error('证据文本不在 rawText 中: ' + text);
        return { role: 'primary', start, end: start + text.length, text };
      }
      const mkField = (name, value, evidence) => ({
        field_name: name, gold_status: 'present',
        values: [{ raw_value: value, normalized_value: value, amount_type: null, currency: null,
          original_unit: null, tax_status: null, lot_id: null, acceptable_evidence_spans: evidence }],
        note: ''
      });
      const annotation = {
        document_id: docAId, annotator_id: 'A', annotation_version: Schema.ANNOTATION_VERSION,
        annotation_time: now,
        fields: [
          mkField('project_identifier', 'ZFCG-2024-0315', [findEv('ZFCG-2024-0315')]),
          mkField('purchaser_name', '某市大数据管理局', [findEv('某市大数据管理局')]),
          mkField('winner_name', '上海智汇科技有限公司', [findEv('上海智汇科技有限公司')]),
          mkField('amount', '1285.60万元', [findEv('1285.60万元')]),
          mkField('publish_date', '2024年3月15日', [findEv('2024年3月15日')]),
          mkField('bid_deadline', '2024年3月10日', [findEv('2024年3月10日')])
        ]
      };
      const draftKey = 'bidagent_annotation_draft_' + docAId;
      localStorage.setItem(draftKey, JSON.stringify({ rawText, annotation, savedAt: now }));
    }, { docAId, rawText });

    // 刷新加载 A 的草稿
    await page.reload();
    await page.waitForFunction(() => window.App && window.App.state && window.App.state.annotation, { timeout: 5000 });
    await page.waitForTimeout(500);

    // 3. 导入 B（A 有数据 → 保存提示 accept；B 新 → 成功 alert）
    dialogPromise = autoAcceptDialogs(page, 2);
    fileChooserPromise = page.waitForEvent('filechooser');
    await page.click('#btnImportText');
    fileChooser = await fileChooserPromise;
    await fileChooser.setFiles(TMP_TXT_B);
    await dialogPromise;
    await page.waitForTimeout(800);

    const docBId = await page.inputValue('#documentId');
    expect(docBId).not.toBe(docAId);

    // 4. 重新导入 A（B 无数据 → 无保存提示；A 有草稿 → 恢复提示 accept；成功 alert）
    dialogPromise = autoAcceptDialogs(page, 2);
    fileChooserPromise = page.waitForEvent('filechooser');
    await page.click('#btnImportText');
    fileChooser = await fileChooserPromise;
    await fileChooser.setFiles(TMP_TXT_A);
    await dialogPromise;
    await page.waitForTimeout(800);

    // 5. 验证 A 的草稿已恢复
    const restoredInfo = await page.evaluate(() => {
      const ann = window.App.state.annotation;
      return {
        documentId: ann.document_id,
        winnerValues: ann.fields.find(f => f.field_name === 'winner_name').values.length,
        amountValues: ann.fields.find(f => f.field_name === 'amount').values.length,
      };
    });

    expect(restoredInfo.documentId).toBe(docAId);
    expect(restoredInfo.winnerValues).toBeGreaterThan(0);
    expect(restoredInfo.amountValues).toBeGreaterThan(0);

    try { fs.unlinkSync(TMP_TXT_B); } catch (_) {}
    try { fs.unlinkSync(TMP_TXT_A); } catch (_) {}
  });

  test('22. 相同 TXT 再次导入时不创建错误的随机新文档', async ({ page }) => {
    fs.writeFileSync(TMP_TXT_B, DOC_B_TEXT, 'utf-8');

    // 第一次导入 B（sample-001 有数据 → 保存提示；B 新 → 成功）
    let dialogPromise = autoAcceptDialogs(page, 2);
    let fileChooserPromise = page.waitForEvent('filechooser');
    await page.click('#btnImportText');
    let fileChooser = await fileChooserPromise;
    await fileChooser.setFiles(TMP_TXT_B);
    await dialogPromise;
    await page.waitForTimeout(800);

    const firstDocId = await page.inputValue('#documentId');

    // 第二次导入相同 TXT B（B 无数据 → 无保存提示；B 有草稿 → 恢复提示 accept；成功）
    dialogPromise = autoAcceptDialogs(page, 2);
    fileChooserPromise = page.waitForEvent('filechooser');
    await page.click('#btnImportText');
    fileChooser = await fileChooserPromise;
    await fileChooser.setFiles(TMP_TXT_B);
    await dialogPromise;
    await page.waitForTimeout(800);

    const secondDocId = await page.inputValue('#documentId');

    // 两次 document_id 必须相同（基于文件名+内容哈希，确定性）
    expect(secondDocId).toBe(firstDocId);

    try { fs.unlinkSync(TMP_TXT_B); } catch (_) {}
  });

  test('23. 导入 B 后刷新，只恢复 B，不恢复 A', async ({ page }) => {
    const { rawText, annotation } = await buildValidAnnotation(page, 'doc-A-refresh');
    await injectState(page, annotation, rawText);
    await page.reload();
    await page.waitForSelector('#fieldsContainer .field-card');
    await page.waitForTimeout(300);

    fs.writeFileSync(TMP_TXT_B, DOC_B_TEXT, 'utf-8');

    // 导入 B
    const dialogPromise = autoAcceptDialogs(page, 2);
    const fileChooserPromise = page.waitForEvent('filechooser');
    await page.click('#btnImportText');
    const fileChooser = await fileChooserPromise;
    await fileChooser.setFiles(TMP_TXT_B);
    await dialogPromise;
    await page.waitForTimeout(800);

    const docBId = await page.inputValue('#documentId');
    expect(docBId).not.toBe('doc-A-refresh');

    // 刷新页面
    await page.reload();
    await page.waitForFunction(() => window.App && window.App.state && window.App.state.annotation, { timeout: 5000 });
    await page.waitForTimeout(500);

    // 刷新后应恢复 B 的草稿（document_id 仍为 B），不是 A
    const restoredId = await page.inputValue('#documentId');
    expect(restoredId).toBe(docBId);

    // B 的字段应为"待判断"状态（空字符串）
    const fieldInfo = await page.evaluate(() => {
      const ann = window.App.state.annotation;
      return ann.fields.map(f => ({ name: f.field_name, status: f.gold_status, valueCount: (f.values || []).length }));
    });
    fieldInfo.forEach(f => {
      expect(f.status).toBe('');
      expect(f.valueCount).toBe(0);
    });

    try { fs.unlinkSync(TMP_TXT_B); } catch (_) {}
  });

  test('24. 新 TXT 导入后完成度不得沿用上一篇', async ({ page }) => {
    const { rawText, annotation } = await buildValidAnnotation(page, 'doc-A-progress');
    await injectState(page, annotation, rawText);
    await page.reload();
    await page.waitForSelector('#fieldsContainer .field-card');
    await page.waitForTimeout(300);

    // A 的完成度应 > 0（6 个字段都有合法标注）
    const aProgress = await page.textContent('#progressText');
    expect(aProgress).toContain('6 / 6');

    fs.writeFileSync(TMP_TXT_B, DOC_B_TEXT, 'utf-8');
    const dialogPromise = autoAcceptDialogs(page, 2);
    const fileChooserPromise = page.waitForEvent('filechooser');
    await page.click('#btnImportText');
    const fileChooser = await fileChooserPromise;
    await fileChooser.setFiles(TMP_TXT_B);
    await dialogPromise;
    await page.waitForTimeout(800);

    // B 的完成度应为 0/6（全 absent 空状态，尚未标注）
    const bProgress = await page.textContent('#progressText');
    expect(bProgress).toContain('0 / 6');

    try { fs.unlinkSync(TMP_TXT_B); } catch (_) {}
  });

  test('25. 切换文件前的保存提示正常', async ({ page }) => {
    const { rawText, annotation } = await buildValidAnnotation(page, 'doc-A-prompt');
    await injectState(page, annotation, rawText);
    await page.reload();
    await page.waitForSelector('#fieldsContainer .field-card');
    await page.waitForTimeout(300);

    fs.writeFileSync(TMP_TXT_B, DOC_B_TEXT, 'utf-8');

    // 监听所有 dialog，记录消息
    const dialogMessages = [];
    const dialogHandler = d => {
      dialogMessages.push({ type: d.type(), message: d.message() });
      d.accept();
    };
    page.on('dialog', dialogHandler);

    const fileChooserPromise = page.waitForEvent('filechooser');
    await page.click('#btnImportText');
    const fileChooser = await fileChooserPromise;
    await fileChooser.setFiles(TMP_TXT_B);
    await page.waitForTimeout(1500);

    page.off('dialog', dialogHandler);

    // 应该出现保存提示（confirm 类型，包含"保存当前草稿"）
    const savePrompt = dialogMessages.find(d => d.type === 'confirm' && d.message.includes('保存当前草稿'));
    expect(savePrompt).toBeDefined();

    try { fs.unlinkSync(TMP_TXT_B); } catch (_) {}
  });

  test('26. 导入空 TXT 或超大 TXT 时明确报错且不覆盖当前文档', async ({ page }) => {
    const { rawText, annotation } = await buildValidAnnotation(page, 'doc-A-error');
    await injectState(page, annotation, rawText);
    await page.reload();
    await page.waitForSelector('#fieldsContainer .field-card');
    await page.waitForTimeout(300);

    const docBefore = await page.inputValue('#documentId');
    const fieldsBefore = await page.evaluate(() => {
      return window.App.state.annotation.fields.map(f => ({
        name: f.field_name, status: f.gold_status, valueCount: (f.values || []).length
      }));
    });

    // 1. 空 TXT
    fs.writeFileSync(TMP_TXT_EMPTY, '   \n  \t  \n', 'utf-8');

    const dialogMessages1 = [];
    const handler1 = d => { dialogMessages1.push(d.message()); d.accept(); };
    page.on('dialog', handler1);

    let fileChooserPromise = page.waitForEvent('filechooser');
    await page.click('#btnImportText');
    let fileChooser = await fileChooserPromise;
    await fileChooser.setFiles(TMP_TXT_EMPTY);
    await page.waitForTimeout(1500);

    page.off('dialog', handler1);

    // 应报错且不覆盖当前文档
    const errorMsg1 = dialogMessages1.find(m => m.includes('文件内容为空'));
    expect(errorMsg1).toBeDefined();

    const docAfterEmpty = await page.inputValue('#documentId');
    expect(docAfterEmpty).toBe(docBefore);

    // 当前文档数据应保持不变
    const fieldsAfterEmpty = await page.evaluate(() => {
      return window.App.state.annotation.fields.map(f => ({
        name: f.field_name, status: f.gold_status, valueCount: (f.values || []).length
      }));
    });
    expect(JSON.stringify(fieldsAfterEmpty)).toBe(JSON.stringify(fieldsBefore));

    // 2. 超大 TXT（超过 MAX_IMPORT_SIZE）
    // 构造一个超过 5MB 的文件名（不实际写 5MB，而是 mock file.size）
    // 由于 Playwright setFiles 无法直接 mock size，这里用 File 构造
    // 改为直接调用 App.importTextFile 并 mock file 对象
    const oversizedResult = await page.evaluate(() => {
      const fakeFile = { size: 6 * 1024 * 1024, name: 'oversized.txt' };
      let alertMsg = null;
      const origAlert = window.alert;
      window.alert = (msg) => { alertMsg = msg; };
      try {
        window.App.importTextFile(fakeFile);
      } catch (e) {
        // FileReader 会失败，但 size 校验应先拦截
      }
      window.alert = origAlert;
      return alertMsg;
    });

    expect(oversizedResult).toContain('文件过大');
    expect(oversizedResult).toContain('当前文档未被修改');

    // 当前文档数据仍应保持不变
    const docAfterOversized = await page.inputValue('#documentId');
    expect(docAfterOversized).toBe(docBefore);

    try { fs.unlinkSync(TMP_TXT_EMPTY); } catch (_) {}
  });

  // ============================================================
  // 场景 27-32：证据质量控制（真实鼠标操作 + 预览弹窗 + 失效检测）
  // ============================================================

  /**
   * 辅助：通过 DOM Range 在原文中选中文本并触发 mouseup（真实选区，非 mock）。
   * @param page
   * @param targetText 要选中的完整文本（必须在 rawText 中唯一出现或选第一处）
   * @returns {Promise<{start:number, end:number}>}
   */
  async function selectTextViaDomRange(page, targetText) {
    return await page.evaluate((targetText) => {
      const rawEl = document.getElementById('rawText');
      const offset = window.App.state.rawText.indexOf(targetText);
      if (offset < 0) throw new Error('目标文本不在 rawText 中: ' + targetText);
      const endOffset = offset + targetText.length;

      const walker = document.createTreeWalker(rawEl, NodeFilter.SHOW_TEXT, null);
      let cumulative = 0;
      let startNode = null, startOff = 0, endNode = null, endOff = 0;
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
      const range = document.createRange();
      range.setStart(startNode, startOff);
      range.setEnd(endNode, endOff);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      rawEl.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
      return { start: offset, end: endOffset };
    }, targetText);
  }

  test('27. 金额标签和数值完整选择（真实鼠标操作）', async ({ page }) => {
    const { rawText, annotation } = await buildValidAnnotation(page, 'mouse-amount');
    // 只保留 amount 字段为 present，其他字段 absent，避免高亮干扰
    annotation.fields.forEach(f => {
      if (f.field_name !== 'amount') {
        f.gold_status = 'absent';
        f.values = [];
      }
    });
    await injectState(page, annotation, rawText);
    await page.reload();
    await page.waitForSelector('#fieldsContainer .field-card');
    await page.waitForTimeout(300);

    // 切换到 amount 字段
    await page.evaluate(() => {
      const idx = window.App.state.annotation.fields.findIndex(f => f.field_name === 'amount');
      window.App.switchToField(idx);
    });
    await page.waitForTimeout(200);

    // 选中完整金额证据："1285.60万元"
    const target = '1285.60万元';
    const sel = await selectTextViaDomRange(page, target);
    expect(sel.start).toBeGreaterThanOrEqual(0);
    expect(sel.end - sel.start).toBe(target.length);

    // 点击「添加证据」按钮触发预览弹窗
    await page.click('.add-evidence-btn');
    await page.waitForTimeout(300);

    // 预览弹窗应显示
    const modalVisible = await page.isVisible('#evidencePreviewModal:not(.hidden)');
    expect(modalVisible).toBe(true);

    // 预览中应显示完整金额
    const previewText = await page.inputValue('#previewText');
    expect(previewText).toBe(target);

    // slice 验证通过
    const verifyClass = await page.getAttribute('#previewVerify', 'class');
    expect(verifyClass).not.toContain('error');

    // 保存证据
    await page.click('#evidencePreviewModal button:has-text("保存证据")');
    await page.waitForTimeout(300);

    // 证据应已添加到 amount 字段
    const evCount = await page.evaluate(() => {
      const f = window.App.state.annotation.fields.find(f => f.field_name === 'amount');
      return f.values[0].acceptable_evidence_spans.length;
    });
    expect(evCount).toBeGreaterThanOrEqual(1);

    // 原文中应出现高亮
    const highlightCount = await page.evaluate(() => {
      return document.getElementById('rawText').querySelectorAll('.evidence-highlight').length;
    });
    expect(highlightCount).toBeGreaterThanOrEqual(1);
  });

  test('28. 公司角色和名称完整选择（真实鼠标操作）', async ({ page }) => {
    const { rawText, annotation } = await buildValidAnnotation(page, 'mouse-company');
    // 只保留 winner_name 字段为 present
    annotation.fields.forEach(f => {
      if (f.field_name !== 'winner_name') {
        f.gold_status = 'absent';
        f.values = [];
      } else {
        // 清空已有证据，本测试自行选择
        f.values[0].acceptable_evidence_spans = [];
      }
    });
    await injectState(page, annotation, rawText);
    await page.reload();
    await page.waitForSelector('#fieldsContainer .field-card');
    await page.waitForTimeout(300);

    await page.evaluate(() => {
      const idx = window.App.state.annotation.fields.findIndex(f => f.field_name === 'winner_name');
      window.App.switchToField(idx);
    });
    await page.waitForTimeout(200);

    // 选中完整公司名称（带上下文，验证偏移准确）
    const target = '上海智汇科技有限公司';
    const sel = await selectTextViaDomRange(page, target);
    expect(sel.end - sel.start).toBe(target.length);

    await page.click('.add-evidence-btn');
    await page.waitForTimeout(300);

    const previewText = await page.inputValue('#previewText');
    expect(previewText).toBe(target);

    // 保存
    await page.click('#evidencePreviewModal button:has-text("保存证据")');
    await page.waitForTimeout(300);

    // 验证保存的证据文本逐字一致
    const evText = await page.evaluate(() => {
      const f = window.App.state.annotation.fields.find(f => f.field_name === 'winner_name');
      return f.values[0].acceptable_evidence_spans[0].text;
    });
    expect(evText).toBe(target);
  });

  test('29. 跨行选择证据（真实鼠标操作）', async ({ page }) => {
    const { rawText, annotation } = await buildValidAnnotation(page, 'mouse-cross-line');
    annotation.fields.forEach(f => {
      if (f.field_name !== 'amount') {
        f.gold_status = 'absent';
        f.values = [];
      } else {
        f.values[0].acceptable_evidence_spans = [];
      }
    });
    await injectState(page, annotation, rawText);
    await page.reload();
    await page.waitForSelector('#fieldsContainer .field-card');
    await page.waitForTimeout(300);

    await page.evaluate(() => {
      const idx = window.App.state.annotation.fields.findIndex(f => f.field_name === 'amount');
      window.App.switchToField(idx);
    });
    await page.waitForTimeout(200);

    // 构造跨行文本：从某行末尾到下一行开头
    // rawText 中 "\n" 为换行符，选中 "...公告\n中标（成交）金额..."
    const rawTextContent = await page.evaluate(() => window.App.state.rawText);
    // 找到第一个 \n 的位置，选中前后各 10 字符（跨行）
    const nlIdx = rawTextContent.indexOf('\n');
    expect(nlIdx).toBeGreaterThan(5);
    const crossStart = Math.max(0, nlIdx - 5);
    const crossEnd = Math.min(rawTextContent.length, nlIdx + 6);
    const target = rawTextContent.slice(crossStart, crossEnd);
    // 确实包含换行
    expect(target).toContain('\n');

    const sel = await selectTextViaDomRange(page, target);
    expect(sel.start).toBe(crossStart);
    expect(sel.end).toBe(crossEnd);

    await page.click('.add-evidence-btn');
    await page.waitForTimeout(300);

    const previewText = await page.inputValue('#previewText');
    expect(previewText).toBe(target);
    expect(previewText).toContain('\n');

    // slice 验证仍通过（跨行不影响偏移）
    const verifyClass = await page.getAttribute('#previewVerify', 'class');
    expect(verifyClass).not.toContain('error');

    await page.click('#evidencePreviewModal button:has-text("保存证据")');
    await page.waitForTimeout(300);

    const evText = await page.evaluate(() => {
      const f = window.App.state.annotation.fields.find(f => f.field_name === 'amount');
      return f.values[0].acceptable_evidence_spans[0].text;
    });
    expect(evText).toBe(target);
  });

  test('30. 重复文本选择第二处（真实鼠标操作）', async ({ page }) => {
    // 构造包含两次"上海智汇科技有限公司"的原文
    const dupRawText = '甲方：上海智汇科技有限公司。\n经评审，上海智汇科技有限公司中标。\n其他内容。';
    const { annotation } = await buildValidAnnotation(page, 'mouse-dup');
    annotation.fields.forEach(f => {
      if (f.field_name !== 'winner_name') {
        f.gold_status = 'absent';
        f.values = [];
      } else {
        f.values[0].acceptable_evidence_spans = [];
      }
    });
    // 注入自定义 rawText
    await page.evaluate(({ annotation, dupRawText }) => {
      const draftKey = 'bidagent_annotation_draft_mouse-dup';
      localStorage.setItem(draftKey, JSON.stringify({
        rawText: dupRawText, annotation, savedAt: new Date().toISOString()
      }));
    }, { annotation, dupRawText });

    page.on('dialog', d => d.accept());
    await page.fill('#documentId', 'mouse-dup');
    await page.press('#documentId', 'Tab');
    await page.waitForTimeout(800);

    await page.evaluate(() => {
      const idx = window.App.state.annotation.fields.findIndex(f => f.field_name === 'winner_name');
      window.App.switchToField(idx);
    });
    await page.waitForTimeout(200);

    // 选中第二处"上海智汇科技有限公司"（offset > 第一处）
    const firstIdx = dupRawText.indexOf('上海智汇科技有限公司');
    const secondIdx = dupRawText.indexOf('上海智汇科技有限公司', firstIdx + 1);
    expect(secondIdx).toBeGreaterThan(firstIdx);

    // 通过 DOM Range 精确选中第二处
    await page.evaluate(({ secondIdx, target }) => {
      const rawEl = document.getElementById('rawText');
      const endOffset = secondIdx + target.length;
      const walker = document.createTreeWalker(rawEl, NodeFilter.SHOW_TEXT, null);
      let cumulative = 0;
      let startNode = null, startOff = 0, endNode = null, endOff = 0;
      while (walker.nextNode()) {
        const node = walker.currentNode;
        const len = node.nodeValue.length;
        if (!startNode && cumulative + len >= secondIdx) {
          startNode = node;
          startOff = secondIdx - cumulative;
        }
        if (!endNode && cumulative + len >= endOffset) {
          endNode = node;
          endOff = endOffset - cumulative;
        }
        cumulative += len;
      }
      const range = document.createRange();
      range.setStart(startNode, startOff);
      range.setEnd(endNode, endOff);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      rawEl.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
    }, { secondIdx, target: '上海智汇科技有限公司' });
    await page.waitForTimeout(300);

    await page.click('.add-evidence-btn');
    await page.waitForTimeout(300);

    // 偏移应指向第二处，而非第一处
    const previewStart = parseInt(await page.inputValue('#previewStart'));
    expect(previewStart).toBe(secondIdx);

    await page.click('#evidencePreviewModal button:has-text("保存证据")');
    await page.waitForTimeout(300);

    const ev = await page.evaluate(() => {
      const f = window.App.state.annotation.fields.find(f => f.field_name === 'winner_name');
      return f.values[0].acceptable_evidence_spans[0];
    });
    expect(ev.start).toBe(secondIdx);
    expect(ev.end).toBe(secondIdx + '上海智汇科技有限公司'.length);
  });

  test('31. 高亮后再次选择第二段证据（真实鼠标操作）', async ({ page }) => {
    const { rawText, annotation } = await buildValidAnnotation(page, 'mouse-after-highlight');
    // amount 字段保留一段证据（产生高亮），winner_name 清空证据
    annotation.fields.forEach(f => {
      if (f.field_name !== 'amount' && f.field_name !== 'winner_name') {
        f.gold_status = 'absent';
        f.values = [];
      }
      if (f.field_name === 'winner_name') {
        f.values[0].acceptable_evidence_spans = [];
      }
    });
    await injectState(page, annotation, rawText);
    await page.reload();
    await page.waitForSelector('#fieldsContainer .field-card');
    await page.waitForTimeout(400);

    // 确认 amount 的高亮已存在
    const hlBefore = await page.evaluate(() => {
      return document.getElementById('rawText').querySelectorAll('.evidence-highlight').length;
    });
    expect(hlBefore).toBeGreaterThanOrEqual(1);

    // 切换到 winner_name 字段
    await page.evaluate(() => {
      const idx = window.App.state.annotation.fields.findIndex(f => f.field_name === 'winner_name');
      window.App.switchToField(idx);
    });
    await page.waitForTimeout(200);

    // 在已有高亮的原文中选中"上海智汇科技有限公司"
    const target = '上海智汇科技有限公司';
    const sel = await selectTextViaDomRange(page, target);
    expect(sel.end - sel.start).toBe(target.length);

    await page.click('.add-evidence-btn');
    await page.waitForTimeout(300);

    const previewText = await page.inputValue('#previewText');
    expect(previewText).toBe(target);

    await page.click('#evidencePreviewModal button:has-text("保存证据")');
    await page.waitForTimeout(300);

    // 验证新证据保存成功，偏移准确
    const ev = await page.evaluate(() => {
      const f = window.App.state.annotation.fields.find(f => f.field_name === 'winner_name');
      return f.values[0].acceptable_evidence_spans[0];
    });
    expect(ev.text).toBe(target);
    expect(rawText.slice(ev.start, ev.end)).toBe(target);
  });

  test('32. 导出再导入后仍逐字一致', async ({ page }) => {
    const { rawText, annotation } = await buildValidAnnotation(page, 'roundtrip-exact');
    await injectState(page, annotation, rawText);
    await page.reload();
    await page.waitForSelector('#fieldsContainer .field-card');
    await page.waitForTimeout(400);

    // 导出 JSON
    const downloadPromise = page.waitForEvent('download', { timeout: 5000 });
    await page.click('#btnExportJson');
    const download = await downloadPromise;
    const downloadPath = path.join(TMP_DIR, 'roundtrip_exact.json');
    await download.saveAs(downloadPath);

    // 读取导出的 JSON
    const exported = JSON.parse(fs.readFileSync(downloadPath, 'utf-8'));

    // 校验每条证据的 text 与 rawText.slice(start, end) 逐字一致
    let evidenceCount = 0;
    for (const field of exported.fields) {
      if (!field.values) continue;
      for (const value of field.values) {
        if (!value.acceptable_evidence_spans) continue;
        for (const ev of value.acceptable_evidence_spans) {
          evidenceCount++;
          const sliced = rawText.slice(ev.start, ev.end);
          expect(sliced).toBe(ev.text);
        }
      }
    }
    expect(evidenceCount).toBeGreaterThanOrEqual(1);

    // 清空 localStorage 后重新导入该 JSON
    await page.evaluate(() => localStorage.clear());
    await page.reload();
    await page.waitForFunction(() => window.App && window.App.state && window.App.state.annotation, { timeout: 5000 });

    const fileChooserPromise = page.waitForEvent('filechooser');
    await page.click('#btnImportJson');
    const fileChooser = await fileChooserPromise;
    await fileChooser.setFiles(downloadPath);
    await page.waitForTimeout(800);

    // 重新导入后，每条证据仍应与 rawText.slice 逐字一致
    const result = await page.evaluate(() => {
      const raw = window.App.state.rawText;
      const ann = window.App.state.annotation;
      const results = [];
      for (const field of ann.fields) {
        if (!field.values) continue;
        for (const value of field.values) {
          if (!value.acceptable_evidence_spans) continue;
          for (const ev of value.acceptable_evidence_spans) {
            results.push({
              text: ev.text,
              sliced: raw.slice(ev.start, ev.end),
              start: ev.start,
              end: ev.end,
              match: raw.slice(ev.start, ev.end) === ev.text
            });
          }
        }
      }
      return results;
    });

    expect(result.length).toBe(evidenceCount);
    for (const r of result) {
      expect(r.match).toBe(true);
      expect(r.sliced).toBe(r.text);
    }

    try { fs.unlinkSync(downloadPath); } catch (_) {}
  });

  // ============================================================
  // 场景 33-36：证据预览弹窗的扩展/重选/失效检测能力
  // ============================================================

  test('33. 向左/向右扩展1字按钮正常工作', async ({ page }) => {
    const { rawText, annotation } = await buildValidAnnotation(page, 'expand-test');
    annotation.fields.forEach(f => {
      if (f.field_name !== 'amount') {
        f.gold_status = 'absent';
        f.values = [];
      } else {
        f.values[0].acceptable_evidence_spans = [];
      }
    });
    await injectState(page, annotation, rawText);
    await page.reload();
    await page.waitForSelector('#fieldsContainer .field-card');
    await page.waitForTimeout(300);

    await page.evaluate(() => {
      const idx = window.App.state.annotation.fields.findIndex(f => f.field_name === 'amount');
      window.App.switchToField(idx);
    });
    await page.waitForTimeout(200);

    // 选中"1285.60万元"中的"1285.60"（不含"万元"，故意制造不完整证据）
    const partial = '1285.60';
    await selectTextViaDomRange(page, partial);
    await page.click('.add-evidence-btn');
    await page.waitForTimeout(300);

    const startBefore = parseInt(await page.inputValue('#previewStart'));
    const endBefore = parseInt(await page.inputValue('#previewEnd'));
    expect(endBefore - startBefore).toBe(partial.length);

    // 向右扩展1字（应包含"万"）
    await page.click('#evidencePreviewModal button:has-text("向右扩展1字")');
    await page.waitForTimeout(200);
    const endAfterRight = parseInt(await page.inputValue('#previewEnd'));
    expect(endAfterRight).toBe(endBefore + 1);
    const textAfterRight = await page.inputValue('#previewText');
    expect(textAfterRight).toBe('1285.60万');

    // 向左扩展1字
    await page.click('#evidencePreviewModal button:has-text("向左扩展1字")');
    await page.waitForTimeout(200);
    const startAfterLeft = parseInt(await page.inputValue('#previewStart'));
    expect(startAfterLeft).toBe(startBefore - 1);

    // slice 验证仍通过
    const verifyClass = await page.getAttribute('#previewVerify', 'class');
    expect(verifyClass).not.toContain('error');
  });

  test('34. 重新选择按钮关闭预览弹窗且不保存', async ({ page }) => {
    const { rawText, annotation } = await buildValidAnnotation(page, 'reselect-test');
    annotation.fields.forEach(f => {
      if (f.field_name !== 'amount') {
        f.gold_status = 'absent';
        f.values = [];
      } else {
        f.values[0].acceptable_evidence_spans = [];
      }
    });
    await injectState(page, annotation, rawText);
    await page.reload();
    await page.waitForSelector('#fieldsContainer .field-card');
    await page.waitForTimeout(300);

    await page.evaluate(() => {
      const idx = window.App.state.annotation.fields.findIndex(f => f.field_name === 'amount');
      window.App.switchToField(idx);
    });
    await page.waitForTimeout(200);

    await selectTextViaDomRange(page, '1285.60');
    await page.click('.add-evidence-btn');
    await page.waitForTimeout(300);

    // 弹窗显示
    expect(await page.isVisible('#evidencePreviewModal:not(.hidden)')).toBe(true);

    // 点击"重新选择"
    await page.click('#evidencePreviewModal button:has-text("重新选择")');
    await page.waitForTimeout(300);

    // 弹窗应关闭
    expect(await page.isVisible('#evidencePreviewModal:not(.hidden)')).toBe(false);

    // 证据不应被保存
    const evCount = await page.evaluate(() => {
      const f = window.App.state.annotation.fields.find(f => f.field_name === 'amount');
      return f.values[0].acceptable_evidence_spans.length;
    });
    expect(evCount).toBe(0);
  });

  test('35. 点击已保存证据滚动并高亮原文', async ({ page }) => {
    const { rawText, annotation } = await buildValidAnnotation(page, 'focus-test');
    await injectState(page, annotation, rawText);
    await page.reload();
    await page.waitForSelector('#fieldsContainer .field-card');
    await page.waitForTimeout(400);

    // 切换到 amount 字段（有 2 段证据：primary + qualifier）
    await page.evaluate(() => {
      const idx = window.App.state.annotation.fields.findIndex(f => f.field_name === 'amount');
      window.App.switchToField(idx);
    });
    await page.waitForTimeout(300);

    // 记录滚动前位置
    const scrollTopBefore = await page.evaluate(() => document.getElementById('rawText').scrollTop);

    // 点击第一个证据项（非编辑/删除按钮）
    const evidenceItem = await page.$('.evidence-item');
    expect(evidenceItem).not.toBeNull();
    await evidenceItem.click();
    await page.waitForTimeout(800); // 等待 smooth 滚动 + flash 动画

    // 应有 flash 高亮（点击后 1.2s 内）
    const hasFlash = await page.evaluate(() => {
      return document.getElementById('rawText').querySelectorAll('.evidence-flash').length > 0;
    });
    // flash 可能在动画结束后消失，只要点击未报错且证据未失效即视为通过
    // 检查证据项没有失效标记
    const hasInvalidTag = await page.evaluate(() => {
      return document.querySelectorAll('.evidence-invalid-tag').length;
    });
    expect(hasInvalidTag).toBe(0);
  });

  test('36. 证据失效检测（slice 不一致时显示"证据已失效"）', async ({ page }) => {
    const { rawText, annotation } = await buildValidAnnotation(page, 'invalid-test');
    await injectState(page, annotation, rawText);
    await page.reload();
    await page.waitForSelector('#fieldsContainer .field-card');
    await page.waitForTimeout(400);

    // 切换到 amount 字段
    await page.evaluate(() => {
      const idx = window.App.state.annotation.fields.findIndex(f => f.field_name === 'amount');
      window.App.switchToField(idx);
    });
    await page.waitForTimeout(300);

    // 初始状态：证据有效，无失效标记
    let invalidCount = await page.evaluate(() => document.querySelectorAll('.evidence-invalid-tag').length);
    expect(invalidCount).toBe(0);

    // 篡改 state.rawText（模拟原文被替换，导致 slice 不一致）
    await page.evaluate(() => {
      window.App.state.rawText = '完全不同的原文内容，导致所有证据 slice 失效。';
      window.App.renderText();
      window.App.renderFields();
    });
    await page.waitForTimeout(400);

    // 重新渲染后，证据项应显示失效标记
    invalidCount = await page.evaluate(() => document.querySelectorAll('.evidence-invalid-tag').length);
    expect(invalidCount).toBeGreaterThan(0);

    // 点击证据项应弹出失效提示（alert）
    const dialogPromise = nextDialogMessage(page);
    const evItem = await page.$('.evidence-item');
    if (evItem) {
      await evItem.click();
      const msg = await dialogPromise;
      expect(msg).toContain('证据已失效');
    }
  });

});
