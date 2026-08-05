"""聊天 Demo 页 CSS 样式（W2-06 智能问答 · 6 Agent 协作）。

从 `app.templates.html.chat` 拆出的 `<style>` 块内容。
包含布局、消息气泡、Agent 卡片、侧边栏、进度条等样式。
"""

CHAT_CSS = """<style>
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

</style>"""

__all__ = ["CHAT_CSS"]
