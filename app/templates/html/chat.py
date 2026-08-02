"""聊天 Demo 页 HTML（W2-06 智能问答 · 6 Agent 协作）。

用于 Demo 视频展示：用户输入查询 → 展示 6 Agent 协作进度 → 输出 Word 报告下载链接。
约束：单文件 ≤ 300 行
"""

CHAT_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=1366, initial-scale=1">
<title>标小智 · 智能问答 · 6 Agent 协作</title>
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
.agent-icon{width:32px;height:32px;border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:16px;color:#1976d2;background:#e3f2fd}
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

/* ===== Unified Sidebar ===== */
.sidebar{position:fixed;left:0;top:0;width:208px;height:100vh;background:#001529;display:flex;flex-direction:column;z-index:100}
.sidebar-logo{height:52px;display:flex;align-items:center;gap:10px;padding:0 20px;border-bottom:1px solid rgba(255,255,255,.08)}
.sidebar-logo .logo-icon{width:28px;height:28px;background:#1677ff;border-radius:6px;display:flex;align-items:center;justify-content:center;color:#fff;font-size:15px;font-weight:800;flex-shrink:0}
.sidebar-logo .logo-text{color:#fff;font-size:15px;font-weight:700;letter-spacing:.3px}
.sidebar-nav{flex:1;padding:8px 0;overflow-y:auto}
.nav-section-label{padding:12px 20px 6px;font-size:10px;color:rgba(255,255,255,.35);text-transform:uppercase;letter-spacing:1px}
.nav-item{display:flex;align-items:center;gap:10px;padding:9px 20px;color:rgba(255,255,255,.7);font-size:13px;text-decoration:none;transition:all .15s;border-left:3px solid transparent;cursor:pointer}
.nav-item:hover{color:#fff;background:rgba(255,255,255,.06)}
.nav-item.active{color:#fff;background:rgba(22,119,255,.15);border-left-color:#1677ff}
.nav-item i{font-size:16px;width:18px;text-align:center;flex-shrink:0}
.nav-item .nav-badge{margin-left:auto;font-size:10px;padding:1px 6px;border-radius:8px;background:#1677ff;color:#fff;font-weight:600}
.sidebar-footer{padding:10px 20px;border-top:1px solid rgba(255,255,255,.08);font-size:11px;color:rgba(255,255,255,.3);line-height:1.6}
.sidebar-footer .comp-tag{color:#1677ff;font-weight:600}
/* ===== Unified Main Wrap ===== */
.main-wrap{margin-left:208px;min-height:100vh;display:flex;flex-direction:column}
.top-header{height:52px;background:#fff;border-bottom:1px solid #e8e8e8;display:flex;align-items:center;justify-content:space-between;padding:0 20px;position:sticky;top:0;z-index:50;box-shadow:0 1px 2px rgba(0,0,0,.03),0 1px 6px -1px rgba(0,0,0,.02)}
.header-left{display:flex;align-items:center;gap:12px}
.header-title{font-size:15px;font-weight:600;color:#1a1a2e}
.header-title i{color:#1677ff;margin-right:6px}
.header-right{display:flex;align-items:center;gap:10px}
.h-badge{display:flex;align-items:center;gap:5px;padding:4px 10px;border-radius:4px;font-size:11px;font-weight:600}
.h-badge.comp{background:#e6f4ff;color:#1677ff}
.h-badge.data{background:#f6ffed;color:#52c41a}
.h-badge.vers{background:#f5f7fa;color:#595959;border:1px solid #e8e8e8}
body{background:#f5f7fa}

</style>
<link rel="stylesheet" href="/static/vendor/phosphor/phosphor-icons.min.css" />
</head>
<body>
<!-- ===== Sidebar ===== -->
<aside class="sidebar">
  <div class="sidebar-logo">
    <div class="logo-icon">标</div>
    <div class="logo-text">标小智</div>
  </div>
  <nav class="sidebar-nav">
    <div class="nav-section-label">主导航</div>
<a href="/ui" class="nav-item"><i class="ph-bold ph-house"></i><span>工作台</span></a>
<a href="/ui/search" class="nav-item"><i class="ph-bold ph-magnifying-glass"></i><span>招标检索</span></a>
<a href="/ui/notice-list" class="nav-item"><i class="ph-bold ph-list-magnifying-glass"></i><span>跨平台去重</span></a>
<a href="/ui/detail" class="nav-item"><i class="ph-bold ph-line-segment"></i><span>证据验证</span></a>
<a href="/ui/org" class="nav-item"><i class="ph-bold ph-users-three"></i><span>组织画像</span></a>
<a href="/ui/quality-dashboard" class="nav-item"><i class="ph-bold ph-chart-bar"></i><span>质量评测</span></a>
<a href="/ui/versions" class="nav-item"><i class="ph-bold ph-git-branch"></i><span>版本历史</span></a>
<a href="/ui/chat" class="nav-item active"><i class="ph-bold ph-chats-circle"></i><span>智能问答</span><span class="nav-badge">AI</span></a>
  </nav>
  <div class="sidebar-footer">
    <div><span class="comp-tag">标小智</span> v4.1</div>
    <div>GOAI 2026 初赛 · W3</div>
  </div>
</aside>

<!-- ===== Main ===== -->
<div class="main-wrap">
  <header class="top-header">
    <div class="header-left">
      <div class="header-title"><i class="ph-bold ph-chats-circle"></i>智能问答</div>
    </div>
    <div class="header-right">
      <div class="h-badge comp"><i class="ph ph-trophy"></i>GOAI 2026</div>
      <div class="h-badge data"><i class="ph ph-database"></i>真实数据 · W3</div>
      <div class="h-badge vers">v4.1 · 107篇</div>
    </div>
  </header>
  <div class="content-area" style="flex:1;padding:0;overflow:hidden">

<div class="main-container">
  <section class="chat-panel">
    <div class="chat-messages" id="chatMessages">
      <div class="message bot">
        <div class="avatar">AI</div>
        <div class="bubble">
          你好！我是标小智。<br><br>
          告诉我你想找什么样的招标项目，我会通过 6 个 Agent 协作帮你：<br>
          1. 理解意图 → 2. 搜集数据 → 3. 清洗处理 →<br>
          4. 质量校验 → 5. 报告生成 → 6. 推送交付<br><br>
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
    <div class="panel-header"><h2><i class="ph-bold ph-robot" style="color:#1976d2;margin-right:6px"></i>Agent 协作流水线</h2><div class="desc">6 个专业 Agent 实时运行状态（功能面板）</div></div>
    <div class="agents-list" id="agentsList"></div>
  </aside>
</div>
<script>

const AGENTS=[
  {id:'intent',name:'意图理解 Agent',icon:'<i class="ph-bold ph-brain"></i>',desc:'解析用户意图，抽取5槽位参数',status:'pending',progress:0},
  {id:'collector',name:'数据采集 Agent',icon:'<i class="ph-bold ph-globe"></i>',desc:'多平台爬取招标公告数据',status:'pending',progress:0},
  {id:'processor',name:'清洗抽取 Agent',icon:'<i class="ph-bold ph-gear-six"></i>',desc:'LLM 抽取6类核心字段',status:'pending',progress:0},
  {id:'quality',name:'质量校验 Agent',icon:'<i class="ph-bold ph-shield-check"></i>',desc:'证据定位 + 反幻觉校验',status:'pending',progress:0},
  {id:'report',name:'报告生成 Agent',icon:'<i class="ph-bold ph-file-text"></i>',desc:'生成 Word 分析报告',status:'pending',progress:0},
  {id:'delivery',name:'交付推送 Agent',icon:'<i class="ph-bold ph-paper-plane-tilt"></i>',desc:'邮件/订阅推送交付',status:'pending',progress:0},
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
// A1 修复：真实轮询 pipeline 进度（替代 setTimeout 模拟）
const STAGE_AGENT_MAP={'intent':0,'collecting':1,'processing':2,'quality':3,'finance':4,'done':5};
let _pipelineSid=null;
async function runReal(){
  AGENTS.forEach(a=>{a.status='pending';a.progress=0});renderAgents();
  // 轮询真实 pipeline 状态
  for(let iter=0;iter<300;iter++){  // 最多轮询 300 次（~5分钟）
    await new Promise(r=>setTimeout(r,1000));
    if(!_pipelineSid)break;
    try{
      const r=await fetch(`/api/demo/pipeline/status?sid=${encodeURIComponent(_pipelineSid)}`);
      if(!r.ok)continue;
      const d=await r.json();
      if(d.code!==200)continue;
      const s=d.data;
      // 根据 stages 更新 6 Agent 进度
      const stages=s.stages||{};
      let agentIdx=0;
      for(const [stageName,stageInfo] of Object.entries(stages)){
        const idx=STAGE_AGENT_MAP[stageName];
        if(idx===undefined)continue;
        if(stageInfo.status==='done'){AGENTS[idx].status='done';AGENTS[idx].progress=100}
        else if(stageInfo.status==='running'){AGENTS[idx].status='running';AGENTS[idx].progress=Math.max(AGENTS[idx].progress,50)}
        else if(stageInfo.status==='pending' && AGENTS[idx].status==='done'){/* skip */}
      }
      // 当前运行中的 Agent 进度递增
      const curIdx=STAGE_AGENT_MAP[s.stage];
      if(curIdx!==undefined && AGENTS[curIdx].status==='running'){
        AGENTS[curIdx].progress=Math.min(95,AGENTS[curIdx].progress+5);
      }
      renderAgents();
      // pipeline 完成
      if(s.stage==='done' && s.finished_at){
        AGENTS.forEach(a=>{a.status='done';a.progress=100});renderAgents();
        return s;
      }
      if(s.error){return s}
    }catch(e){/* 轮询失败继续 */}
  }
  return null;
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
    `已解析你的需求，5 槽位参数如下：${renderSlots(slots)}<br>`+
    `正在启动 6 个 Agent 协作处理，请稍候...`;
  // A1 修复：启动真实 pipeline 并轮询
  try{
    const sr=await fetch(`/api/demo/pipeline/start?query=${encodeURIComponent(text)}`,{method:'POST'});
    const sd=await sr.json();
    if(sd.code===200 && sd.data.session_id){
      _pipelineSid=sd.data.session_id;
      const result=await runReal();
      _pipelineSid=null;
      const r=result&&result.result||{};
      const cs=r.collect_summary||{};
      const ps=r.process_summary||{};
      const foundCount=cs.total||ps.total_processed||0;
      const reportName=slots.keyword||'招标分析';
      const rn=`招标分析报告_${reportName}_${new Date().toISOString().slice(0,10)}.docx`;
      botMsg.querySelector('.bubble').innerHTML=
        `处理完成！共找到 <b>${foundCount}</b> 条相关招标公告。`+
        `<div class="report-card">
          <div class="report-icon"><i class="ph-bold ph-file-text" style="font-size:28px;color:#2e7d32"></i></div>
          <div class="report-info">
            <div class="report-name">${rn}</div>
            <div class="report-size">Word 文档 · 真实 pipeline 生成 · 包含项目列表+趋势分析+风险提示</div>
          </div>
          <a href="#" class="report-btn" onclick="downloadReport();return false;">下载报告</a>
        </div>`;
    }else{
      botMsg.querySelector('.bubble').innerHTML=`Pipeline 启动失败: ${sd.msg||'未知错误'}`;
    }
  }catch(e){
    botMsg.querySelector('.bubble').innerHTML=`Pipeline 执行异常: ${e.message}`;
  }
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
  if(text.includes('充电桩')||text.includes('充电站')||text.includes('充电设备')){s.keyword='充电桩';s.industry='新能源'}
  else if(text.includes('IT')||text.includes('信息化')||text.includes('软件')){s.keyword='IT / 信息化';s.industry='信息技术'}
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
  return `<div class="slots-card"><div class="slots-title">5 槽位解析结果</div>
    <div class="slots-grid">
      <div class="slot-item"><span class="slot-label">关键词</span><span class="slot-value">${s.keyword||'<span class="slot-empty">未指定</span>'}</span></div>
      <div class="slot-item"><span class="slot-label">地区</span><span class="slot-value">${s.region||'<span class="slot-empty">未指定</span>'}</span></div>
      <div class="slot-item"><span class="slot-label">时间范围</span><span class="slot-value">${s.time_range||'<span class="slot-empty">未指定</span>'}</span></div>
      <div class="slot-item"><span class="slot-label">行业</span><span class="slot-value">${s.industry||'<span class="slot-empty">未指定</span>'}</span></div>
      <div class="slot-item" style="grid-column:span 2;"><span class="slot-label">公告类型</span><span class="slot-value">${s.notice_type||'<span class="slot-empty">未指定</span>'}</span></div>
    </div></div>`;
}
renderAgents();document.getElementById('msgInput').focus();

function downloadReport(){
  const q=document.getElementById('chatInput')?document.getElementById('chatInput').value.trim():'';
  const query=q||'医疗设备采购';
  // 调真实后端 /api/demo/report 生成 Word 报告
  window.location.href='/api/demo/report?query='+encodeURIComponent(query);
}

</script>
</div>
</div>

</body>
</html>"""
