"""订阅管理页 HTML（S-4 拆分自 app/api/ui.py）。"""

SUBSCRIPTIONS_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>订阅管理 · 标小智</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, "Segoe UI", "PingFang SC", sans-serif;
       background: #f5f5f5; padding: 20px; color: #333; }
.container { max-width: 900px; margin: 0 auto; }
.header { background: white; padding: 24px; border-radius: 12px; margin-bottom: 20px;
          box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
h1 { color: #1a1a2e; margin-bottom: 8px; }
.back-link { color: #1976d2; text-decoration: none; font-size: 14px; }
.card { background: white; padding: 24px; border-radius: 12px; margin-bottom: 20px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
.form-group { margin-bottom: 16px; }
label { display: block; margin-bottom: 6px; font-weight: 500; color: #1a1a2e; }
input, textarea, select { width: 100%; padding: 10px; border: 1px solid #ddd;
                          border-radius: 6px; font-size: 14px; }
textarea { min-height: 80px; }
.btn { padding: 10px 24px; background: #1976d2; color: white; border: none;
       border-radius: 6px; cursor: pointer; font-size: 14px; }
.btn:hover { background: #1565c0; }
.example { background: #f8f9ff; padding: 12px; border-radius: 6px; margin-top: 8px;
           font-size: 13px; color: #666; }
.example b { color: #1a1a2e; }
.api-key-input { background: #fff3cd; }
.status-active { color: #2e7d32; }
.status-inactive { color: #c62828; }
#result { margin-top: 16px; padding: 12px; border-radius: 6px; display: none; }

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
    <h1>订阅管理</h1>
    <p style="color:#666; font-size:14px;">创建订阅 · 触发推送 · 查看增量推送结果</p>
  </div>

  <div class="card">
    <h2>创建新订阅</h2>
    <div class="form-group">
      <label>API Key（Bearer Token）</label>
      <input type="password" id="apiKey" class="api-key-input" placeholder="sk_xxx">
    </div>
    <div class="form-group">
      <label>自然语言查询</label>
      <textarea id="query" placeholder="例如：最近3个月的上海区域内的充电桩招标信息都有哪些，请汇总后每天9:00发送给我"></textarea>
      <div class="example">
        <b>命题示例：</b><br>
        1. 最近1个月的安徽省区域内的服务器招标信息都有哪些<br>
        2. 2026年3月份的上海区域内的充电桩招标信息都有哪些<br>
        3. 最近3个月的上海区域内的充电桩招标信息都有哪些，请汇总后每天9:00发送给我<br>
        4. 2026年4月份上海的充电桩招标信息都有哪些，请汇总后今天9:00发送给我
      </div>
    </div>
    <button class="btn" onclick="createSubscription()">创建订阅</button>
    <div id="result"></div>
  </div>

  <div class="card">
    <h2>我的订阅</h2>
    <button class="btn" onclick="listSubscriptions()">刷新订阅列表</button>
    <div id="subscriptionsList" style="margin-top: 16px;"></div>
  </div>
</div>

<script>
const API_BASE = '';

async function createSubscription() {
  const apiKey = document.getElementById('apiKey').value;
  const query = document.getElementById('query').value;
  const result = document.getElementById('result');

  if (!apiKey || !query) {
    result.style.display = 'block';
    result.style.background = '#ffebee';
    result.innerHTML = '请填写 API Key 和查询';
    return;
  }

  try {
    const resp = await fetch(API_BASE + '/api/subscriptions', {
      method: 'POST',
      headers: {
        'Authorization': 'Bearer ' + apiKey,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        raw_query: query,
        platforms: ['ccgp'],
        push_channels: ['email']
      })
    });
    const data = await resp.json();
    result.style.display = 'block';
    if (data.code === 201) {
      result.style.background = '#e8f5e9';
      result.innerHTML = '<b>订阅创建成功！</b><br>订阅 ID：' + data.data.subscription_id + '<br>请到下方点击"触发推送"';
    } else {
      result.style.background = '#ffebee';
      result.innerHTML = '<b>创建失败：</b>' + (data.detail || data.msg || JSON.stringify(data));
    }
  } catch (e) {
    result.style.display = 'block';
    result.style.background = '#ffebee';
    result.innerHTML = '<b>请求失败：</b>' + e.message;
  }
}

async function listSubscriptions() {
  const apiKey = document.getElementById('apiKey').value;
  const listDiv = document.getElementById('subscriptionsList');
  if (!apiKey) {
    listDiv.innerHTML = '<span style="color:#c62828;">请先填写 API Key</span>';
    return;
  }
  try {
    const resp = await fetch(API_BASE + '/api/subscriptions?limit=20', {
      headers: { 'Authorization': 'Bearer ' + apiKey }
    });
    const data = await resp.json();
    if (data.code !== 200) {
      listDiv.innerHTML = '<span style="color:#c62828;">加载失败：' + (data.detail || data.msg) + '</span>';
      return;
    }
    const subs = data.data || [];
    if (subs.length === 0) {
      listDiv.innerHTML = '<span style="color:#666;">暂无订阅</span>';
      return;
    }
    listDiv.innerHTML = subs.map(s => `
      <div style="padding:12px; border:1px solid #eee; border-radius:6px; margin-bottom:8px;">
        <div><b>订阅 #${s.id}</b> ${s.is_active ? '<span class="status-active">● 活跃</span>' : '<span class="status-inactive">● 已取消</span>'}</div>
        <div style="color:#666; font-size:13px; margin-top:4px;">查询：${s.raw_query}</div>
        <div style="color:#666; font-size:13px;">触发类型：${s.trigger_type} | 频率：${s.frequency_cron || '立即'}</div>
        <div style="margin-top:8px;">
          <button class="btn" style="padding:4px 12px; font-size:12px;" onclick="triggerSub(${s.id})">触发推送</button>
          <button class="btn" style="padding:4px 12px; font-size:12px; background:#666;" onclick="viewTenders(${s.id})">查看招标信息</button>
        </div>
        <div id="sub-result-${s.id}" style="margin-top:8px;"></div>
      </div>
    `).join('');
  } catch (e) {
    listDiv.innerHTML = '<span style="color:#c62828;">请求失败：' + e.message + '</span>';
  }
}

async function triggerSub(subId) {
  const apiKey = document.getElementById('apiKey').value;
  const resultDiv = document.getElementById('sub-result-' + subId);
  resultDiv.innerHTML = '<span style="color:#666;">触发中...</span>';
  try {
    const resp = await fetch(API_BASE + '/api/subscriptions/' + subId + '/trigger', {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + apiKey }
    });
    const data = await resp.json();
    if (data.code === 200) {
      const r = data.data;
      resultDiv.innerHTML = `<div style="background:#e8f5e9; padding:8px; border-radius:4px;">
        <b>推送结果：</b>${r.status}<br>
        推送数量：${r.count}<br>
        ${r.report_path ? '报告路径：' + r.report_path : ''}
      </div>`;
    } else {
      resultDiv.innerHTML = '<span style="color:#c62828;">失败：' + (data.detail || data.msg) + '</span>';
    }
  } catch (e) {
    resultDiv.innerHTML = '<span style="color:#c62828;">请求失败：' + e.message + '</span>';
  }
}

async function viewTenders(subId) {
  const apiKey = document.getElementById('apiKey').value;
  const resultDiv = document.getElementById('sub-result-' + subId);
  resultDiv.innerHTML = '<span style="color:#666;">加载中...</span>';
  try {
    const resp = await fetch(API_BASE + '/api/subscriptions/' + subId + '/tenders?only_unpushed=true&limit=10', {
      headers: { 'Authorization': 'Bearer ' + apiKey }
    });
    const data = await resp.json();
    if (data.code === 200) {
      const items = data.data || [];
      if (items.length === 0) {
        resultDiv.innerHTML = '<span style="color:#666;">暂无未推送的招标信息</span>';
      } else {
        resultDiv.innerHTML = items.map(t => `
          <div style="padding:8px; background:#f8f9ff; border-radius:4px; margin-top:4px;">
            <b>${t.project_name || '-'}</b><br>
            <span style="color:#666; font-size:12px;">${t.publish_time || '-'} | ${t.source_platform || '-'}</span><br>
            <a href="${t.source_url || '#'}" target="_blank" style="color:#1976d2; font-size:12px;">查看原文</a>
          </div>
        `).join('');
      }
    }
  } catch (e) {
    resultDiv.innerHTML = '<span style="color:#c62828;">请求失败：' + e.message + '</span>';
  }
}
</script>
<script>
</script>
</body>
</html>"""
