// Playwright 配置：BidAgent W1-05 标注工具端到端测试
const { defineConfig, devices } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './',
  testMatch: 'e2e.spec.js',
  fullyParallel: false, // 共享 localStorage，串行执行避免污染
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: [['list'], ['html', { open: 'never' }]],
  timeout: 30000,
  expect: { timeout: 5000 },

  use: {
    baseURL: 'http://localhost:8765',
    actionTimeout: 5000,
    navigationTimeout: 10000,
    // 每个 test 都用全新 context，避免 localStorage 串扰
    // 但同 test 内多 page 共享需通过 context.storageState 手动管理
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  // 自动启动 Python HTTP 服务器托管 annotation_tool
  webServer: {
    command: 'python -m http.server 8765 --directory ..',
    url: 'http://localhost:8765/index.html',
    reuseExistingServer: !process.env.CI,
    timeout: 15000,
    cwd: __dirname,
  },
});
