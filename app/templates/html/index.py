"""Web UI 首页 HTML（S-4 拆分自 app/api/ui.py）。"""

INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>ScrapeFlow · 招投标信息聚合工具</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, "Segoe UI", "PingFang SC", sans-serif;
       background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
       min-height: 100vh; padding: 40px 20px; color: #333; }
.container { max-width: 960px; margin: 0 auto; }
.card { background: white; border-radius: 16px; padding: 32px; margin-bottom: 24px;
        box-shadow: 0 20px 60px rgba(0,0,0,0.15); }
h1 { color: #1a1a2e; font-size: 32px; margin-bottom: 8px; }
.subtitle { color: #666; font-size: 14px; margin-bottom: 24px; }
.badge { display: inline-block; padding: 4px 12px; border-radius: 12px;
         background: #e8f4fd; color: #1976d2; font-size: 12px; margin-right: 8px; }
.section-title { color: #1a1a2e; font-size: 20px; margin: 24px 0 16px; }
.feature-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
                gap: 16px; }
.feature { padding: 16px; background: #f8f9ff; border-radius: 8px; border-left: 4px solid #1976d2; }
.feature h4 { color: #1a1a2e; margin-bottom: 8px; }
.feature p { color: #666; font-size: 13px; }
.btn { display: inline-block; padding: 10px 24px; background: #1976d2; color: white;
       text-decoration: none; border-radius: 8px; font-weight: 500; margin-right: 8px; }
.btn:hover { background: #1565c0; }
.btn-secondary { background: #666; }
.btn-secondary:hover { background: #444; }
.coverage { width: 100%; border-collapse: collapse; margin-top: 16px; }
.coverage th, .coverage td { padding: 12px; text-align: left; border-bottom: 1px solid #eee; }
.coverage th { background: #f8f9ff; color: #1a1a2e; font-weight: 600; }
.status-yes { color: #2e7d32; font-weight: bold; }
.status-partial { color: #ed6c02; }
.footer { text-align: center; color: white; margin-top: 32px; font-size: 13px; }
</style>
</head>
<body>
<div class="container">
  <div class="card">
    <div>
      <span class="badge">2026 AI 先锋未来人才大赛</span>
      <span class="badge">超聚变命题</span>
      <span class="badge">队伍：智汇标讯</span>
    </div>
    <h1>ScrapeFlow · 招投标信息聚合工具</h1>
    <p class="subtitle">自然语言驱动 · 多源聚合 · 增量推送 · Word 报告</p>

    <div style="margin: 24px 0;">
      <a href="/ui/subscriptions" class="btn">订阅管理</a>
      <a href="/ui/tenders" class="btn btn-secondary">招标信息查询</a>
      <a href="/docs" class="btn btn-secondary">API 文档</a>
    </div>
  </div>

  <div class="card">
    <h2 class="section-title">命题硬要求覆盖度</h2>
    <table class="coverage">
      <thead>
        <tr><th>#</th><th>硬要求</th><th>实现位置</th><th>状态</th></tr>
      </thead>
      <tbody>
        <tr><td>1</td><td>意图解析（5 槽位）</td><td>app/llm/parser.py</td><td class="status-yes">✓ 完成</td></tr>
        <tr><td>2</td><td>信息来源 ≥2 网站，≥1 登录</td><td>app/templates/ccgp/chinabidding/ggzy</td><td class="status-partial">⚠ 骨架完成</td></tr>
        <tr><td>3</td><td>内容清洗去重</td><td>app/processors/</td><td class="status-partial">⚠ SimHash 待补</td></tr>
        <tr><td>4</td><td>5 字段汇总</td><td>app/report/docx_components.py</td><td class="status-yes">✓ 完成</td></tr>
        <tr><td>5</td><td>定时执行</td><td>app/scheduler/subscription.py</td><td class="status-yes">✓ 完成</td></tr>
        <tr><td>6</td><td>增量推送</td><td>PushLog 表 + scheduler</td><td class="status-yes">✓ 完成</td></tr>
        <tr><td>交付</td><td>Word 命名规则</td><td>docx_generator.build_filename</td><td class="status-yes">✓ 完成</td></tr>
        <tr><td>交付</td><td>附件链接处理</td><td>attachment_downloader.py</td><td class="status-yes">✓ 完成</td></tr>
      </tbody>
    </table>
  </div>

  <div class="card">
    <h2 class="section-title">核心特性</h2>
    <div class="feature-grid">
      <div class="feature">
        <h4>自然语言意图解析</h4>
        <p>支持命题 4 个示例：主题/地区/时间范围/频率/触发类型 5 个槽位</p>
      </div>
      <div class="feature">
        <h4>多源采集</h4>
        <p>ccgp / chinabidding / ggzy 三大招投标平台模板</p>
      </div>
      <div class="feature">
        <h4>增量推送</h4>
        <p>PushLog 表 + SimHash 去重，已推送内容不重复</p>
      </div>
      <div class="feature">
        <h4>Word 报告生成</h4>
        <p>命题命名规则 {用户问题}_{YYYYMMDDHHmm}.docx</p>
      </div>
      <div class="feature">
        <h4>附件下载</h4>
        <p>支持 PDF/DOC/XLS 等 9 种格式，按 tender_id 隔离</p>
      </div>
      <div class="feature">
        <h4>企业级架构</h4>
        <p>API Key 鉴权 + Admin 后台 + 速率限制 + 代理池</p>
      </div>
    </div>
  </div>

  <div class="footer">
    ScrapeFlow · 2026 AI 先锋未来人才大赛 · 智汇标讯 · 超聚变命题
  </div>
</div>
</body>
</html>"""
