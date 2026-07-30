"""聊天 Demo 页 HTML（W2-06 6 Agent 协作 Demo）。

用于 Demo 视频展示：用户输入查询 → 展示 6 Agent 协作进度 → 输出 Word 报告下载链接。
约束：单文件 ≤ 300 行
"""

CHAT_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=1366, initial-scale=1">
<title>智能招标助手 · 6 Agent 协作 Demo</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,"Segoe UI","PingFang SC",sans-serif;
     background:#f0f2f5;color:#333;height:100vh;display:flex;flex-direction:column}
.header{background:linear-gradient(135deg,#1976d2 0%,#1565c0 100%);
        color:white;padding:16px 24px;display:flex;align-items:center;justify-content:space-between}
.header-left{display:flex;align-items:center;gap:12px}
.logo{width:36px;height:36px;background:white;border-radius:8px;
      display:flex;align-items:center;justify-content:center;font-size:18px;font-weight:bold;color:#1976d2}
.header h1{font-size:18px;font-weight:600}
.header .subtitle{font-size:12px;opacity:.85;margin-top:2px}
.back-link{color:rgba(255,255,255,.9);text-decoration:none;font-size:13px}
.main-container{flex:1;display:flex;overflow:hidden}
.chat-panel{flex:1;display:flex;flex-direction:column;background:white;
            border-right:1px solid #e8e8e8;min-width:0}
.chat-messages{flex:1;overflow-y:auto;padding:20px;display:flex;flex-direction:column;gap:16px}
.message{max-width:75%;display:flex;gap:10px}
.message.user{align-self:flex-end;flex-direction:row-reverse}
.avatar{width:36px;height:36px;border-radius:50%;flex-shrink:0;
        display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:600}
.message.user .avatar{background:#1976d2;color:white}
.message.bot .avatar{background:#e3f2fd;color:#1565c0}
.bubble{padding:12px 16px;border-radius:12px;font-size:14px;line-height:1.6;word-break:break-word}
.message.user .bubble{background:#1976d2;color:white;border-top-right-radius:4px}
.message.bot .bubble{background:#f5f7fa;color:#333;border-top-left-radius:4px}
.slots-card{background:#f8f9ff;border:1px solid #e0e6ff;border-radius:8px;padding:12px;margin-top:10px}
.slots-title{font-size:12px;color:#666;margin-bottom:8px;font-weight:600}
.slots-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}
.slot-item{display:flex;flex-direction:column;gap:2px}
.slot-label{font-size:11px;color:#999}
.slot-value{font-size:13px;color:#1a1a2e;font-weight:500}
.slot-empty{color:#bbb;font-style:italic}
.report-card{background:#e8f5e9;border:1px solid #a5d6a7;border-radius:8px;padding:14px;
             margin-top:10px;display:flex;align-items:center;gap:12px}
.report-icon{font-size:28px}
.report-info{flex:1}
.report-name{font-size:14px;font-weight:600;color:#2e7d32}
.report-size{font-size:12px;color:#666;margin-top:2px}
.report-btn{padding:8px 16px;background:#43a047;color:white;border:none;border-radius:6px;
            cursor:pointer;font-size:13px;text-decoration:none;display:inline-block}
.report-btn:hover{background:#388e3c}
.chat-input-area{padding:16px 20px;border-top:1px solid #e8e8e8;background:#fafafa}
.input-row{display:flex;gap:10px}
.input-row input{flex:1;padding:12px 16px;border:1px solid #ddd;border-radius:8px;
                 font-size:14px;outline:none;transition:border-color .2s}
.input-row input:focus{border-color:#1976d2}
.input-row button{padding:12px 24px;background:#1976d2;color:white;border:none;
                   border-radius:8px;cursor:pointer;font-size:14px;font-weight:500}
.input-row button:hover{background:#1565c0}
.input-row button:disabled{background:#bdbdbd;cursor:not-allowed}
.input-hint{font-size:11px;color:#999;margin-top:8px}
.input-hint span{color:#1976d2;cursor:pointer;margin-right:8px}
.agents-panel{width:340px;background:#fafbfc;display:flex;flex-direction:column;min-width:340px}
.panel-header{padding:16px 20px;border-bottom:1px solid #f0f0f0}
.panel-header h2{font-size:14px;color:#1a1a2e}
.panel-header .desc{font-size:12px;color:#999;margin-top:4px}
.agents-list{flex:1;overflow-y:auto;padding:12px}
.agent-card{background:white;border:1px solid #e8e8e8;border-radius:8px;
            padding:12px;margin-bottom:10px;transition:all .3s}
.agent-card.active{border-color:#1976d2;box-shadow:0 2px 8px rgba(25,118,210,.15)}
.agent-card.done{border-color:#a5d6a7;background:#f1f8e9}
.agent-header{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.agent-icon{width:32px;height:32px;border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:16px}
.agent-name{font-size:13px;font-weight:600;color:#1a1a2e;flex:1}
.agent-status{font-size:11px;padding:2px 8px;border-radius:10px}
.status-pending{background:#f5f5f5;color:#999}
.status-running{background:#e3f2fd;color:#1565c0}
.status-done{background:#e8f5e9;color:#2e7d32}
.agent-desc{font-size:11px;color:#999;margin-bottom:8px;line-height:1.4}
.progress-bar{height:4px;background:#f0f0f0;border-radius:2px;overflow:hidden}
.progress-fill{height:100%;background:linear-gradient(90deg,#1976d2,#42a5f5);border-radius:2px;width:0;transition:width .5s ease}
.agent-card.done .progress-fill{background:linear-gradient(90deg,#43a047,#66bb6a)}
.typing-dots{display:inline-flex;gap:4px}
.typing-dots span{width:6px;height:6px;background:#999;border-radius:50%;animation:bounce 1.4s infinite both}
.typing-dots span:nth-child(2){animation-delay:.2s}
.typing-dots span:nth-child(3){animation-delay:.4s}
@keyframes bounce{0%,80%,100%{transform:scale(.6);opacity:.5}40%{transform:scale(1);opacity:1}}
@media(max-width:1024px){.agents-panel{width:280px;min-width:280px}}
</style>
</head>
<body>
<div class="header">
  <div class="header-left">
    <div class="logo">B</div>
    <div><h1>BidAgent 智能招标助手</h1><div class="subtitle">6 Agent 协作 · 一键生成招标分析报告</div></div>
  </div>
  <a href="/ui" class="back-link">← 返回首页</a>
</div>
<div class="main-container">
  <section class="chat-panel">
    <div class="chat-messages" id="chatMessages">
      <div class="message bot">
        <div class="avatar">AI</div>
        <div class="bubble">
          👋 你好！我是 BidAgent 智能招标助手。<br><br>
          告诉我你想找什么样的招标项目，我会通过 6 个 Agent 协作帮你：<br>
          1️⃣ 理解意图 → 2️⃣ 搜集数据 → 3️⃣ 清洗处理 →<br>
          4️⃣ 质量校验 → 5️⃣ 报告生成 → 6️⃣ 推送交付<br><br>
          试试输入：<b>"找上海最近7天的IT采购项目"</b>
        </div>
      </div>
    </div>
    <div class="chat-input-area">
      <div class="input-row">
        <input type="text" id="msgInput" placeholder="输入你的需求，例如：找上海最近7天的IT采购项目"
               onkeydown="if(event.key==='Enter')sendMsg()">
        <button id="sendBtn" onclick="sendMsg()">发送</button>
      </div>
      <div class="input-hint">
        快捷示例：
        <span onclick="setInput('找上海最近7天的IT采购项目')">上海 IT 采购</span>
        <span onclick="setInput('北京教育系统的中标公告')">北京 教育 中标</span>
        <span onclick="setInput('广东省医疗设备招标')">广东 医疗设备</span>
      </div>
    </div>
  </section>
  <aside class="agents-panel">
    <div class="panel-header"><h2>Agent 协作进度</h2><div class="desc">6 个专业 Agent 流水线协作</div></div>
    <div class="agents-list" id="agentsList"></div>
  </aside>
</div>
<script>
const AGENTS=[
  {id:'intent',name:'意图理解 Agent',icon:'🎯',desc:'解析用户意图，抽取5槽位参数',status:'pending',progress:0},
  {id:'collector',name:'数据采集 Agent',icon:'🕷️',desc:'多平台爬取招标公告数据',status:'pending',progress:0},
  {id:'processor',name:'清洗抽取 Agent',icon:'⚙️',desc:'LLM 抽取6类核心字段',status:'pending',progress:0},
  {id:'quality',name:'质量校验 Agent',icon:'✅',desc:'证据定位 + 反幻觉校验',status:'pending',progress:0},
  {id:'report',name:'报告生成 Agent',icon:'📄',desc:'生成 Word 分析报告',status:'pending',progress:0},
  {id:'delivery',name:'交付推送 Agent',icon:'📧',desc:'邮件/订阅推送交付',status:'pending',progress:0},
];
let running=false;
function renderAgents(){
  document.getElementById('agentsList').innerHTML=AGENTS.map(a=>{
    const cc=a.status==='running'?' active':a.status==='done'?' done':'';
    const sl=a.status==='running'?'运行中':a.status==='done'?'已完成':'等待中';
    return `<div class="agent-card${cc}">
      <div class="agent-header">
        <div class="agent-icon">${a.icon}</div>
        <div class="agent-name">${a.name}</div>
        <span class="agent-status status-${a.status}">${sl}</span>
      </div>
      <div class="agent-desc">${a.desc}</div>
      <div class="progress-bar"><div class="progress-fill" style="width:${a.progress}%"></div></div>
    </div>`;
  }).join('');
}
function addMsg(role,content){
  const c=document.getElementById('chatMessages');
  const m=document.createElement('div');
  m.className=`message ${role}`;
  m.innerHTML=`<div class="avatar">${role==='user'?'我':'AI'}</div><div class="bubble">${content}</div>`;
  c.appendChild(m);c.scrollTop=c.scrollHeight;return m;
}
function setInput(t){document.getElementById('msgInput').value=t}
async function runSim(slots){
  AGENTS.forEach(a=>{a.status='pending';a.progress=0});renderAgents();
  for(let i=0;i<AGENTS.length;i++){
    AGENTS[i].status='running';renderAgents();
    for(let p=0;p<=100;p+=20){
      await new Promise(r=>setTimeout(r,150+Math.random()*150));
      AGENTS[i].progress=p;renderAgents();
    }
    AGENTS[i].progress=100;AGENTS[i].status='done';renderAgents();
    await new Promise(r=>setTimeout(r,300));
  }
}
async function sendMsg(){
  if(running)return;
  const input=document.getElementById('msgInput');
  const btn=document.getElementById('sendBtn');
  const text=input.value.trim();if(!text)return;
  running=true;btn.disabled=true;
  addMsg('user',text);input.value='';
  const botMsg=addMsg('bot','<div class="typing-dots"><span></span><span></span><span></span></div>');
  await new Promise(r=>setTimeout(r,600));
  const slots=parseSlots(text);
  botMsg.querySelector('.bubble').innerHTML=
    `✅ 已解析你的需求，5 槽位参数如下：${renderSlots(slots)}<br>`+
    `正在启动 6 个 Agent 协作处理，请稍候...`;
  await runSim(slots);
  const rn=`招标分析报告_${slots.keyword||'自定义'}_${new Date().toISOString().slice(0,10)}.docx`;
  botMsg.querySelector('.bubble').innerHTML=
    `🎉 处理完成！共找到 <b>${Math.floor(Math.random()*30)+10}</b> 条相关招标公告。`+
    `<div class="report-card">
      <div class="report-icon">📄</div>
      <div class="report-info">
        <div class="report-name">${rn}</div>
        <div class="report-size">Word 文档 · ${(Math.random()*2+.5).toFixed(1)} MB · 包含项目列表+趋势分析+风险提示</div>
      </div>
      <a href="#" class="report-btn" onclick="alert('Demo 模式，下载链接待接入真实后端');return false;">下载报告</a>
    </div>`;
  running=false;btn.disabled=false;input.focus();
}
function parseSlots(text){
  const s={keyword:'',region:'',time_range:'',industry:'',notice_type:''};
  if(text.includes('上海'))s.region='上海市';
  else if(text.includes('北京'))s.region='北京市';
  else if(text.includes('广东')||text.includes('广州')||text.includes('深圳'))s.region='广东省';
  else if(text.includes('浙江')||text.includes('杭州'))s.region='浙江省';
  else s.region='全国';
  if(text.includes('7天')||text.includes('一周'))s.time_range='最近 7 天';
  else if(text.includes('30天')||text.includes('一个月'))s.time_range='最近 30 天';
  else if(text.includes('今天')||text.includes('今日'))s.time_range='今日';
  else s.time_range='最近 15 天';
  if(text.includes('IT')||text.includes('信息化')||text.includes('软件')){s.keyword='IT / 信息化';s.industry='信息技术'}
  else if(text.includes('医疗')||text.includes('医院')||text.includes('设备')){s.keyword='医疗设备';s.industry='医疗卫生'}
  else if(text.includes('教育')||text.includes('学校')||text.includes('大学')){s.keyword='教育系统';s.industry='教育'}
  else if(text.includes('基建')||text.includes('工程')||text.includes('建筑')){s.keyword='基建工程';s.industry='建筑工程'}
  else{s.keyword=text.slice(0,20);s.industry='综合'}
  if(text.includes('中标')||text.includes('成交'))s.notice_type='中标公告';
  else if(text.includes('更正'))s.notice_type='更正公告';
  else s.notice_type='招标公告';
  return s;
}
function renderSlots(s){
  return `<div class="slots-card"><div class="slots-title">📋 5 槽位解析结果</div>
    <div class="slots-grid">
      <div class="slot-item"><span class="slot-label">关键词</span><span class="slot-value">${s.keyword||'<span class="slot-empty">未指定</span>'}</span></div>
      <div class="slot-item"><span class="slot-label">地区</span><span class="slot-value">${s.region||'<span class="slot-empty">未指定</span>'}</span></div>
      <div class="slot-item"><span class="slot-label">时间范围</span><span class="slot-value">${s.time_range||'<span class="slot-empty">未指定</span>'}</span></div>
      <div class="slot-item"><span class="slot-label">行业</span><span class="slot-value">${s.industry||'<span class="slot-empty">未指定</span>'}</span></div>
      <div class="slot-item" style="grid-column:span 2;"><span class="slot-label">公告类型</span><span class="slot-value">${s.notice_type||'<span class="slot-empty">未指定</span>'}</span></div>
    </div></div>`;
}
renderAgents();document.getElementById('msgInput').focus();
</script>
</body>
</html>"""
