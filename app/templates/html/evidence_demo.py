"""公告详情+证据高亮页面（Demo 用）.

对应 v4.1 第十一章 11.2 核心交互：点击字段高亮原文证据.
"""

EVIDENCE_DEMO_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BidAgent - 公告详情与证据验证</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, "Microsoft YaHei", sans-serif; background: #f5f7fa; color: #333; }
.header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px 40px; }
.header h1 { font-size: 22px; margin-bottom: 4px; }
.header .subtitle { font-size: 13px; opacity: 0.85; }
.container { display: flex; gap: 20px; padding: 20px; max-width: 1600px; margin: 0 auto; }
.left-panel { flex: 1; background: white; border-radius: 12px; padding: 24px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); min-width: 0; }
.right-panel { width: 420px; background: white; border-radius: 12px; padding: 24px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); overflow-y: auto; max-height: calc(100vh - 120px); }
.panel-title { font-size: 16px; font-weight: 600; margin-bottom: 16px; padding-bottom: 12px; border-bottom: 2px solid #f0f0f0; }
.tender-meta { background: #f8f9fb; border-radius: 8px; padding: 12px 16px; margin-bottom: 16px; font-size: 13px; }
.tender-meta div { margin: 4px 0; }
.raw-text { font-size: 14px; line-height: 1.8; white-space: pre-wrap; word-break: break-all; padding: 16px; background: #fafbfc; border-radius: 8px; border: 1px solid #eee; max-height: calc(100vh - 280px); overflow-y: auto; }
.field-card { border: 1px solid #e8e8e8; border-radius: 8px; padding: 14px; margin-bottom: 12px; cursor: pointer; transition: all 0.2s; }
.field-card:hover { border-color: #667eea; box-shadow: 0 2px 8px rgba(102,126,234,0.15); }
.field-card.active { border-color: #667eea; background: #f8f9ff; }
.field-name { font-weight: 600; font-size: 14px; color: #333; margin-bottom: 4px; display: flex; align-items: center; gap: 8px; }
.field-value { font-size: 13px; color: #555; margin-bottom: 6px; word-break: break-all; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 500; }
.badge-direct { background: #e6f7e6; color: #389e0d; }
.badge-equivalent { background: #e6f4ff; color: #1890ff; }
.badge-unsupported { background: #fff2f0; color: #cf1322; }
.badge-verified { background: #f6ffed; color: #52c41a; }
.evidence-list { margin-top: 8px; }
.evidence-item { background: #f8f9fb; border-radius: 6px; padding: 8px 10px; margin-top: 6px; font-size: 12px; }
.evidence-item .ev-text { color: #333; margin-bottom: 4px; }
.evidence-item .ev-meta { color: #999; font-size: 11px; }
.highlight { background: #fff3cd; border-radius: 3px; padding: 1px 2px; }
.highlight-primary { background: #ffe066; font-weight: 500; }
.highlight-context { background: #d3e9ff; }
.highlight-conflict { background: #ffcdd2; }
.no-evidence { color: #999; font-style: italic; font-size: 12px; }
.empty { text-align: center; padding: 40px; color: #999; }
.back-link { display: inline-block; margin-bottom: 16px; color: #667eea; text-decoration: none; font-size: 13px; }
.back-link:hover { text-decoration: underline; }
.tender-select { width: 100%; padding: 8px 12px; border: 1px solid #e8e8e8; border-radius: 6px; margin-bottom: 16px; font-size: 14px; }
.stats { display: flex; gap: 12px; margin-bottom: 16px; }
.stat-item { flex: 1; text-align: center; background: #f8f9fb; border-radius: 8px; padding: 10px; }
.stat-num { font-size: 20px; font-weight: 600; color: #667eea; }
.stat-label { font-size: 11px; color: #999; margin-top: 2px; }
</style>
</head>
<body>
<div class="header">
    <h1>BidAgent 智能标讯助手</h1>
    <div class="subtitle">可验证招投标数据引擎 - 公告详情与证据验证</div>
</div>
<div class="container">
    <div class="left-panel">
        <div class="panel-title">公告原文（点击右侧字段高亮证据）</div>
        <select class="tender-select" id="tenderSelect" onchange="loadTender()">
            <option value="">选择公告...</option>
        </select>
        <div class="tender-meta" id="tenderMeta" style="display:none;">
            <div><strong>公告名称：</strong><span id="tenderName"></span></div>
            <div><strong>公告类型：</strong><span id="tenderType"></span></div>
            <div><strong>来源平台：</strong><span id="tenderPlatform"></span></div>
        </div>
        <div class="raw-text" id="rawText">
            <div class="empty">请选择公告查看原文</div>
        </div>
    </div>
    <div class="right-panel">
        <div class="panel-title">抽取字段与证据</div>
        <div class="stats" id="stats" style="display:none;">
            <div class="stat-item"><div class="stat-num" id="statFields">0</div><div class="stat-label">字段</div></div>
            <div class="stat-item"><div class="stat-num" id="statEvidence">0</div><div class="stat-label">证据</div></div>
            <div class="stat-item"><div class="stat-num" id="statVerified">0</div><div class="stat-label">已验证</div></div>
            <div class="stat-item"><div class="stat-num" id="statDirect">0</div><div class="stat-label">高可信</div></div>
        </div>
        <div id="fieldList">
            <div class="empty">请选择公告查看字段</div>
        </div>
    </div>
</div>
<script>
let currentRawText = "";
let currentHighlights = [];

// 加载公告列表
async function loadTenderList() {
    try {
        const resp = await fetch("/api/tenders?limit=20");
        const data = await resp.json();
        const select = document.getElementById("tenderSelect");
        (data.data || []).forEach(t => {
            const opt = document.createElement("option");
            opt.value = t.id;
            opt.textContent = t.project_name;
            select.appendChild(opt);
        });
    } catch (e) { console.error("加载列表失败", e); }
}

// 加载公告详情
async function loadTender() {
    const tid = document.getElementById("tenderSelect").value;
    if (!tid) return;
    clearHighlights();
    document.getElementById("fieldList").innerHTML = "<div class=empty>加载中...</div>";
    try {
        const resp = await fetch(`/api/tenders/${tid}/evidence`);
        const data = await resp.json();
        currentRawText = data.core_content || "";
        document.getElementById("tenderMeta").style.display = "block";
        document.getElementById("tenderName").textContent = data.project_name;
        document.getElementById("tenderType").textContent = data.notice_type || "-";
        document.getElementById("tenderPlatform").textContent = data.source_platform || "-";
        renderRawText();
        renderFields(data.fields || []);
        renderStats(data.fields || []);
    } catch (e) {
        document.getElementById("fieldList").innerHTML = "<div class=empty>加载失败: " + e.message + "</div>";
    }
}

function renderRawText() {
    const el = document.getElementById("rawText");
    el.textContent = currentRawText;
    el.innerHTML = escapeHtml(currentRawText);
}

function escapeHtml(s) {
    return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

function renderFields(fields) {
    const list = document.getElementById("fieldList");
    if (!fields.length) { list.innerHTML = "<div class=empty>无字段</div>"; return; }
    list.innerHTML = "";
    fields.forEach(f => {
        const card = document.createElement("div");
        card.className = "field-card";
        card.onclick = () => highlightField(f);
        const supportClass = f.support_level === "direct" ? "badge-direct" : (f.support_level === "equivalent" ? "badge-equivalent" : "badge-unsupported");
        const supportLabel = {direct:"直接证据",equivalent:"等价证据",inferred:"推导",unsupported:"无依据",contradicted:"冲突"}[f.support_level] || f.support_level;
        let evHtml = "";
        if (f.evidences && f.evidences.length) {
            evHtml = "<div class=evidence-list>";
            f.evidences.forEach(ev => {
                evHtml += `<div class=evidence-item>
                    <div class=ev-text>"${escapeHtml(ev.evidence_text.substring(0,80))}${ev.evidence_text.length>80?"...":""}"</div>
                    <div class=ev-meta>偏移[${ev.raw_start},${ev.raw_end}] 方法:${ev.match_method} ${ev.verified?"✓已验证":"✗未验证"} 角色:${ev.evidence_role}</div>
                </div>`;
            });
            evHtml += "</div>";
        } else {
            evHtml = "<div class=no-evidence>无证据（系统已拒绝展示）</div>";
        }
        card.innerHTML = `
            <div class=field-name>${f.field_name}
                <span class="badge ${supportClass}">${supportLabel}</span>
                ${f.evidences && f.evidences.length ? `<span class="badge badge-verified">${f.evidences.length}条证据</span>` : ""}
            </div>
            <div class=field-value>${f.raw_value ? escapeHtml(f.raw_value.substring(0,60)) : "(空)"}${f.amount_type?" ["+f.amount_type+"]":""}</div>
            ${evHtml}
        `;
        list.appendChild(card);
    });
}

function renderStats(fields) {
    document.getElementById("stats").style.display = "flex";
    const evCount = fields.reduce((s,f) => s + (f.evidences||[]).length, 0);
    const verifiedCount = fields.reduce((s,f) => s + (f.evidences||[]).filter(e=>e.verified).length, 0);
    const directCount = fields.filter(f => f.support_level === "direct" || f.support_level === "equivalent").length;
    document.getElementById("statFields").textContent = fields.length;
    document.getElementById("statEvidence").textContent = evCount;
    document.getElementById("statVerified").textContent = verifiedCount;
    document.getElementById("statDirect").textContent = directCount;
}

function highlightField(field) {
    clearHighlights();
    document.querySelectorAll(".field-card").forEach(c => c.classList.remove("active"));
    event.currentTarget.classList.add("active");
    if (!field.evidences || !field.evidences.length) return;
    const rawEl = document.getElementById("rawText");
    let html = escapeHtml(currentRawText);
    const highlights = [];
    field.evidences.forEach((ev, idx) => {
        const cls = ev.evidence_role === "primary" ? "highlight-primary" : "highlight-context";
        highlights.push({start: ev.raw_start, end: ev.raw_end, cls: cls, idx: idx});
    });
    highlights.sort((a,b) => b.start - a.start);
    highlights.forEach(h => {
        const before = html.substring(0, h.start);
        const mid = html.substring(h.start, h.end);
        const after = html.substring(h.end);
        html = before + `<span class="highlight ${h.cls}" data-idx="${h.idx}">${mid}</span>` + after;
    });
    rawEl.innerHTML = html;
    const firstHighlight = rawEl.querySelector(".highlight");
    if (firstHighlight) firstHighlight.scrollIntoView({behavior:"smooth", block:"center"});
    currentHighlights = highlights;
}

function clearHighlights() {
    currentHighlights = [];
    renderRawText();
}

loadTenderList();
</script>
</body>
</html>"""
