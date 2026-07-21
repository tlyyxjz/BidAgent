// 临时脚本：使用 Playwright 运行 test.html 单元测试
const { chromium } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto('http://localhost:8765/test.html');

  // 等待测试完成（#summary 不再是"运行中..."）
  await page.waitForFunction(() => {
    const el = document.getElementById('summary');
    return el && !el.textContent.includes('运行中');
  }, { timeout: 10000 });

  const summary = await page.textContent('#summary');
  const totalCount = await page.textContent('#totalCount');
  const passCount = await page.textContent('#passCount');
  const failCount = await page.textContent('#failCount');

  console.log('=== 浏览器单元测试结果 ===');
  console.log('总结:', summary);
  console.log('总计:', totalCount, '通过:', passCount, '失败:', failCount);

  // 提取失败的测试详情
  const failures = await page.$$eval('.test-case.fail', els =>
    els.map(e => ({
      name: e.querySelector('.test-name')?.textContent || '',
      error: e.querySelector('.test-error')?.textContent || ''
    }))
  );
  if (failures.length > 0) {
    console.log('\n失败用例:');
    failures.forEach(f => {
      console.log('  ✗', f.name);
      console.log('    ', f.error);
    });
  }

  await browser.close();
  process.exit(failCount !== '0' && parseInt(failCount) > 0 ? 1 : 0);
})();
