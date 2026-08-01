"""招标信息查询页 HTML（S-4 拆分自 app/api/ui.py）。"""

TENDERS_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>招标信息查询 · 标小智</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, "Segoe UI", "PingFang SC", sans-serif;
       background: #f5f5f5; padding: 20px; color: #333; }
.container { max-width: 1000px; margin: 0 auto; }
.header { background: white; padding: 24px; border-radius: 12px; margin-bottom: 20px;
          box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
h1 { color: #1a1a2e; margin-bottom: 8px; }
.back-link { color: #1976d2; text-decoration: none; font-size: 14px; }
.card { background: white; padding: 24px; border-radius: 12px; margin-bottom: 20px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
.form-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 12px; margin-bottom: 12px; }
.form-group label { display: block; font-size: 12px; color: #666; margin-bottom: 4px; }
.form-group input, .form-group select { width: 100%; padding: 8px; border: 1px solid #ddd;
                                        border-radius: 4px; font-size: 13px; }
.btn { padding: 10px 24px; background: #1976d2; color: white; border: none;
       border-radius: 6px; cursor: pointer; }
.btn:hover { background: #1565c0; }
table { width: 100%; border-collapse: collapse; margin-top: 16px; }
th, td { padding: 10px; text-align: left; border-bottom: 1px solid #eee; font-size: 13px; }
th { background: #f8f9ff; color: #1a1a2e; font-weight: 600; }
.stat-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-top: 16px; }
.stat { background: #f8f9ff; padding: 16px; border-radius: 8px; text-align: center; }
.stat-num { font-size: 24px; font-weight: bold; color: #1976d2; }
.stat-label { font-size: 12px; color: #666; margin-top: 4px; }

/* ===== 标小智品牌头 ===== */
.brand{display:flex;align-items:center;gap:8px;font-weight:700;font-size:15px;color:#1a1a2e}
.brand-logo{width:26px;height:26px;background:linear-gradient(135deg,#1976d2,#1565c0);border-radius:6px;display:flex;align-items:center;justify-content:center;color:white;font-size:13px;font-weight:800}
</style>
<link rel="stylesheet" href="/static/vendor/phosphor/phosphor-icons.min.css" />
</head>
<body>
<div class="container">
  <div class="header">
    <a href="/ui" class="back-link">← 返回首页</a>
    <div class="brand"><div class="brand-logo">标</div><span>标小智</span></div>
    <h1>招标信息查询</h1>
    <p style="color:#666; font-size:14px;">多维度过滤查询招标信息</p>
  </div>

  <div class="card">
    <h2>统计概览</h2>
    <button class="btn" onclick="loadStats()">加载统计</button>
    <div id="statsContainer" class="stat-grid" style="display:none;"></div>
  </div>

  <div class="card">
    <h2>查询条件</h2>
    <div class="form-row">
      <div class="form-group">
        <label>API Key</label>
        <input type="password" id="apiKey" placeholder="sk_xxx">
      </div>
      <div class="form-group">
        <label>平台</label>
        <select id="platform">
          <option value="">全部</option>
          <option value="ccgp">ccgp</option>
          <option value="chinabidding">chinabidding</option>
          <option value="ggzy">ggzy</option>
        </select>
      </div>
      <div class="form-group">
        <label>地区</label>
        <input type="text" id="region" placeholder="如：上海">
      </div>
      <div class="form-group">
        <label>主题关键词</label>
        <input type="text" id="topic" placeholder="如：充电桩">
      </div>
      <div class="form-group">
        <label>公告类型</label>
        <select id="noticeType">
          <option value="">全部</option>
          <option value="招标公告">招标公告</option>
          <option value="中标公告">中标公告</option>
        </select>
      </div>
      <div class="form-group">
        <label>每页数量</label>
        <input type="number" id="limit" value="20" min="1" max="200">
      </div>
    </div>
    <button class="btn" onclick="searchTenders()">查询</button>
  </div>

  <div class="card">
    <h2>查询结果</h2>
    <div id="tendersTable"></div>
  </div>
</div>

<script>
async function loadStats() {
  const apiKey = document.getElementById('apiKey').value;
  if (!apiKey) { alert('请先填写 API Key'); return; }
  try {
    const resp = await fetch('/api/tenders/stats/overview', {
      headers: { 'Authorization': 'Bearer ' + apiKey }
    });
    const data = await resp.json();
    if (data.code !== 200) { alert('失败：' + data.detail); return; }
    const s = data.data;
    document.getElementById('statsContainer').style.display = 'grid';
    document.getElementById('statsContainer').innerHTML = `
      <div class="stat"><div class="stat-num">${s.total}</div><div class="stat-label">总数</div></div>
      <div class="stat"><div class="stat-num">${(s.total_budget/10000).toFixed(2)}万</div><div class="stat-label">预算总额</div></div>
      <div class="stat"><div class="stat-num">${Object.keys(s.by_platform||{}).length}</div><div class="stat-label">平台数</div></div>
      <div class="stat"><div class="stat-num">${Object.keys(s.by_notice_type||{}).length}</div><div class="stat-label">公告类型</div></div>
    `;
  } catch (e) { alert('请求失败：' + e.message); }
}

async function searchTenders() {
  const apiKey = document.getElementById('apiKey').value;
  if (!apiKey) { alert('请先填写 API Key'); return; }
  const params = new URLSearchParams();
  const platform = document.getElementById('platform').value;
  const region = document.getElementById('region').value;
  const topic = document.getElementById('topic').value;
  const noticeType = document.getElementById('noticeType').value;
  const limit = document.getElementById('limit').value;
  if (platform) params.append('platform', platform);
  if (region) params.append('region', region);
  if (topic) params.append('topic', topic);
  if (noticeType) params.append('notice_type', noticeType);
  params.append('limit', limit);

  try {
    const resp = await fetch('/api/tenders?' + params.toString(), {
      headers: { 'Authorization': 'Bearer ' + apiKey }
    });
    const data = await resp.json();
    if (data.code !== 200) {
      document.getElementById('tendersTable').innerHTML = '<span style="color:#c62828;">查询失败：' + (data.detail || data.msg) + '</span>';
      return;
    }
    const items = data.data.items || [];
    const total = data.data.total;
    if (items.length === 0) {
      document.getElementById('tendersTable').innerHTML = '<span style="color:#666;">暂无数据</span>';
      return;
    }
    document.getElementById('tendersTable').innerHTML = `
      <div style="color:#666; font-size:13px; margin-bottom:8px;">共 ${total} 条，显示 ${items.length} 条</div>
      <table>
        <thead><tr>
          <th>标题</th><th>地区</th><th>发布时间</th><th>平台</th><th>预算</th><th>链接</th>
        </tr></thead>
        <tbody>
          ${items.map(t => `
            <tr>
              <td>${t.project_name || '-'}</td>
              <td>${t.location || '-'}</td>
              <td>${(t.publish_time||'-').substring(0,10)}</td>
              <td>${t.source_platform || '-'}</td>
              <td>${t.budget_amount ? (t.budget_amount/10000).toFixed(2)+'万' : '-'}</td>
              <td><a href="${t.source_url||'#'}" target="_blank" style="color:#1976d2;">查看</a></td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;
  } catch (e) {
    document.getElementById('tendersTable').innerHTML = '<span style="color:#c62828;">请求失败：' + e.message + '</span>';
  }
}
</script>
<script>
</script>
</body>
</html>"""
