"""标小智 Demo 浏览器自动化演示脚本（场景 2-9）。

用法：
    1. 启动后端：cd <仓库根目录> && uvicorn app.main:app --port 8000
    2. 打开 OBS 录屏
    3. 运行：python demo_auto_play.py
"""
import asyncio
import sys

from playwright.async_api import async_playwright

BASE = "http://localhost:8000"

async def wait(msg, seconds):
    print(f"  ⏳ {msg} ({seconds}s)")
    await asyncio.sleep(seconds)

async def scene_02_workbench(page):
    print("\n[场景2] 工作台首页")
    await page.goto(f"{BASE}/ui", wait_until="networkidle")
    await wait("页面加载", 3)
    print("  → 鼠标滑过 KPI 卡片")
    kpi_cards = page.locator(".kpi-card, .stat-card, [class*='kpi']").all()
    for i in range(min(4, len(kpi_cards))):
        await kpi_cards[i].scroll_into_view_if_needed()
        await kpi_cards[i].hover()
        await asyncio.sleep(1.5)
    print("  → 滚动到 Pipeline 流程图")
    await page.evaluate("window.scrollTo({top: document.body.scrollHeight*0.3, behavior:'smooth'})")
    await wait("展示 Pipeline", 3)
    print("  → 滚动到四大能力卡片")
    await page.evaluate("window.scrollTo({top: document.body.scrollHeight*0.6, behavior:'smooth'})")
    await wait("展示能力卡片", 4)

async def scene_03_search(page):
    print("\n[场景3] 招标检索")
    await page.goto(f"{BASE}/ui/search", wait_until="networkidle")
    await wait("页面加载", 2)
    print("  → 输入搜索词")
    search_input = page.locator("input[placeholder*='上海'], input[type='text'], #searchInput").first
    await search_input.click()
    await asyncio.sleep(0.5)
    await search_input.type("北京教育", delay=100)
    await wait("展示输入", 2)
    try:
        btn = page.locator("button:has-text('检索'), button:has-text('搜索'), #searchBtn").first
        await btn.click()
        await wait("搜索结果加载", 3)
    except:
        await wait("自动搜索", 3)
    print("  → 滚动展示结果")
    await page.evaluate("window.scrollTo({top: 400, behavior:'smooth'})")
    await wait("展示结果列表", 4)

async def scene_04_dedup(page):
    print("\n[场景4] 跨平台去重")
    await page.goto(f"{BASE}/ui/notice-list", wait_until="networkidle")
    await wait("页面加载", 2)
    print("  → 点击中标筛选")
    try:
        el = page.locator("button:has-text('中标'), a:has-text('中标'), [data-type='award']").first
        await el.scroll_into_view_if_needed()
        await asyncio.sleep(1)
        await el.click()
        await wait("展示中标列表", 3)
    except:
        print("  (跳过筛选点击)")
    print("  → 滚动展示列表")
    await page.evaluate("window.scrollTo({top: 300, behavior:'smooth'})")
    await wait("展示列表", 4)

async def scene_05_evidence(page):
    print("\n[场景5] 证据验证（核心）")
    await page.goto(f"{BASE}/ui/detail?id=1", wait_until="networkidle")
    await wait("页面加载", 4)
    print("  → 等待字段加载")
    await wait("字段加载", 3)
    print("  → 点击金额字段")
    try:
        selectors = ["text=金额", "[data-field='amount']", ".field-card:has-text('金额')", "div:has-text('金额')"]
        clicked = False
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    await el.scroll_into_view_if_needed()
                    await asyncio.sleep(1)
                    await el.click()
                    clicked = True
                    break
            except:
                continue
        if clicked:
            await wait("原文高亮", 4)
        else:
            print("  (未找到金额字段)")
    except:
        print("  (点击失败)")
    print("  → 滚动展示原文")
    await page.evaluate("document.querySelector('.raw-text, #rawText, .source-text')?.scrollIntoView({behavior:'smooth'})")
    await wait("展示原文高亮", 5)

async def scene_06_org(page):
    print("\n[场景6] 组织画像")
    await page.goto(f"{BASE}/ui/org?name=北京大学第三医院", wait_until="networkidle")
    await wait("页面加载", 4)
    print("  → 等待雷达图渲染")
    await wait("雷达图渲染", 3)
    print("  → 滚动展示观察信号")
    await page.evaluate("window.scrollTo({top: 400, behavior:'smooth'})")
    await wait("展示观察信号", 5)
    await page.evaluate("window.scrollTo({top: 800, behavior:'smooth'})")
    await wait("展示更多", 4)

async def scene_07_quality(page):
    print("\n[场景7] 质量评测")
    await page.goto(f"{BASE}/ui/quality-dashboard", wait_until="networkidle")
    await wait("页面加载", 4)
    print("  → 展示 KPI")
    await wait("KPI 展示", 4)
    print("  → 滚动到消融对比图")
    await page.evaluate("window.scrollTo({top: 400, behavior:'smooth'})")
    await wait("展示消融图", 5)
    print("  → 滚动到 Bootstrap CI")
    await page.evaluate("window.scrollTo({top: document.body.scrollHeight*0.7, behavior:'smooth'})")
    await wait("展示 Bootstrap CI", 5)

async def scene_08_versions(page):
    print("\n[场景8] 版本历史")
    await page.goto(f"{BASE}/ui/versions?id=1", wait_until="networkidle")
    await wait("页面加载", 3)
    print("  → 等待谱系加载")
    await wait("谱系加载", 4)
    print("  → 滚动展示哈希")
    await page.evaluate("window.scrollTo({top: 300, behavior:'smooth'})")
    await wait("展示哈希", 3)

async def scene_09_chat(page):
    print("\n[场景9] 智能问答")
    await page.goto(f"{BASE}/ui/chat", wait_until="networkidle")
    await wait("页面加载", 3)
    print("  → 输入查询")
    try:
        msg_input = page.locator("#msgInput, input[placeholder*='输入'], textarea").first
        await msg_input.click()
        await asyncio.sleep(0.5)
        await msg_input.type("北京教育 最近30天", delay=100)
        await wait("展示输入", 2)
        send_btn = page.locator("button:has-text('发送'), #sendBtn, button[type='submit']").first
        await send_btn.click()
        print("  → 等待 6 Agent 流水线")
        await wait("Agent 流水线运行", 8)
    except:
        print("  (输入失败，仅展示页面)")
        await wait("展示页面", 8)

async def main():
    print("=" * 60)
    print("标小智 Demo 浏览器自动化演示")
    print("场景 2-9，总时长约 133 秒")
    print("=" * 60)
    print("\n⚠️  请确认：")
    print("  1. 后端服务已启动（http://localhost:8000）")
    print("  2. OBS 已开始录屏")
    print("  3. 浏览器将自动打开并全屏")
    print("\n3 秒后开始...")
    await asyncio.sleep(3)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--start-maximized"])
        context = await browser.new_context(viewport={"width": 1920, "height": 1080}, locale="zh-CN")
        page = await context.new_page()
        try:
            await scene_02_workbench(page)
            await scene_03_search(page)
            await scene_04_dedup(page)
            await scene_05_evidence(page)
            await scene_06_org(page)
            await scene_07_quality(page)
            await scene_08_versions(page)
            await scene_09_chat(page)
            print("\n" + "=" * 60)
            print("✅ 全部 8 个场景演示完成！现在可以停止 OBS 录屏")
            print("=" * 60)
            await wait("展示结束页面", 5)
        except KeyboardInterrupt:
            print("\n\n⚠️ 用户中断")
        except Exception as e:
            print(f"\n❌ 错误: {e}")
            import traceback
            traceback.print_exc()
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
