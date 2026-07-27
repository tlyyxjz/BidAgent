// BidAgent GOAI 初赛 PPT 生成器（28 页）
// 严格按照 GOAI_初赛提交材料_正式版.md 大纲
// 调色板：Midnight Executive（navy + ice blue + white），契合金融主题

const PptxGenJS = require('pptxgenjs');
const fs = require('fs');

const pptx = new PptxGenJS();
// 显式声明 4:3 布局（10" x 7.5"）。脚本所有 y 坐标按 7.5" 高度设计，
// 不设置会默认 16:9（10" x 5.625"），导致 y > 5.625 的页脚/封面日期等超出页面。
pptx.defineLayout({ name: 'CUSTOM_4x3', width: 10, height: 7.5 });
pptx.layout = 'CUSTOM_4x3';
pptx.author = '徐浚钊';
pptx.title = 'BidAgent 智能标讯助手';
pptx.subject = 'GOAI 世界人工智能开源大赛 · AI+金融方向';

// 调色板（Midnight Executive）
const NAVY = '1E2761';
const ICE = 'CADCFC';
const WHITE = 'FFFFFF';
const ACCENT = 'FFC107'; // 金色点缀
const GREY = '5A6B8C';
const LIGHT_BG = 'F5F7FB';
const DANGER = 'D32F2F';
const SUCCESS = '388E3C';

// 字体
const HEADER_FONT = 'Calibri';
const BODY_FONT = 'Calibri';

// 统一布局
const LAYOUT = {
  margin: 0.5,
  titleY: 0.4,
  titleSize: 32,
  bodySize: 14,
};

// 全局 slide 计数器（解决 pptxgenjs slideNumber 在 addSlide 时为 null 的问题）
// 初始化为 1：封面（slide 1）不调用 addSlideBase，但占用第 1 页
let SLIDE_IDX = 1;
const TOTAL_SLIDES = 28;

// 工具函数
function addSlideBase(slide, title, opts = {}) {
  slide.background = { color: opts.bg || WHITE };
  if (opts.dark) {
    slide.background = { color: NAVY };
    slide.addText(title, {
      x: LAYOUT.margin, y: LAYOUT.titleY, w: 9, h: 0.7,
      fontFace: HEADER_FONT, fontSize: LAYOUT.titleSize, bold: true,
      color: WHITE, align: 'left'
    });
    // 装饰：右侧金色小方块
    slide.addShape(pptx.ShapeType.rect, {
      x: 9.5, y: 0.5, w: 0.15, h: 0.5, fill: { color: ACCENT }, line: { color: ACCENT }
    });
  } else {
    slide.addText(title, {
      x: LAYOUT.margin, y: LAYOUT.titleY, w: 9, h: 0.7,
      fontFace: HEADER_FONT, fontSize: LAYOUT.titleSize, bold: true,
      color: NAVY, align: 'left'
    });
    // 装饰：标题下方小金色方块
    slide.addShape(pptx.ShapeType.rect, {
      x: LAYOUT.margin, y: 1.05, w: 0.5, h: 0.08, fill: { color: ACCENT }, line: { color: ACCENT }
    });
  }
  // 页脚
  if (!opts.noFooter) {
    SLIDE_IDX += 1;
    slide.addText('BidAgent · GOAI 2026 · 标小智', {
      x: LAYOUT.margin, y: 7.0, w: 9, h: 0.3,
      fontFace: BODY_FONT, fontSize: 10, color: GREY, align: 'left'
    });
    slide.addText(`${SLIDE_IDX} / ${TOTAL_SLIDES}`, {
      x: 9, y: 7.0, w: 1, h: 0.3,
      fontFace: BODY_FONT, fontSize: 10, color: GREY, align: 'right'
    });
  }
}

// ========== 第 1 页：封面 ==========
{
  const s = pptx.addSlide();
  s.background = { color: NAVY };
  // 主标题
  s.addText('BidAgent', {
    x: 0.5, y: 2.0, w: 9, h: 1.0,
    fontFace: HEADER_FONT, fontSize: 60, bold: true, color: WHITE, align: 'left'
  });
  // 副标题
  s.addText('智能标讯助手 · AI+金融', {
    x: 0.5, y: 3.0, w: 9, h: 0.6,
    fontFace: HEADER_FONT, fontSize: 28, color: ICE, align: 'left'
  });
  // 金色分割线
  s.addShape(pptx.ShapeType.rect, {
    x: 0.5, y: 3.8, w: 2, h: 0.05, fill: { color: ACCENT }, line: { color: ACCENT }
  });
  // 一句话定位
  s.addText('为供应链金融机构提供招投标数据聚合与供应商信用评分 API 的 AI Agent 应用', {
    x: 0.5, y: 4.0, w: 9, h: 0.8,
    fontFace: BODY_FONT, fontSize: 16, color: WHITE, align: 'left', italic: true
  });
  // 赛事信息
  s.addText('GOAI 世界人工智能开源大赛 · 无界应用赛道 · AI+金融方向', {
    x: 0.5, y: 5.5, w: 9, h: 0.4,
    fontFace: BODY_FONT, fontSize: 14, color: ICE, align: 'left'
  });
  // 团队
  s.addText('团队：标小智  ·  徐浚钊  ·  王祯明', {
    x: 0.5, y: 6.0, w: 9, h: 0.4,
    fontFace: BODY_FONT, fontSize: 14, color: ICE, align: 'left'
  });
  // 日期
  s.addText('2026 年 8 月', {
    x: 0.5, y: 6.5, w: 9, h: 0.4,
    fontFace: BODY_FONT, fontSize: 12, color: ICE, align: 'left'
  });
  // 右上装饰
  s.addShape(pptx.ShapeType.rect, {
    x: 9.0, y: 0.5, w: 1, h: 0.05, fill: { color: ACCENT }, line: { color: ACCENT }
  });
}

// ========== 第 2 页：目录 ==========
{
  const s = pptx.addSlide();
  addSlideBase(s, '目录');
  const items = [
    { num: '01', title: '项目背景与定位', page: '3-7' },
    { num: '02', title: '六 Agent 协同架构', page: '8-12' },
    { num: '03', title: '产品体验与 Demo', page: '13' },
    { num: '04', title: '六大技术亮点', page: '14-19' },
    { num: '05', title: '测试与质量保障', page: '20-23' },
    { num: '06', title: '安全合规与开放复用', page: '24-25' },
    { num: '07', title: '路线图与团队', page: '26-28' },
  ];
  items.forEach((it, i) => {
    const y = 1.5 + i * 0.65;
    s.addText(it.num, {
      x: 0.8, y, w: 0.8, h: 0.5,
      fontFace: HEADER_FONT, fontSize: 22, bold: true, color: ACCENT, align: 'left'
    });
    s.addText(it.title, {
      x: 1.7, y: y + 0.05, w: 6, h: 0.4,
      fontFace: BODY_FONT, fontSize: 16, color: NAVY, align: 'left'
    });
    s.addText(`P.${it.page}`, {
      x: 8, y: y + 0.05, w: 1.5, h: 0.4,
      fontFace: BODY_FONT, fontSize: 12, color: GREY, align: 'right'
    });
    // 分隔线
    if (i < items.length - 1) {
      s.addShape(pptx.ShapeType.line, {
        x: 0.8, y: y + 0.55, w: 8.5, h: 0, line: { color: ICE, width: 1 }
      });
    }
  });
}

// ========== 第 3 页：项目背景 - 数据孤岛 ==========
{
  const s = pptx.addSlide();
  addSlideBase(s, '01 · 项目背景');
  s.addText('招投标信息分散在 30+ 平台，形成数据孤岛', {
    x: 0.5, y: 1.4, w: 9, h: 0.5,
    fontFace: BODY_FONT, fontSize: 18, color: GREY, align: 'left', italic: true
  });
  // 四个平台卡片
  const platforms = [
    { name: 'ccgp.gov.cn', desc: '中国政府采购网', color: NAVY },
    { name: 'chinabidding', desc: '中国招标投标网', color: GREY },
    { name: 'ggzy.gov.cn', desc: '公共资源交易平台', color: NAVY },
    { name: 'vip.qianlima', desc: '千里马招标网', color: GREY },
  ];
  platforms.forEach((p, i) => {
    const x = 0.5 + (i % 2) * 4.5;
    const y = 2.1 + Math.floor(i / 2) * 1.5;
    s.addShape(pptx.ShapeType.roundRect, {
      x, y, w: 4, h: 1.3, fill: { color: LIGHT_BG }, line: { color: p.color, width: 2 }, rectRadius: 0.1
    });
    s.addText(p.name, {
      x: x + 0.2, y: y + 0.15, w: 3.6, h: 0.4,
      fontFace: HEADER_FONT, fontSize: 16, bold: true, color: p.color, align: 'left'
    });
    s.addText(p.desc, {
      x: x + 0.2, y: y + 0.65, w: 3.6, h: 0.4,
      fontFace: BODY_FONT, fontSize: 12, color: GREY, align: 'left'
    });
  });
  // 底部痛点
  s.addText('金融机构评估供应商信用时，需人工跨平台检索、比对报价、核查中标记录，耗时且易遗漏风险信号', {
    x: 0.5, y: 5.5, w: 9, h: 0.8,
    fontFace: BODY_FONT, fontSize: 14, color: DANGER, align: 'left', italic: true
  });
}

// ========== 第 4 页：用户痛点 ==========
{
  const s = pptx.addSlide();
  addSlideBase(s, '01 · 用户痛点');
  const pains = [
    { icon: '⏱', title: '人工检索耗时', desc: '跨 30 个平台人工查供应商中标记录，单次尽调 2-4 小时', impact: '2-4 小时' },
    { icon: '⚠', title: '风险信号遗漏', desc: '报价异常/废标风险/供应商信用难系统化分析', impact: '高漏报率' },
    { icon: '🔄', title: '重复劳动', desc: '同一招标公告多平台重复出现，人工筛选困难', impact: '低效率' },
    { icon: '📅', title: '错过截止', desc: '招标截止时间各异，没有提醒机制容易错过', impact: '错失机会' },
  ];
  pains.forEach((p, i) => {
    const y = 1.5 + i * 1.3;
    // 图标圆圈
    s.addShape(pptx.ShapeType.ellipse, {
      x: 0.6, y: y + 0.1, w: 0.6, h: 0.6, fill: { color: NAVY }, line: { color: NAVY }
    });
    s.addText(p.icon, {
      x: 0.6, y: y + 0.15, w: 0.6, h: 0.5,
      fontFace: BODY_FONT, fontSize: 20, color: WHITE, align: 'center', valign: 'middle'
    });
    // 标题
    s.addText(p.title, {
      x: 1.5, y, w: 5, h: 0.4,
      fontFace: HEADER_FONT, fontSize: 16, bold: true, color: NAVY, align: 'left'
    });
    // 描述
    s.addText(p.desc, {
      x: 1.5, y: y + 0.45, w: 5, h: 0.5,
      fontFace: BODY_FONT, fontSize: 12, color: GREY, align: 'left'
    });
    // 影响标签
    s.addShape(pptx.ShapeType.roundRect, {
      x: 7.5, y: y + 0.2, w: 2, h: 0.5, fill: { color: DANGER }, line: { color: DANGER }, rectRadius: 0.05
    });
    s.addText(p.impact, {
      x: 7.5, y: y + 0.25, w: 2, h: 0.4,
      fontFace: HEADER_FONT, fontSize: 12, bold: true, color: WHITE, align: 'center'
    });
  });
}

// ========== 第 5 页：目标用户 ==========
{
  const s = pptx.addSlide();
  addSlideBase(s, '01 · 目标用户');
  const users = [
    { name: '供应链金融机构', val: '核心用户', pain: '供应商信用评估耗时', color: NAVY },
    { name: '投标企业 BD', val: '高频用户', pain: '找不到能投的项目', color: GREY },
    { name: '招标代理公司', val: '企业用户', pain: '客户要中标分析报告', color: NAVY },
    { name: '财税咨询公司', val: '企业用户', pain: '客户问对手中标情况', color: GREY },
  ];
  users.forEach((u, i) => {
    const x = 0.5 + (i % 2) * 4.7;
    const y = 1.5 + Math.floor(i / 2) * 2.5;
    // 大卡片
    s.addShape(pptx.ShapeType.roundRect, {
      x, y, w: 4.2, h: 2.2, fill: { color: WHITE }, line: { color: u.color, width: 2 }, rectRadius: 0.1
    });
    // 用户类型标签
    s.addShape(pptx.ShapeType.roundRect, {
      x: x + 0.2, y: y + 0.2, w: 1.5, h: 0.4, fill: { color: u.color }, line: { color: u.color }, rectRadius: 0.05
    });
    s.addText(u.val, {
      x: x + 0.2, y: y + 0.25, w: 1.5, h: 0.3,
      fontFace: HEADER_FONT, fontSize: 11, bold: true, color: WHITE, align: 'center'
    });
    // 用户名
    s.addText(u.name, {
      x: x + 0.2, y: y + 0.8, w: 3.8, h: 0.5,
      fontFace: HEADER_FONT, fontSize: 18, bold: true, color: NAVY, align: 'left'
    });
    // 痛点
    s.addText('痛点：' + u.pain, {
      x: x + 0.2, y: y + 1.4, w: 3.8, h: 0.6,
      fontFace: BODY_FONT, fontSize: 12, color: GREY, align: 'left'
    });
  });
}

// ========== 第 6 页：市场规模 ==========
{
  const s = pptx.addSlide();
  addSlideBase(s, '01 · 市场规模');
  // 大数字
  s.addText('30+', {
    x: 0.5, y: 1.5, w: 4, h: 1.5,
    fontFace: HEADER_FONT, fontSize: 80, bold: true, color: NAVY, align: 'center'
  });
  s.addText('招投标信息平台', {
    x: 0.5, y: 3.0, w: 4, h: 0.5,
    fontFace: BODY_FONT, fontSize: 16, color: GREY, align: 'center'
  });
  // 分割
  s.addShape(pptx.ShapeType.line, {
    x: 4.8, y: 1.5, w: 0, h: 3, line: { color: ICE, width: 2 }
  });
  // 右侧
  s.addText('2-4h', {
    x: 5.3, y: 1.5, w: 4, h: 1.5,
    fontFace: HEADER_FONT, fontSize: 80, bold: true, color: ACCENT, align: 'center'
  });
  s.addText('人工单次尽调耗时', {
    x: 5.3, y: 3.0, w: 4, h: 0.5,
    fontFace: BODY_FONT, fontSize: 16, color: GREY, align: 'center'
  });
  // BidAgent 价值
  s.addShape(pptx.ShapeType.roundRect, {
    x: 0.5, y: 4.5, w: 9, h: 1.5, fill: { color: NAVY }, line: { color: NAVY }, rectRadius: 0.1
  });
  s.addText('BidAgent 价值', {
    x: 0.7, y: 4.65, w: 3, h: 0.4,
    fontFace: HEADER_FONT, fontSize: 14, bold: true, color: ACCENT, align: 'left'
  });
  s.addText('将"跨 30 平台人工查公告做风控"从 2-4 小时压缩到几分钟，且每条数据有原文证据可追溯', {
    x: 0.7, y: 5.1, w: 8.6, h: 0.8,
    fontFace: BODY_FONT, fontSize: 14, color: WHITE, align: 'left'
  });
}

// ========== 第 7 页：解决方案概述 ==========
{
  const s = pptx.addSlide();
  addSlideBase(s, '01 · 解决方案');
  s.addText('一句话需求 → Word 报告 + 邮件推送', {
    x: 0.5, y: 1.4, w: 9, h: 0.6,
    fontFace: HEADER_FONT, fontSize: 20, bold: true, color: NAVY, align: 'center'
  });
  // 流程图
  const steps = ['自然语言', '六 Agent 协同', 'Word 报告', '邮件推送'];
  steps.forEach((step, i) => {
    const x = 0.5 + i * 2.4;
    s.addShape(pptx.ShapeType.roundRect, {
      x, y: 2.5, w: 2, h: 0.8, fill: { color: i === 1 ? NAVY : LIGHT_BG }, line: { color: NAVY, width: 2 }, rectRadius: 0.1
    });
    s.addText(step, {
      x, y: 2.6, w: 2, h: 0.6,
      fontFace: HEADER_FONT, fontSize: 13, bold: i === 1, color: i === 1 ? WHITE : NAVY, align: 'center', valign: 'middle'
    });
    if (i < steps.length - 1) {
      s.addShape(pptx.ShapeType.rightArrow, {
        x: x + 2.05, y: 2.7, w: 0.3, h: 0.4, fill: { color: ACCENT }, line: { color: ACCENT }
      });
    }
  });
  // 三大差异化
  s.addText('三大差异化能力', {
    x: 0.5, y: 4.0, w: 9, h: 0.5,
    fontFace: HEADER_FONT, fontSize: 18, bold: true, color: NAVY, align: 'left'
  });
  const caps = [
    { num: '1', title: 'BOQ 报价异常检测', desc: '20 类基准价格库，识别围标/劣质供货' },
    { num: '2', title: '废标风险预警', desc: '18 条规则扫描资质与条款隐患' },
    { num: '3', title: '供应商信用评分', desc: '活跃度/中标率/偏离度三维度加权' },
  ];
  caps.forEach((c, i) => {
    const y = 4.6 + i * 0.7;
    s.addShape(pptx.ShapeType.ellipse, {
      x: 0.6, y, w: 0.4, h: 0.4, fill: { color: ACCENT }, line: { color: ACCENT }
    });
    s.addText(c.num, {
      x: 0.6, y, w: 0.4, h: 0.4,
      fontFace: HEADER_FONT, fontSize: 14, bold: true, color: NAVY, align: 'center', valign: 'middle'
    });
    s.addText(c.title, {
      x: 1.2, y, w: 3, h: 0.4,
      fontFace: HEADER_FONT, fontSize: 14, bold: true, color: NAVY, align: 'left'
    });
    s.addText(c.desc, {
      x: 4.2, y, w: 5.3, h: 0.4,
      fontFace: BODY_FONT, fontSize: 12, color: GREY, align: 'left'
    });
  });
}

// ========== 第 8 页：六 Agent 架构图 ==========
{
  const s = pptx.addSlide();
  addSlideBase(s, '02 · 六 Agent 协同架构');
  const agents = [
    { num: '①', name: '意图解析 Agent', desc: '自然语言 → 5 槽位 + 多轮追问' },
    { num: '②', name: '采集执行 Agent', desc: '4+ 平台并行抓取 + 登录态' },
    { num: '③', name: '数据加工 Agent', desc: '字段对齐 + 分类 + 相关性' },
    { num: '④', name: '质量保障 Agent', desc: 'SimHash 去重 + 反幻觉校验' },
    { num: '⑤', name: '金融分析 Agent ⭐', desc: 'BOQ + 废标 + 信用评分' },
    { num: '⑥', name: '报告交付 Agent', desc: 'Word + SMTP + Webhook' },
  ];
  agents.forEach((a, i) => {
    const y = 1.3 + i * 0.82;
    const isCore = i === 4;
    // 序号圆
    s.addShape(pptx.ShapeType.ellipse, {
      x: 0.5, y, w: 0.55, h: 0.55, fill: { color: isCore ? ACCENT : NAVY }, line: { color: isCore ? ACCENT : NAVY }
    });
    s.addText(a.num, {
      x: 0.5, y: y + 0.05, w: 0.55, h: 0.45,
      fontFace: HEADER_FONT, fontSize: 18, bold: true, color: isCore ? NAVY : WHITE, align: 'center', valign: 'middle'
    });
    // Agent 名
    s.addText(a.name, {
      x: 1.25, y: y + 0.05, w: 3.5, h: 0.45,
      fontFace: HEADER_FONT, fontSize: 15, bold: true, color: isCore ? ACCENT : NAVY, align: 'left'
    });
    // 描述
    s.addText(a.desc, {
      x: 5, y: y + 0.08, w: 4.5, h: 0.4,
      fontFace: BODY_FONT, fontSize: 12, color: GREY, align: 'left'
    });
    // 向下箭头
    if (i < agents.length - 1) {
      s.addShape(pptx.ShapeType.downArrow, {
        x: 0.7, y: y + 0.6, w: 0.15, h: 0.15, fill: { color: ICE }, line: { color: ICE }
      });
    }
  });
  // 底部标注
  s.addText('每个 Agent 职责单一、技术栈清晰，金融分析 Agent 独立成卖点，与 AI+金融赛道主题完美契合', {
    x: 0.5, y: 6.4, w: 9, h: 0.4,
    fontFace: BODY_FONT, fontSize: 11, color: GREY, align: 'left', italic: true
  });
}

// ========== 第 9-12 页：各 Agent 详解 ==========
function addAgentSlide(slideNum, agentNum, agentName, agentDesc, responsibilities, tech, files) {
  const s = pptx.addSlide();
  addSlideBase(s, `02 · ${agentNum} ${agentName}`);
  s.addText(agentDesc, {
    x: 0.5, y: 1.3, w: 9, h: 0.4,
    fontFace: BODY_FONT, fontSize: 14, color: GREY, align: 'left', italic: true
  });
  // 职责
  s.addText('核心职责', {
    x: 0.5, y: 1.9, w: 3, h: 0.4,
    fontFace: HEADER_FONT, fontSize: 14, bold: true, color: NAVY, align: 'left'
  });
  responsibilities.forEach((r, i) => {
    const y = 2.4 + i * 0.45;
    s.addShape(pptx.ShapeType.rect, {
      x: 0.6, y: y + 0.13, w: 0.1, h: 0.1, fill: { color: ACCENT }, line: { color: ACCENT }
    });
    s.addText(r, {
      x: 0.9, y, w: 8.6, h: 0.4,
      fontFace: BODY_FONT, fontSize: 13, color: NAVY, align: 'left'
    });
  });
  // 技术栈
  s.addText('技术栈', {
    x: 0.5, y: 4.7, w: 3, h: 0.4,
    fontFace: HEADER_FONT, fontSize: 14, bold: true, color: NAVY, align: 'left'
  });
  tech.forEach((t, i) => {
    const y = 5.2 + i * 0.4;
    s.addText(`• ${t}`, {
      x: 0.7, y, w: 8.8, h: 0.35,
      fontFace: BODY_FONT, fontSize: 12, color: GREY, align: 'left'
    });
  });
  // 文件
  s.addText('代码文件：' + files, {
    x: 0.5, y: 6.5, w: 9, h: 0.3,
    fontFace: 'Consolas', fontSize: 11, color: NAVY, align: 'left'
  });
}

addAgentSlide(9, '①', '意图解析 Agent',
  '用户说"找上海最近7天的IT采购项目" → 拆解为 5 槽位',
  ['自然语言查询解析', '关键词降级兜底（LLM 不可用时走规则匹配）', '多轮追问（slot 缺失时反问用户）', '输出 ParsedFilters（query/region/budget/time/category）'],
  ['DeepSeek V3 LLM', 'Pydantic Schema 校验', '关键词正则降级'],
  'app/agents/intent_agent.py + app/llm/parser.py');

addAgentSlide(10, '②', '采集执行 Agent',
  '调度多平台采集器并行抓取，管理登录态',
  ['4+ 平台并行采集（ccgp/chinabidding/ggzy/千里马）', '千里马登录态采集（16 cookies 持久化实测通过）', '浏览器反检测 + 浏览器池复用', '任务状态上报（progress/started_at/completed_at）'],
  ['Playwright + patchright stealth', 'SessionManager storage_state', 'BrowserPool 信号量有界池'],
  'app/agents/collector_agent.py + app/templates/*.py');

addAgentSlide(11, '③④', '数据加工 + 质量保障 Agent',
  '字段对齐 → 分类标注 → SimHash 去重 → 反幻觉校验',
  ['字段对齐（不同平台字段名映射到统一 schema）', '分类标注（IT/工程/医疗等品类）+ 相关性评分（TF-IDF）', 'SimHash 64 位去重（汉明距离 ≤ 3）', '反幻觉校验（金额/日期归一化 + 原文事实比对，无出处一律丢弃）'],
  ['jieba 分词 + SimHash 64 位', '金额万元/亿元转元 + 日期 ISO 8601 归一化', 'SQL NOT EXISTS 增量查询'],
  'app/agents/{processor,quality}_agent.py + app/processors/{simhash,hallucination_checker}.py');

// ========== 第 12 页：金融分析 Agent（核心）==========
{
  const s = pptx.addSlide();
  addSlideBase(s, '02 · ⑤ 金融分析 Agent（核心）');
  // 三大子模块
  const modules = [
    {
      title: 'BOQ 报价异常检测',
      desc: '20 类基准价格库\n正则提取 + 市场均价 ±std 判定\nunderpriced/overpriced/normal',
      stat: '20 类',
      statLabel: '基准品类'
    },
    {
      title: '废标风险预警',
      desc: '18 条规则覆盖\n排他性资质/付款风险/交货期/资质门槛\n含否定语境检测',
      stat: '18 条',
      statLabel: '风险规则'
    },
    {
      title: '供应商信用评分',
      desc: '三维度加权评分\n活跃度 30% + 中标率 40% + 偏离度 30%\n输出 SupplierRiskReport',
      stat: '30/40/30',
      statLabel: '权重分配'
    },
  ];
  modules.forEach((m, i) => {
    const x = 0.5 + i * 3.15;
    // 卡片
    s.addShape(pptx.ShapeType.roundRect, {
      x, y: 1.5, w: 2.95, h: 4.5, fill: { color: LIGHT_BG }, line: { color: NAVY, width: 2 }, rectRadius: 0.1
    });
    // 标题
    s.addText(m.title, {
      x: x + 0.15, y: 1.7, w: 2.65, h: 0.7,
      fontFace: HEADER_FONT, fontSize: 15, bold: true, color: NAVY, align: 'center'
    });
    // 分隔线
    s.addShape(pptx.ShapeType.line, {
      x: x + 0.5, y: 2.5, w: 1.95, h: 0, line: { color: ACCENT, width: 2 }
    });
    // 描述
    s.addText(m.desc, {
      x: x + 0.2, y: 2.7, w: 2.55, h: 2,
      fontFace: BODY_FONT, fontSize: 12, color: NAVY, align: 'left', valign: 'top'
    });
    // 大数字
    s.addText(m.stat, {
      x: x + 0.15, y: 4.8, w: 2.65, h: 0.7,
      fontFace: HEADER_FONT, fontSize: 28, bold: true, color: ACCENT, align: 'center'
    });
    s.addText(m.statLabel, {
      x: x + 0.15, y: 5.5, w: 2.65, h: 0.3,
      fontFace: BODY_FONT, fontSize: 11, color: GREY, align: 'center'
    });
  });
  // 底部
  s.addText('已剔除伪造的联邦学习代码（PPT 不讲联邦学习，避免答辩翻车）', {
    x: 0.5, y: 6.3, w: 9, h: 0.3,
    fontFace: BODY_FONT, fontSize: 11, color: GREY, align: 'left', italic: true
  });
  s.addText('代码：app/agents/finance_agent.py + app/processors/{boq_engine,risk_engine,supplier_risk}.py', {
    x: 0.5, y: 6.6, w: 9, h: 0.3,
    fontFace: 'Consolas', fontSize: 11, color: NAVY, align: 'left'
  });
}

// ========== 第 13 页：产品体验 ==========
{
  const s = pptx.addSlide();
  addSlideBase(s, '03 · 产品体验与 Demo');
  // 流程
  const flow = [
    { step: '1', title: '用户输入', desc: '"找上海最近7天IT采购项目，预算100万以上"' },
    { step: '2', title: '意图解析', desc: '展示 5 槽位：关键词/地区/预算/时间/品类' },
    { step: '3', title: '采集进度', desc: '4 平台并行采集，实时进度条' },
    { step: '4', title: '数据加工', desc: '去重 + 反幻觉校验，展示丢弃条数' },
    { step: '5', title: '金融分析', desc: 'BOQ 异常 + 废标风险 + 信用评分' },
    { step: '6', title: '报告交付', desc: 'Word 报告下载 + 邮件推送' },
  ];
  flow.forEach((f, i) => {
    const col = i % 3;
    const row = Math.floor(i / 3);
    const x = 0.5 + col * 3.15;
    const y = 1.5 + row * 2.5;
    s.addShape(pptx.ShapeType.roundRect, {
      x, y, w: 2.95, h: 2.2, fill: { color: WHITE }, line: { color: NAVY, width: 2 }, rectRadius: 0.1
    });
    s.addShape(pptx.ShapeType.ellipse, {
      x: x + 1.15, y: y + 0.2, w: 0.6, h: 0.6, fill: { color: NAVY }, line: { color: NAVY }
    });
    s.addText(f.step, {
      x: x + 1.15, y: y + 0.25, w: 0.6, h: 0.5,
      fontFace: HEADER_FONT, fontSize: 20, bold: true, color: WHITE, align: 'center', valign: 'middle'
    });
    s.addText(f.title, {
      x: x + 0.15, y: y + 0.95, w: 2.65, h: 0.4,
      fontFace: HEADER_FONT, fontSize: 14, bold: true, color: NAVY, align: 'center'
    });
    s.addText(f.desc, {
      x: x + 0.2, y: y + 1.4, w: 2.55, h: 0.7,
      fontFace: BODY_FONT, fontSize: 11, color: GREY, align: 'center', valign: 'top'
    });
  });
}

// ========== 第 14-21 页：六大技术亮点 ==========
function addTechSlide(num, title, subtitle, problem, solution, stat, statLabel, codeRef) {
  const s = pptx.addSlide();
  addSlideBase(s, `04 · T${num} ${title}`);
  s.addText(subtitle, {
    x: 0.5, y: 1.3, w: 9, h: 0.4,
    fontFace: BODY_FONT, fontSize: 14, color: GREY, align: 'left', italic: true
  });
  // 痛点
  s.addText('问题', {
    x: 0.5, y: 1.9, w: 2, h: 0.4,
    fontFace: HEADER_FONT, fontSize: 14, bold: true, color: DANGER, align: 'left'
  });
  s.addText(problem, {
    x: 0.5, y: 2.3, w: 9, h: 0.8,
    fontFace: BODY_FONT, fontSize: 13, color: NAVY, align: 'left'
  });
  // 解决方案
  s.addText('解决方案', {
    x: 0.5, y: 3.2, w: 3, h: 0.4,
    fontFace: HEADER_FONT, fontSize: 14, bold: true, color: SUCCESS, align: 'left'
  });
  s.addText(solution, {
    x: 0.5, y: 3.6, w: 9, h: 1.5,
    fontFace: BODY_FONT, fontSize: 13, color: NAVY, align: 'left', valign: 'top'
  });
  // 数据
  if (stat) {
    s.addShape(pptx.ShapeType.roundRect, {
      x: 0.5, y: 5.3, w: 9, h: 1.0, fill: { color: NAVY }, line: { color: NAVY }, rectRadius: 0.1
    });
    s.addText(stat, {
      x: 0.7, y: 5.4, w: 4, h: 0.8,
      fontFace: HEADER_FONT, fontSize: 32, bold: true, color: ACCENT, align: 'left', valign: 'middle'
    });
    s.addText(statLabel, {
      x: 5, y: 5.5, w: 4.3, h: 0.6,
      fontFace: BODY_FONT, fontSize: 14, color: WHITE, align: 'left', valign: 'middle'
    });
  }
  s.addText('代码：' + codeRef, {
    x: 0.5, y: 6.5, w: 9, h: 0.3,
    fontFace: 'Consolas', fontSize: 11, color: NAVY, align: 'left'
  });
}

addTechSlide(1, '反检测与登录态持久化', 'patchright + stealth 抹平浏览器指纹',
  '招投标平台对自动化访问有反爬检测，普通 Playwright 易被识别',
  'patchright + stealth 注入抹平浏览器指纹，storage_state 持久化会话\n千里马 16 cookies 实测通过，401 自动重登',
  '16 个', 'cookies 实测通过',
  'app/core/{anti_detect,session_manager}.py + app/templates/qianlima_login.py');

addTechSlide(2, 'SimHash 去重 + 反幻觉校验', '64位指纹去重 + 原文事实比对',
  '同一公告多平台重复出现；LLM 抽取字段可能编造',
  'SimHash 64 位指纹去重（汉明距离 ≤ 3 视为重复）\n反幻觉校验：金额归一化 + 日期归一化 + 原文事实比对\n找不到出处的字段一律丢弃',
  '≤ 3', '汉明距离阈值',
  'app/processors/{simhash,hallucination_checker}.py');

addTechSlide(3, '证据验证闭环（W2 核心）', '5 级降级匹配 + IoU 评测 + 消融实验',
  'LLM 抽取的证据文本可能多空格/少标点，无法直接定位原文',
  '5 级降级匹配：L1 精确 → L2 去空白 → L3 去标点 → L4 核心子串 → L5 失败标记\n双坐标映射（normalized_index ↔ raw_index）\nIoU 边界质量评测 + A/B/C 三组消融实验（22 篇金标）',
  'unjustified ↓97%', 'A(100%) → C(2.91%)',
  'app/processors/{normalizer,evidence_locator,field_validator}.py + scripts/eval_*.py');

addTechSlide(4, 'BOQ 异常检测', '20 类基准价格库识别报价偏离',
  '低价中标可能存在围标/劣质供货风险，难系统化识别',
  '20 类常见采购品类基准价格库（充电桩/服务器/电脑/交换机等）\n正则提取"数量+单位+品名"和"品名+数量+单位"两种模式\n按市场均价 ±std 判定 underpriced/overpriced/normal',
  '20 类', '基准价格品类',
  'app/processors/boq_engine.py');

addTechSlide(5, '废标风险预警 + 供应商信用评分', '18 条规则 + 三维度加权评分',
  '废标风险难发现；供应商信用评估依赖人工经验',
  '废标风险预警：18 条规则覆盖排他性资质/付款风险/交货期/资质门槛，含否定语境检测\n供应商信用评分：活跃度 30% + 中标率 40% + 偏离度 30% 加权\n已剔除伪造联邦学习代码',
  '18 + 3', '规则 + 评分维度',
  'app/processors/{risk_engine,supplier_risk}.py');

addTechSlide(6, '推送幂等 + 安全防护', 'at-least-once + content_hash + SSRF 三层',
  '金融数据不能漏发也不能重发；外部 URL 请求存在 SSRF 风险',
  '推送幂等：at-least-once 语义 + content_hash 30 分钟去重窗口\nSMTP 实发联调通过（163 邮箱）\nSSRF 三层防护：私网/环回/链路本地 IP 校验\n邮件头注入过滤 + HMAC 签名 + 路径穿越防护 + LIKE 通配符转义',
  '30 分钟', '幂等去重窗口',
  'app/scheduler/push.py + app/core/{email_sender,webhook_sender}.py + app/utils/url_safety.py');

// ========== 第 22 页：测试概览 ==========
{
  const s = pptx.addSlide();
  addSlideBase(s, '05 · 测试与质量保障');
  // 大数字
  s.addText('571', {
    x: 0.5, y: 1.5, w: 5, h: 1.5,
    fontFace: HEADER_FONT, fontSize: 100, bold: true, color: NAVY, align: 'center'
  });
  s.addText('项测试全部通过', {
    x: 0.5, y: 3.0, w: 5, h: 0.5,
    fontFace: BODY_FONT, fontSize: 18, color: GREY, align: 'center'
  });
  // 右侧分类
  const cats = [
    { name: '核心算法测试', count: '~180' },
    { name: '企业级特征测试', count: '~150' },
    { name: 'W2 证据验证测试', count: '~100' },
    { name: '安全/合规测试', count: '~80' },
    { name: '采集器集成测试', count: '~60' },
  ];
  cats.forEach((c, i) => {
    const y = 1.6 + i * 0.7;
    s.addText(c.name, {
      x: 5.8, y, w: 3, h: 0.4,
      fontFace: BODY_FONT, fontSize: 14, color: NAVY, align: 'left'
    });
    s.addText(c.count, {
      x: 9, y, w: 1, h: 0.4,
      fontFace: HEADER_FONT, fontSize: 16, bold: true, color: ACCENT, align: 'right'
    });
  });
  // 覆盖率
  s.addShape(pptx.ShapeType.roundRect, {
    x: 0.5, y: 5.0, w: 9, h: 1.3, fill: { color: NAVY }, line: { color: NAVY }, rectRadius: 0.1
  });
  s.addText('extractor.py 覆盖率 100%', {
    x: 0.7, y: 5.15, w: 5, h: 0.5,
    fontFace: HEADER_FONT, fontSize: 18, bold: true, color: WHITE, align: 'left'
  });
  s.addText('evidence_locator / field_validator / normalizer 全部 95%+', {
    x: 0.7, y: 5.7, w: 8.6, h: 0.5,
    fontFace: BODY_FONT, fontSize: 14, color: ICE, align: 'left'
  });
}

// ========== 第 23 页：W2 证据验证闭环 ==========
{
  const s = pptx.addSlide();
  addSlideBase(s, '05 · W2 证据验证闭环测试');
  s.addText('W2-08 消融实验 A/B/C 三组对比', {
    x: 0.5, y: 1.3, w: 9, h: 0.4,
    fontFace: HEADER_FONT, fontSize: 16, bold: true, color: NAVY, align: 'left'
  });
  // 表格
  const headers = ['指标', 'A 组（无证据）', 'B 组（不验证）', 'C 组（完整）'];
  const rows = [
    ['fields_with_value', '103', '103', '103'],
    ['fields_with_evidence', '0', '103', '103'],
    ['unjustified_rate', '100%', '0%', '2.91%'],
    ['field_precision', '92.13%', '91.34%', '88.98%'],
    ['evidence_precision', '0%', '0%', '100%'],
  ];
  const tableData = [headers, ...rows];
  s.addTable(tableData, {
    x: 0.5, y: 1.8, w: 9, h: 3,
    fontFace: BODY_FONT, fontSize: 12, color: NAVY,
    border: { type: 'solid', color: ICE, pt: 1 },
    align: 'center', valign: 'middle',
    colW: [3, 2, 2, 2]
  });
  // 结论
  s.addText('结论：unjustified_rate A(100%) → C(2.91%)，证据验证显著抑制无依据输出', {
    x: 0.5, y: 5.0, w: 9, h: 0.5,
    fontFace: HEADER_FONT, fontSize: 14, bold: true, color: SUCCESS, align: 'left'
  });
  // W2-09
  s.addText('W2-09 证据定位指标', {
    x: 0.5, y: 5.6, w: 9, h: 0.4,
    fontFace: HEADER_FONT, fontSize: 16, bold: true, color: NAVY, align: 'left'
  });
  s.addText('recall 66.99%  ·  precision 61.98%  ·  iou_avg 0.5962  ·  iou_avg_matched 0.8876  ·  P50/P95=1.0', {
    x: 0.5, y: 6.0, w: 9, h: 0.4,
    fontFace: BODY_FONT, fontSize: 13, color: NAVY, align: 'left'
  });
  s.addText('金标：22 篇 × 6 字段 = 132 字段，模型 deepseek-v4-flash，max_tokens=8000', {
    x: 0.5, y: 6.5, w: 9, h: 0.3,
    fontFace: BODY_FONT, fontSize: 11, color: GREY, align: 'left', italic: true
  });
}

// ========== 第 24 页：企业级工程规范 ==========
{
  const s = pptx.addSlide();
  addSlideBase(s, '05 · 企业级工程规范');
  const rules = [
    '所有中间件 async/await，不用 callback 风格',
    'Docker 多阶段构建 + non-root 用户 + healthcheck',
    'API key 限速用 SHA256 hash 而非明文',
    '环境变量密钥用 secrets.token_hex(32) 生成 64 字符 hex',
    'SMTP 配置支持 465/587/25 三端口',
    'CORS origins 不默认 *，生产环境显式配置域名',
    '附件下载 sanitize 文件名防路径穿越',
    '外部 URL 请求校验私网/环回/链路本地 IP 防 SSRF',
    '定时订阅用 croniter 校验 frequency_cron 表达式',
    '异步函数用 run_in_executor 卸载同步 CPU/IO 任务',
    '增量数据查询用 SQL NOT EXISTS 避免 N+1',
    'LIKE 查询参数转义 %/_/\ 防通配符注入',
    'LLM 语义缓存用 TTLCache 防 memory leak',
    'PushLog 用 add_all() 批量插入',
    '数据库连接池显式配置 pool_size/max_overflow/pool_recycle',
    '单文件 ≤ 300 行',
  ];
  rules.forEach((r, i) => {
    const col = i % 2;
    const row = Math.floor(i / 2);
    const x = 0.5 + col * 4.7;
    const y = 1.4 + row * 0.65;
    s.addShape(pptx.ShapeType.rect, {
      x: x + 0.05, y: y + 0.13, w: 0.12, h: 0.12, fill: { color: ACCENT }, line: { color: ACCENT }
    });
    s.addText(r, {
      x: x + 0.3, y, w: 4.3, h: 0.55,
      fontFace: BODY_FONT, fontSize: 11, color: NAVY, align: 'left', valign: 'middle'
    });
  });
}

// ========== 第 25 页：CI/CD 与部署 ==========
{
  const s = pptx.addSlide();
  addSlideBase(s, '05 · CI/CD 与部署');
  // 部署架构
  s.addText('Docker 一键部署', {
    x: 0.5, y: 1.4, w: 9, h: 0.5,
    fontFace: HEADER_FONT, fontSize: 18, bold: true, color: NAVY, align: 'left'
  });
  const services = [
    { name: 'web', desc: 'FastAPI Web 服务', port: '8000' },
    { name: 'worker', desc: '采集任务 worker', port: '-' },
    { name: 'scheduler', desc: '定时调度器', port: '-' },
  ];
  services.forEach((sv, i) => {
    const x = 0.5 + i * 3.15;
    s.addShape(pptx.ShapeType.roundRect, {
      x, y: 2.1, w: 2.95, h: 1.5, fill: { color: LIGHT_BG }, line: { color: NAVY, width: 2 }, rectRadius: 0.1
    });
    s.addText(sv.name, {
      x: x + 0.2, y: 2.25, w: 2.55, h: 0.4,
      fontFace: HEADER_FONT, fontSize: 16, bold: true, color: NAVY, align: 'center'
    });
    s.addText(sv.desc, {
      x: x + 0.2, y: 2.7, w: 2.55, h: 0.4,
      fontFace: BODY_FONT, fontSize: 12, color: GREY, align: 'center'
    });
    s.addText('PORT: ' + sv.port, {
      x: x + 0.2, y: 3.15, w: 2.55, h: 0.3,
      fontFace: 'Consolas', fontSize: 11, color: ACCENT, align: 'center'
    });
  });
  // APP_ROLE
  s.addText('APP_ROLE 环境变量区分 web/worker 角色', {
    x: 0.5, y: 3.9, w: 9, h: 0.4,
    fontFace: BODY_FONT, fontSize: 13, color: NAVY, align: 'left', italic: true
  });
  // 健康检查
  s.addText('健康检查 + 优雅关闭', {
    x: 0.5, y: 4.4, w: 9, h: 0.4,
    fontFace: HEADER_FONT, fontSize: 16, bold: true, color: NAVY, align: 'left'
  });
  s.addText('• /health 端点 + Docker healthcheck\n• SIGTERM 优雅关闭，等待 in-flight 请求完成\n• Worker 进程崩溃自动重启', {
    x: 0.5, y: 4.9, w: 9, h: 1.2,
    fontFace: BODY_FONT, fontSize: 13, color: NAVY, align: 'left', valign: 'top'
  });
  s.addText('配置：Dockerfile + docker-compose.yml + .env.example', {
    x: 0.5, y: 6.3, w: 9, h: 0.3,
    fontFace: 'Consolas', fontSize: 11, color: NAVY, align: 'left'
  });
}

// ========== 第 26 页：安全合规 ==========
{
  const s = pptx.addSlide();
  addSlideBase(s, '06 · 安全合规');
  // 定位
  s.addShape(pptx.ShapeType.roundRect, {
    x: 0.5, y: 1.4, w: 9, h: 1.0, fill: { color: NAVY }, line: { color: NAVY }, rectRadius: 0.1
  });
  s.addText('决策辅助工具，不提供金融建议，不承担决策责任', {
    x: 0.7, y: 1.55, w: 8.6, h: 0.7,
    fontFace: HEADER_FONT, fontSize: 16, bold: true, color: WHITE, align: 'center', valign: 'middle'
  });
  // 三栏
  const cols = [
    {
      title: '数据合规',
      items: ['ccgp/ggzy 政府公开数据', 'chinabidding 公开数据', '千里马用户授权账号', '不绕过付费墙', '遵守 robots.txt']
    },
    {
      title: '隐私保护',
      items: ['不采集个人隐私', '身份证/手机/住址不采集', '账号密码 storage_state 加密', '日志敏感字段脱敏', '不接入央行征信']
    },
    {
      title: 'AI 反幻觉',
      items: ['5 级降级匹配定位', '原文事实比对', '无依据字段不展示', 'IoU 0.89 边界质量', 'unjustified ↓97%']
    },
  ];
  cols.forEach((c, i) => {
    const x = 0.5 + i * 3.15;
    s.addShape(pptx.ShapeType.roundRect, {
      x, y: 2.6, w: 2.95, h: 3.7, fill: { color: LIGHT_BG }, line: { color: NAVY, width: 1 }, rectRadius: 0.1
    });
    s.addText(c.title, {
      x: x + 0.2, y: 2.75, w: 2.55, h: 0.5,
      fontFace: HEADER_FONT, fontSize: 16, bold: true, color: NAVY, align: 'center'
    });
    s.addShape(pptx.ShapeType.line, {
      x: x + 0.5, y: 3.3, w: 1.95, h: 0, line: { color: ACCENT, width: 2 }
    });
    c.items.forEach((it, j) => {
      const y = 3.5 + j * 0.5;
      s.addShape(pptx.ShapeType.rect, {
        x: x + 0.25, y: y + 0.1, w: 0.1, h: 0.1, fill: { color: ACCENT }, line: { color: ACCENT }
      });
      s.addText(it, {
        x: x + 0.5, y, w: 2.3, h: 0.4,
        fontFace: BODY_FONT, fontSize: 11, color: NAVY, align: 'left', valign: 'middle'
      });
    });
  });
  s.addText('免责声明：报告均标注「AI 生成，仅供参考，决策请人工复核」', {
    x: 0.5, y: 6.5, w: 9, h: 0.3,
    fontFace: BODY_FONT, fontSize: 11, color: GREY, align: 'left', italic: true
  });
}

// ========== 第 27 页：开放复用 ==========
{
  const s = pptx.addSlide();
  addSlideBase(s, '06 · 开放复用价值');
  s.addText('六大可独立复用组件', {
    x: 0.5, y: 1.3, w: 9, h: 0.5,
    fontFace: HEADER_FONT, fontSize: 18, bold: true, color: NAVY, align: 'left'
  });
  const comps = [
    { name: 'Agent 协作框架', desc: '纯 Python async 图，不依赖 langgraph', file: 'app/agents/coordinator.py' },
    { name: 'SimHash 去重', desc: '64 位指纹 + 汉明距离 ≤ 3', file: 'app/processors/simhash.py' },
    { name: '反幻觉校验', desc: '金额/日期归一化 + 原文事实比对', file: 'app/processors/hallucination_checker.py' },
    { name: 'BOQ 基准价格库', desc: '20 类采购品类 + ±std 预警', file: 'app/processors/boq_engine.py' },
    { name: '证据定位引擎', desc: '5 级降级匹配 + 双坐标映射', file: 'app/processors/evidence_locator.py' },
    { name: '浏览器池 + 登录态', desc: '反检测 + storage_state 持久化', file: 'app/core/{browser_pool,session_manager}.py' },
  ];
  comps.forEach((c, i) => {
    const col = i % 2;
    const row = Math.floor(i / 2);
    const x = 0.5 + col * 4.7;
    const y = 1.9 + row * 1.5;
    s.addShape(pptx.ShapeType.roundRect, {
      x, y, w: 4.4, h: 1.3, fill: { color: LIGHT_BG }, line: { color: NAVY, width: 1 }, rectRadius: 0.1
    });
    s.addText(c.name, {
      x: x + 0.2, y: y + 0.15, w: 4, h: 0.4,
      fontFace: HEADER_FONT, fontSize: 14, bold: true, color: NAVY, align: 'left'
    });
    s.addText(c.desc, {
      x: x + 0.2, y: y + 0.55, w: 4, h: 0.4,
      fontFace: BODY_FONT, fontSize: 11, color: GREY, align: 'left'
    });
    s.addText(c.file, {
      x: x + 0.2, y: y + 0.95, w: 4, h: 0.3,
      fontFace: 'Consolas', fontSize: 10, color: ACCENT, align: 'left'
    });
  });
  s.addText('开源协议：Apache License 2.0', {
    x: 0.5, y: 6.5, w: 9, h: 0.3,
    fontFace: HEADER_FONT, fontSize: 13, bold: true, color: NAVY, align: 'left'
  });
}

// ========== 第 28 页：路线图 ==========
{
  const s = pptx.addSlide();
  addSlideBase(s, '07 · 路线图');
  const phases = [
    { phase: '初赛', time: '7.16 - 8.16', status: '当前', color: ACCENT, items: ['作品简介 500 字', '方案 PPT 28 页', '合规边界声明', 'Demo 视频录制'] },
    { phase: '复赛', time: '8.25 - 9.23', status: '规划', color: NAVY, items: ['WebUI 完善 + 多轮交互', '知识库/RAG 接入', 'Demo 视频升级', '代码开源准备'] },
    { phase: '决赛', time: '9.22 - 9.23', status: '目标', color: GREY, items: ['路演 PPT', '现场 Demo', '杭州线下', '最终工程材料'] },
  ];
  // W4 演进规划条带（React 升级 + SaaS 商用）
  s.addShape(pptx.ShapeType.roundRect, {
    x: 0.5, y: 6.5, w: 9, h: 0.4, fill: { color: ICE }, line: { color: NAVY, width: 1 }, rectRadius: 0.05
  });
  s.addText('W4 演进规划：前端升级 React + TypeScript + Vite，支持多页面路由和状态管理，为 SaaS 商用做准备；evidence API 补齐，前端切回真实接口', {
    x: 0.7, y: 6.55, w: 8.6, h: 0.3,
    fontFace: BODY_FONT, fontSize: 11, color: NAVY, align: 'left', italic: true
  });
  phases.forEach((p, i) => {
    const x = 0.5 + i * 3.15;
    s.addShape(pptx.ShapeType.roundRect, {
      x, y: 1.4, w: 2.95, h: 5, fill: { color: LIGHT_BG }, line: { color: p.color, width: 2 }, rectRadius: 0.1
    });
    // 阶段名
    s.addShape(pptx.ShapeType.roundRect, {
      x: x + 0.2, y: 1.55, w: 1.5, h: 0.4, fill: { color: p.color }, line: { color: p.color }, rectRadius: 0.05
    });
    s.addText(p.phase, {
      x: x + 0.2, y: 1.6, w: 1.5, h: 0.3,
      fontFace: HEADER_FONT, fontSize: 13, bold: true, color: WHITE, align: 'center'
    });
    // 时间
    s.addText(p.time, {
      x: x + 0.2, y: 2.1, w: 2.55, h: 0.4,
      fontFace: HEADER_FONT, fontSize: 14, bold: true, color: NAVY, align: 'center'
    });
    // 状态
    s.addText(p.status, {
      x: x + 0.2, y: 2.55, w: 2.55, h: 0.3,
      fontFace: BODY_FONT, fontSize: 11, color: GREY, align: 'center', italic: true
    });
    // 分隔线
    s.addShape(pptx.ShapeType.line, {
      x: x + 0.5, y: 3.05, w: 1.95, h: 0, line: { color: p.color, width: 1 }
    });
    // 任务
    p.items.forEach((it, j) => {
      const y = 3.3 + j * 0.6;
      s.addShape(pptx.ShapeType.rect, {
        x: x + 0.25, y: y + 0.1, w: 0.1, h: 0.1, fill: { color: p.color }, line: { color: p.color }
      });
      s.addText(it, {
        x: x + 0.5, y, w: 2.3, h: 0.5,
        fontFace: BODY_FONT, fontSize: 11, color: NAVY, align: 'left', valign: 'middle'
      });
    });
  });
}

// ========== 第 29 页：团队 ==========
{
  const s = pptx.addSlide();
  addSlideBase(s, '07 · 团队');
  // 团队名
  s.addText('标小智', {
    x: 0.5, y: 1.2, w: 9, h: 0.9,
    fontFace: HEADER_FONT, fontSize: 54, bold: true, color: NAVY, align: 'center'
  });
  s.addText('智汇标讯 · AI+金融', {
    x: 0.5, y: 2.1, w: 9, h: 0.4,
    fontFace: HEADER_FONT, fontSize: 18, color: GREY, align: 'center', italic: true
  });
  // 分割
  s.addShape(pptx.ShapeType.rect, {
    x: 4, y: 2.7, w: 2, h: 0.05, fill: { color: ACCENT }, line: { color: ACCENT }
  });
  // 两位成员卡片（左右排列）
  const members = [
    {
      name: '徐浚钊',
      school: '上海建桥学院 · 计算机科学与技术 · 大二',
      role: '项目负责人 / 全栈开发',
      x: 0.5, w: 4.4,
      duties: ['架构设计 + 六 Agent 实现', '后端 API + 数据库', '前端 Demo + 路演材料']
    },
    {
      name: '王祯明',
      school: '上海建桥学院 · 计算机科学与技术 · 大二',
      role: '数据标注 / 质量测试',
      x: 5.1, w: 4.4,
      duties: ['金标数据标注', '测试用例验证', '质量回归复核']
    },
  ];
  members.forEach((m) => {
    const yBase = 3.0;
    // 卡片
    s.addShape(pptx.ShapeType.roundRect, {
      x: m.x, y: yBase, w: m.w, h: 3.4, fill: { color: LIGHT_BG }, line: { color: NAVY, width: 2 }, rectRadius: 0.1
    });
    // 姓名
    s.addText(m.name, {
      x: m.x + 0.2, y: yBase + 0.15, w: m.w - 0.4, h: 0.5,
      fontFace: HEADER_FONT, fontSize: 26, bold: true, color: NAVY, align: 'center'
    });
    // 学校专业
    s.addText(m.school, {
      x: m.x + 0.2, y: yBase + 0.7, w: m.w - 0.4, h: 0.35,
      fontFace: BODY_FONT, fontSize: 11, color: GREY, align: 'center'
    });
    // 角色标签
    s.addShape(pptx.ShapeType.roundRect, {
      x: m.x + (m.w - 2.4) / 2, y: yBase + 1.15, w: 2.4, h: 0.4, fill: { color: NAVY }, line: { color: NAVY }, rectRadius: 0.05
    });
    s.addText(m.role, {
      x: m.x + (m.w - 2.4) / 2, y: yBase + 1.2, w: 2.4, h: 0.3,
      fontFace: HEADER_FONT, fontSize: 12, bold: true, color: WHITE, align: 'center'
    });
    // 分隔线
    s.addShape(pptx.ShapeType.line, {
      x: m.x + 0.5, y: yBase + 1.75, w: m.w - 1.0, h: 0, line: { color: ACCENT, width: 1 }
    });
    // 职责
    m.duties.forEach((d, j) => {
      const y = yBase + 1.95 + j * 0.4;
      s.addShape(pptx.ShapeType.rect, {
        x: m.x + 0.3, y: y + 0.1, w: 0.1, h: 0.1, fill: { color: ACCENT }, line: { color: ACCENT }
      });
      s.addText(d, {
        x: m.x + 0.5, y, w: m.w - 0.7, h: 0.35,
        fontFace: BODY_FONT, fontSize: 11, color: NAVY, align: 'left', valign: 'middle'
      });
    });
  });
  // 底部 AI 协作工具条
  s.addShape(pptx.ShapeType.roundRect, {
    x: 0.5, y: 6.55, w: 9, h: 0.35, fill: { color: ICE }, line: { color: NAVY, width: 1 }, rectRadius: 0.05
  });
  s.addText('AI 协作：GLM 5.2 (Trae) · GPT-5.6 Sol / Claude Fable 5 · 豆包 Turbo · 豆包审查 Prompt', {
    x: 0.7, y: 6.6, w: 8.6, h: 0.25,
    fontFace: BODY_FONT, fontSize: 10, color: NAVY, align: 'center', italic: true
  });
}

// ========== 第 30 页：致谢（实际第 28 页，无页脚） ==========
{
  const s = pptx.addSlide();
  s.background = { color: NAVY };
  // 主文字
  s.addText('Thank You', {
    x: 0.5, y: 2.5, w: 9, h: 1.5,
    fontFace: HEADER_FONT, fontSize: 72, bold: true, color: WHITE, align: 'center'
  });
  // 金色分割
  s.addShape(pptx.ShapeType.rect, {
    x: 4.5, y: 4.0, w: 1, h: 0.05, fill: { color: ACCENT }, line: { color: ACCENT }
  });
  // 副文字
  s.addText('BidAgent · 智能标讯助手', {
    x: 0.5, y: 4.3, w: 9, h: 0.6,
    fontFace: HEADER_FONT, fontSize: 24, color: ICE, align: 'center'
  });
  // 联系方式
  s.addText('徐浚钊  ·  王祯明  ·  标小智  ·  13566878907@163.com', {
    x: 0.5, y: 5.2, w: 9, h: 0.4,
    fontFace: BODY_FONT, fontSize: 14, color: ICE, align: 'center'
  });
  s.addText('GOAI 世界人工智能开源大赛 · AI+金融方向  ·  2026', {
    x: 0.5, y: 5.7, w: 9, h: 0.4,
    fontFace: BODY_FONT, fontSize: 12, color: GREY, align: 'center'
  });
  // GitHub
  s.addText('github.com/tlyyxjz/BidAgent', {
    x: 0.5, y: 6.5, w: 9, h: 0.4,
    fontFace: 'Consolas', fontSize: 14, color: ACCENT, align: 'center'
  });
}

// 保存
const outPath = process.argv[2] || 'proposal.pptx';
pptx.writeFile({ fileName: outPath }).then(fn => {
  const stats = fs.statSync(fn);
  console.log(`[OK] PPT 已生成: ${fn}`);
  console.log(`     大小: ${(stats.size / 1024).toFixed(1)} KB`);
  console.log(`     页数: ${TOTAL_SLIDES}`);
});
