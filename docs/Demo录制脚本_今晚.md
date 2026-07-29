# BidAgent Demo 录制脚本（今晚用）

> **录制时间**：2026-07-28 晚
> **时长目标**：90-120 秒
> **核心卖点**：可验证证据链（字段→证据→原文追溯）

## 一、启动准备

```powershell
cd C:\Users\Lenovo\Desktop\BidAgent
$env:SECRET_KEY="a"*64
$env:ADMIN_SECRET="test-admin-secret-12345"
$env:DATABASE_URL="sqlite+aiosqlite:///./data/bidagent.db"
$env:REDIS_URL="redis://localhost:6379/0"
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 二、录制流程（90 秒）

| 时间 | 操作 | 画面 | 旁白/字幕 |
|------|------|------|-----------|
| 0-10s | 打开浏览器，访问 http://localhost:8000/ui/evidence | 页面加载，显示公告列表 | "BidAgent 智能标讯助手，可验证招投标数据引擎" |
| 10-25s | 在公告列表中选择 "award_05" | 显示 5 篇真实公告，选中 award_05 | "5 篇真实招投标公告，38 个字段，42 条证据" |
| 25-45s | 点击 "winner_name" 字段 | 原文高亮中标人证据（黄色） | "点击字段，原文证据自动高亮，支持多段证据" |
| 45-65s | 点击 "amount" 字段 | 展示金额证据 + support_level=direct | "每个字段都标注支持度：direct/equivalent/inferred" |
| 65-80s | 点击无证据字段 | 显示"系统已拒绝展示"（灰色） | "找不到原文证据的字段，系统拒绝展示，杜绝 AI 幻觉" |
| 80-95s | 切换到 multi_lot_02 公告 | 展示多分包多证据 | "多分包公告，每个分包独立证据链" |
| 95-110s | 打开 org_profile.html | 90 天活跃度 SVG 柱状图 | "组织画像：中标活跃度、Top3 采购人集中度" |
| 110-120s | 打开 version_history.html | 版本时间线 + SHA256 | "版本历史：每次抓取都有 SHA256 指纹，可追溯" |

## 三、结尾字幕（5 秒）

```
BidAgent 智能标讯助手
字段 → 证据 → 原文 → 来源 → 版本
完整可验证追溯链

团队：标小智
赛事：GOAI 2026 · AI+金融方向
GitHub: github.com/tlyyxjz/BidAgent
```

## 四、核心数据（录制时如实提及）

- 5 篇真实公告 / 38 字段 / 42 证据
- 22 篇金标评测：recall 69.90%, precision 60.63%, IoU avg 0.5307
- A/B/C 三组消融：unjustified_rate 从 100% 降至 1.94%
- field_precision 94.49%（C 组）
- 571 项测试通过（K3 独立复跑确认）

## 五、录制注意事项

1. **用真实数据，不用 Mock**：主用 /ui/evidence 页面
2. **高亮黄色是证据区间**：click 字段后自动滚动到原文位置
3. **灰色字段是反幻觉展示**：系统拒绝展示无证据字段
4. **录屏分辨率**：1366×768 或 1920×1080
5. **录屏工具**：OBS / Bandicam / Windows 自带 Win+G
6. **输出文件名**：BidAgent_Demo_90s.mp4

## 六、备用页面（如主页面出问题）

| 页面 | URL | 用途 |
|------|-----|------|
| 公告详情 | http://localhost:8000/static/notice_detail.html?doc=1 | 字段卡片+多段证据 |
| 版本历史 | http://localhost:8000/static/version_history.html?source=1 | SHA256+change_type |
| 组织画像 | http://localhost:8000/static/org_profile.html?org=1 | 90天活跃度+Top3采购人 |
