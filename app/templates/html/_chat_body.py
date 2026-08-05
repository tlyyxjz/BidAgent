"""聊天 Demo 页 HTML body 结构（W2-06 智能问答 · 6 Agent 协作）。

从 `app.templates.html.chat` 拆出的 `<body>` 块内容。
包含侧边栏、顶部导航、聊天面板、Agent 协作面板。
"""

CHAT_BODY = """<body>
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
"""

__all__ = ["CHAT_BODY"]
