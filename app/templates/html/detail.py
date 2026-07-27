"""招标详情页 HTML（W2-06 字段高亮 Demo）。

左侧：公告原文（基于字符偏移量高亮）
右侧：六类核心字段列表（点击字段 → 左侧高亮证据）
约束：单文件 ≤ 300 行
"""

TENDER_DETAIL_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=1366, initial-scale=1">
<title>招标详情 · 字段高亮 Demo</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,"Segoe UI","PingFang SC",sans-serif;
     background:#f5f7fa;color:#333;height:100vh;overflow:hidden}
.header{background:white;padding:12px 24px;border-bottom:1px solid #e8e8e8;
        display:flex;align-items:center;justify-content:space-between}
.header-left{display:flex;align-items:center;gap:16px}
.back-link{color:#1976d2;text-decoration:none;font-size:14px}
h1{font-size:18px;color:#1a1a2e}
.doc-id{font-size:12px;color:#999}
.legend{display:flex;gap:16px;font-size:12px}
.legend-item{display:flex;align-items:center;gap:6px}
.legend-color{width:14px;height:14px;border-radius:3px}
.legend-primary{background:rgba(239,83,80,.3);border:1px solid #ef5350}
.legend-context{background:rgba(255,193,7,.3);border:1px solid #ffc107}
.legend-qualifier{background:rgba(33,150,243,.3);border:1px solid #2196f3}
.main-container{display:flex;height:calc(100vh - 57px)}
.text-panel{flex:1;display:flex;flex-direction:column;border-right:1px solid #e8e8e8;
            background:white;min-width:0}
.panel-header{padding:12px 20px;border-bottom:1px solid #f0f0f0;
              display:flex;justify-content:space-between;align-items:center}
.panel-header h2{font-size:14px;color:#1a1a2e}
.char-count{font-size:12px;color:#999}
.text-container{flex:1;overflow:auto;padding:20px}
.raw-text{font-family:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;
          font-size:13px;line-height:1.8;white-space:pre-wrap;word-break:break-all;color:#333}
.hl{border-radius:2px;cursor:pointer;transition:all .15s}
.hl-primary{background:rgba(239,83,80,.28);border-bottom:2px solid #ef5350}
.hl-context{background:rgba(255,193,7,.28);border-bottom:2px solid #ffc107}
.hl-qualifier{background:rgba(33,150,243,.28);border-bottom:2px solid #2196f3}
.hl-active{box-shadow:0 0 0 2px rgba(25,118,210,.5)}
.fields-panel{width:420px;display:flex;flex-direction:column;background:#fafbfc;min-width:420px}
.fields-nav{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;
            padding:12px;border-bottom:1px solid #f0f0f0}
.field-nav-item{padding:8px 10px;background:white;border:1px solid #e0e0e0;
                border-radius:6px;cursor:pointer;text-align:center;font-size:12px;transition:all .15s}
.field-nav-item:hover{border-color:#1976d2;color:#1976d2}
.field-nav-item.active{background:#e3f2fd;border-color:#1976d2;color:#1565c0;font-weight:600}
.field-nav-item .count{font-size:11px;color:#999;display:block;margin-top:2px}
.fields-container{flex:1;overflow-y:auto;padding:12px}
.field-card{background:white;border:1px solid #e8e8e8;border-radius:8px;margin-bottom:12px;overflow:hidden}
.field-card-header{padding:12px 14px;background:#f8f9ff;border-bottom:1px solid #eef0f8;
                   display:flex;justify-content:space-between;align-items:center;cursor:pointer}
.field-card-title{font-size:13px;font-weight:600;color:#1a1a2e}
.field-card-badge{font-size:11px;padding:2px 8px;border-radius:10px}
.badge-unsupported{background:#ffebee;color:#c62828}
.badge-supported{background:#e8f5e9;color:#2e7d32}
.badge-absent{background:#f5f5f5;color:#999}
.field-card-body{padding:12px 14px;display:none}
.field-card.expanded .field-card-body{display:block}
.field-value{padding:10px;background:#f5f7fa;border-radius:6px;margin-bottom:10px;font-size:13px}
.field-value-label{font-size:11px;color:#999;margin-bottom:4px}
.field-value-text{color:#1a1a2e;word-break:break-all}
.evidence-list{margin-top:8px}
.evidence-item{padding:8px 10px;border:1px solid #e8e8e8;border-radius:6px;
               margin-bottom:6px;cursor:pointer;transition:all .15s;font-size:12px}
.evidence-item:hover{border-color:#1976d2;background:#f5f9ff}
.evidence-item.active{border-color:#1976d2;background:#e3f2fd}
.evidence-role{display:inline-block;padding:1px 6px;border-radius:3px;
               font-size:10px;font-weight:600;margin-bottom:4px}
.role-primary{background:#ffebee;color:#c62828}
.role-context{background:#fff8e1;color:#f57f17}
.role-qualifier{background:#e3f2fd;color:#1565c0}
.evidence-text{color:#555;line-height:1.5;word-break:break-all;
               display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.evidence-offset{font-size:10px;color:#aaa;margin-top:4px}
.no-evidence{color:#c62828;font-size:12px;padding:8px 0}
.support-level{font-size:11px;color:#666;margin-top:6px}
.empty-state{text-align:center;padding:40px 20px;color:#999;font-size:13px}
@media(max-width:900px){.fields-panel{width:340px;min-width:340px}}
</style>
</head>
<body>
<div class="header">
  <div class="header-left">
    <a href="/ui/tenders" class="back-link">← 返回列表</a>
    <h1>招标详情 · 字段高亮 Demo</h1>
    <span class="doc-id" id="docId">--</span>
  </div>
  <div class="legend">
    <div class="legend-item"><span class="legend-color legend-primary"></span>primary 主证据</div>
    <div class="legend-item"><span class="legend-color legend-context"></span>context 上下文</div>
    <div class="legend-item"><span class="legend-color legend-qualifier"></span>qualifier 限定</div>
  </div>
</div>
<div class="main-container">
  <section class="text-panel">
    <div class="panel-header"><h2>公告原文</h2><span class="char-count" id="charCount">0 字符</span></div>
    <div class="text-container"><pre id="rawText" class="raw-text">加载中...</pre></div>
  </section>
  <section class="fields-panel">
    <div class="panel-header"><h2>六类核心字段</h2><span style="font-size:12px;color:#999;">点击查看证据</span></div>
    <nav class="fields-nav" id="fieldsNav"></nav>
    <div class="fields-container" id="fieldsContainer"><div class="empty-state">加载中...</div></div>
  </section>
</div>
<script>
const FL={project_identifier:'项目编号',purchaser_name:'采购人',winner_name:'中标人',
          amount:'金额',publish_date:'发布日期',bid_deadline:'投标截止日期'};
const FO=['project_identifier','purchaser_name','winner_name','amount','publish_date','bid_deadline'];
let rawText='',annData=null,curField=null,activeEv=new Set();
function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML}
function renderText(evList){
  if(!rawText)return;
  const spans=evList.map(e=>({start:e.start,end:e.end,role:e.role,id:e.id}));
  spans.sort((a,b)=>a.start-b.start);
  const merged=[];
  for(const s of spans){
    if(!merged.length||s.start>=merged.at(-1).end){merged.push({...s,roles:[s.role],ids:[s.id]})}
    else{const l=merged.at(-1);l.end=Math.max(l.end,s.end);if(!l.roles.includes(s.role))l.roles.push(s.role);l.ids.push(s.id)}
  }
  let html='',pos=0;
  for(const m of merged){
    if(m.start>pos)html+=esc(rawText.slice(pos,m.start));
    const rc=m.roles.includes('primary')?'hl-primary':m.roles.includes('qualifier')?'hl-qualifier':'hl-context';
    const ac=m.ids.some(id=>activeEv.has(id))?' hl-active':'';
    html+=`<span class="hl ${rc}${ac}" data-ids="${m.ids.join(',')}">${esc(rawText.slice(m.start,m.end))}</span>`;
    pos=m.end;
  }
  if(pos<rawText.length)html+=esc(rawText.slice(pos));
  document.getElementById('rawText').innerHTML=html;
  document.getElementById('charCount').textContent=rawText.length+' 字符';
  document.querySelectorAll('.hl').forEach(el=>el.addEventListener('click',()=>{
    const ids=el.dataset.ids.split(',');const fn=findField(ids);if(fn)selField(fn,ids[0])
  }));
}
function findField(ids){
  if(!annData)return null;
  for(const f of annData.fields)for(const v of f.values||[])
    for(let i=0;i<(v.acceptable_evidence_spans||[]).length;i++){
      const eid=`${f.field_name}_0_${i}`;
      if(ids.includes(eid))return f.field_name
    }
  return null;
}
function allEvidence(){
  const r=[];if(!annData)return r;
  for(const f of annData.fields)for(let vi=0;vi<(f.values||[]).length;vi++){
    const v=f.values[vi];
    for(let ei=0;ei<(v.acceptable_evidence_spans||[]).length;ei++){
      const e=v.acceptable_evidence_spans[ei];
      r.push({id:`${f.field_name}_${vi}_${ei}`,start:e.start,end:e.end,role:e.role||'primary',text:e.text})
    }
  }
  return r;
}
function renderNav(){
  document.getElementById('fieldsNav').innerHTML=FO.map(name=>{
    const f=annData?.fields?.find(x=>x.field_name===name);
    const cnt=f?.values?.length||0,st=f?.gold_status||'absent';
    const hasEv=cnt>0&&f.values.some(v=>(v.acceptable_evidence_spans||[]).length>0);
    const cls=st==='absent'?'无此字段':hasEv?cnt+' 个值':'无依据';
    return `<div class="field-nav-item${curField===name?' active':''}" onclick="selField('${name}')">
              ${FL[name]||name}<span class="count">${cls}</span></div>`;
  }).join('');
}
function renderCards(){
  const c=document.getElementById('fieldsContainer');
  if(!annData){c.innerHTML='<div class="empty-state">暂无数据</div>';return}
  c.innerHTML=FO.map(name=>{
    const f=annData.fields.find(x=>x.field_name===name);if(!f)return '';
    const st=f.gold_status||'absent';
    const hasEv=(f.values||[]).some(v=>(v.acceptable_evidence_spans||[]).length>0);
    const bc=st==='absent'?'badge-absent':hasEv?'badge-supported':'badge-unsupported';
    const bt=st==='absent'?'无此字段':hasEv?'有依据':'无依据';
    const vHtml=(f.values||[]).map((v,vi)=>{
      const evs=v.acceptable_evidence_spans||[];
      const eh=evs.map((e,ei)=>{
        const eid=`${name}_${vi}_${ei}`,role=e.role||'primary';
        const rl=role==='primary'?'主证据':role==='context'?'上下文':role==='qualifier'?'限定条件':role;
        const ac=activeEv.has(eid)?' active':'';
        return `<div class="evidence-item${ac}" onclick="hlEv('${eid}',${e.start},${e.end})">
          <span class="evidence-role role-${role}">${rl}</span>
          <div class="evidence-text">${esc(e.text||'')}</div>
          <div class="evidence-offset">偏移:[${e.start},${e.end}) 长度:${e.end-e.start}</div></div>`;
      }).join('');
      const nv=v.normalized_value||v.raw_value||'(空)';
      const raw=v.raw_value&&v.raw_value!==v.normalized_value?
        `<div style="font-size:11px;color:#999;margin-top:4px;">原值:${esc(v.raw_value)}</div>`:'';
      return `<div class="field-value">
        <div class="field-value-label">字段值 ${vi+1}${v.lot_id?' · '+v.lot_id:''}</div>
        <div class="field-value-text">${esc(nv)}</div>${raw}
        ${v.amount_type?`<div class="support-level">金额类型:${v.amount_type}</div>`:''}
        ${evs.length?`<div class="evidence-list">${eh}</div>`:'<div class="no-evidence">⚠ 无证据支持</div>'}
      </div>`;
    }).join('');
    const exp=curField===name?' expanded':'';
    return `<div class="field-card${exp}"><div class="field-card-header" onclick="toggleField('${name}')">
      <span class="field-card-title">${FL[name]||name}</span>
      <span class="field-card-badge ${bc}">${bt}</span></div>
      <div class="field-card-body">${st==='absent'?
        '<div class="no-evidence">该公告不含此字段（absent）</div>':
        (vHtml||'<div class="no-evidence">⚠ 无依据 - 未找到证据支持</div>')}
      </div></div>`;
  }).join('');
}
function toggleField(n){if(curField===n){curField=null;clearHL()}else selField(n)}
function selField(name,evId){
  curField=name;activeEv.clear();
  const f=annData?.fields?.find(x=>x.field_name===name);
  if(f)(f.values||[]).forEach((v,vi)=>(v.acceptable_evidence_spans||[]).forEach((e,ei)=>{
    const eid=`${name}_${vi}_${ei}`;if(!evId)activeEv.add(eid)
  }));
  if(evId)activeEv.add(evId);
  renderText(allEvidence());renderNav();renderCards();
  if(evId){const el=document.querySelector(`.hl[data-ids*="${evId}"]`);if(el)el.scrollIntoView({behavior:'smooth',block:'center'})}
}
function hlEv(eid,s,e){
  const ac=activeEv.has(eid);activeEv.clear();if(!ac)activeEv.add(eid);
  renderText(allEvidence());renderCards();
  if(activeEv.size){const el=document.querySelector(`.hl[data-ids*="${eid}"]`);if(el)el.scrollIntoView({behavior:'smooth',block:'center'})}
}
function clearHL(){activeEv.clear();renderText(allEvidence());renderNav();renderCards()}
async function loadData(){
  const p=new URLSearchParams(window.location.search);
  const doc=p.get('doc')||'tender_06_4e47868721c5';
  document.getElementById('docId').textContent=doc;
  try{
    const rr=await fetch(`/ui/api/demo/raw?doc=${encodeURIComponent(doc)}`);
    if(!rr.ok)throw new Error('原文加载失败');rawText=await rr.text();
    const ar=await fetch(`/ui/api/demo/annotation?doc=${encodeURIComponent(doc)}`);
    if(!ar.ok)throw new Error('标注加载失败');annData=await ar.json();
  }catch(e){
    document.getElementById('rawText').textContent='加载失败: '+e.message+'\\n\\n请确保 Demo 数据服务已配置正确。';return
  }
  renderText(allEvidence());renderNav();renderCards();
}
loadData();
</script>
</body>
</html>"""
