"""聊天 Demo 页 JavaScript 脚本（W2-06 智能问答 · 6 Agent 协作）。

从 `app.templates.html.chat` 拆出的 `<script>` 块内容。
包含 6 Agent 配置、渲染、消息收发、pipeline 轮询、槽位解析、报告下载。
"""

CHAT_SCRIPT = """<script>

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

</script>"""

__all__ = ["CHAT_SCRIPT"]
