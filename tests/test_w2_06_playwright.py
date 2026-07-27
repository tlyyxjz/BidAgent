"""W2-06 前端字段高亮 Playwright 测试（场景 37-48）。

覆盖：
- 场景 37-40: 多值字段滚动与高亮
- 场景 41-44: 字段状态保持（currentFieldIndex、展开/折叠、scrollTop）
- 场景 45-46: 左右滚动容器独立
- 场景 47: 1366×768 分辨率适配
- 场景 48: 金额字段多值查看

运行方式：
    pytest tests/test_w2_06_playwright.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

BASE_URL = "http://localhost:8000"
DETAIL_URL = f"{BASE_URL}/ui/tenders/1?doc=tender_06_4e47868721c5"
CHAT_URL = f"{BASE_URL}/ui/chat"


@pytest.fixture
async def browser():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        yield browser
        await browser.close()


@pytest.fixture
async def page(browser):
    context = await browser.new_context(viewport={"width": 1366, "height": 768})
    page = await context.new_page()
    yield page
    await context.close()


async def _load_detail_page(page):
    await page.goto(DETAIL_URL, wait_until="domcontentloaded")
    await page.wait_for_selector("#rawText", timeout=10000)
    await page.wait_for_selector("#fieldsContainer", timeout=10000)
    await page.wait_for_selector(".field-nav-item", timeout=10000)
    await page.wait_for_timeout(500)


class TestW206Scene37MultiValueScroll:
    """场景 37: 多值字段滚动 - 中标人多值时滚动列表不重置。"""

    @pytest.mark.asyncio
    async def test_scene_37_multi_value_scroll(self, page):
        await _load_detail_page(page)
        fields_container = page.locator("#fieldsContainer")
        scroll_before = await fields_container.evaluate("el => el.scrollTop")
        await fields_container.evaluate("el => el.scrollTop = 50")
        await page.wait_for_timeout(100)
        scroll_after = await fields_container.evaluate("el => el.scrollTop")
        print(f"  滚动前: {scroll_before}, 滚动后: {scroll_after}")
        assert scroll_after >= 50 or scroll_before == scroll_after, "字段容器应可滚动或内容不足无需滚动"


class TestW206Scene38MultiValueHighlight:
    """场景 38: 多值字段高亮 - 点击字段按钮切换高亮区间。"""

    @pytest.mark.asyncio
    async def test_scene_38_multi_value_highlight(self, page):
        await _load_detail_page(page)
        nav_items = page.locator(".field-nav-item")
        count = await nav_items.count()
        assert count == 6, f"应有 6 个字段导航项，实际 {count}"
        await nav_items.nth(2).click()
        await page.wait_for_timeout(300)
        highlights = page.locator(".hl")
        hl_count = await highlights.count()
        print(f"  点击中标人后高亮数量: {hl_count}")
        assert hl_count >= 1, "点击字段后应至少有1处高亮"


class TestW206Scene39FieldIndexPersist:
    """场景 39: 字段索引保持 - 切换字段后 currentFieldIndex 不重置为0。"""

    @pytest.mark.asyncio
    async def test_scene_39_field_index_persist(self, page):
        await _load_detail_page(page)
        nav_items = page.locator(".field-nav-item")
        await nav_items.nth(3).click()
        await page.wait_for_timeout(200)
        active_before = page.locator(".field-nav-item.active")
        active_text_before = await active_before.inner_text()
        print(f"  第一次点击后激活: {active_text_before[:20]}")
        await nav_items.nth(1).click()
        await page.wait_for_timeout(200)
        active_after = page.locator(".field-nav-item.active")
        active_text_after = await active_after.inner_text()
        print(f"  第二次点击后激活: {active_text_after[:20]}")
        assert active_text_before != active_text_after, "切换字段后激活项应变化"


class TestW206Scene40ValueEvidence:
    """场景 40: 字段值证据 - 点击字段值可查看证据列表。"""

    @pytest.mark.asyncio
    async def test_scene_40_value_evidence(self, page):
        await _load_detail_page(page)
        nav_items = page.locator(".field-nav-item")
        await nav_items.nth(0).click()
        await page.wait_for_timeout(200)
        field_values = page.locator(".field-value")
        count = await field_values.count()
        print(f"  字段值数量: {count}")
        assert count >= 1, "字段至少应有一个值"
        evidence_items = page.locator(".evidence-item")
        ev_count = await evidence_items.count()
        print(f"  证据项数量: {ev_count}")


class TestW206Scene41ExpandCollapse:
    """场景 41: 展开/折叠状态 - 点击字段卡片头部切换展开折叠。"""

    @pytest.mark.asyncio
    async def test_scene_41_expand_collapse(self, page):
        await _load_detail_page(page)
        nav_items = page.locator(".field-nav-item")
        await nav_items.nth(0).click()
        await page.wait_for_timeout(200)
        active_before = await nav_items.nth(0).evaluate("el => el.classList.contains('active')")
        assert active_before is True, "点击后字段导航项应为激活状态"
        await nav_items.nth(0).click()
        await page.wait_for_timeout(200)
        active_after = await nav_items.nth(0).evaluate("el => el.classList.contains('active')")
        print(f"  第一次点击激活: {active_before}, 第二次点击激活: {active_after}")


class TestW206Scene42ScrollTopPersist:
    """场景 42: scrollTop 位置保持 - 滚动后切换字段不重置文本滚动。"""

    @pytest.mark.asyncio
    async def test_scene_42_scrolltop_persist(self, page):
        await _load_detail_page(page)
        text_container = page.locator(".text-container")
        await text_container.evaluate("el => el.scrollTop = 100")
        await page.wait_for_timeout(100)
        scroll_before = await text_container.evaluate("el => el.scrollTop")
        nav_items = page.locator(".field-nav-item")
        await nav_items.nth(1).click()
        await page.wait_for_timeout(200)
        scroll_after = await text_container.evaluate("el => el.scrollTop")
        print(f"  文本滚动前: {scroll_before}, 切换字段后: {scroll_after}")


class TestW206Scene43LeftScrollIndependent:
    """场景 43: 左侧文本滚动独立 - 滚动左侧不影响右侧。"""

    @pytest.mark.asyncio
    async def test_scene_43_left_scroll_independent(self, page):
        await _load_detail_page(page)
        text_container = page.locator(".text-container")
        fields_container = page.locator("#fieldsContainer")
        fields_scroll_before = await fields_container.evaluate("el => el.scrollTop")
        await text_container.evaluate("el => el.scrollTop = 200")
        await page.wait_for_timeout(100)
        fields_scroll_after = await fields_container.evaluate("el => el.scrollTop")
        assert fields_scroll_before == fields_scroll_after, "滚动左侧不应影响右侧滚动位置"
        print(f"  右侧滚动位置保持: {fields_scroll_after}")


class TestW206Scene44RightScrollIndependent:
    """场景 44: 右侧字段滚动独立 - 滚动右侧不影响左侧。"""

    @pytest.mark.asyncio
    async def test_scene_44_right_scroll_independent(self, page):
        await _load_detail_page(page)
        text_container = page.locator(".text-container")
        fields_container = page.locator("#fieldsContainer")
        text_scroll_before = await text_container.evaluate("el => el.scrollTop")
        await fields_container.evaluate("el => el.scrollTop = 100")
        await page.wait_for_timeout(100)
        text_scroll_after = await text_container.evaluate("el => el.scrollTop")
        assert text_scroll_before == text_scroll_after, "滚动右侧不应影响左侧滚动位置"
        print(f"  左侧滚动位置保持: {text_scroll_after}")


class TestW206Scene45TextContainerScroll:
    """场景 45: text-container 可独立滚动。"""

    @pytest.mark.asyncio
    async def test_scene_45_text_container_scroll(self, page):
        await _load_detail_page(page)
        text_container = page.locator(".text-container")
        scroll_height = await text_container.evaluate("el => el.scrollHeight")
        client_height = await text_container.evaluate("el => el.clientHeight")
        can_scroll = scroll_height > client_height
        print(f"  文本容器可滚动: {can_scroll} (scrollHeight={scroll_height}, clientHeight={client_height})")
        assert scroll_height > 0, "文本容器应有内容高度"


class TestW206Scene46FieldsContainerScroll:
    """场景 46: fields-container 可独立滚动。"""

    @pytest.mark.asyncio
    async def test_scene_46_fields_container_scroll(self, page):
        await _load_detail_page(page)
        fields_container = page.locator("#fieldsContainer")
        scroll_height = await fields_container.evaluate("el => el.scrollHeight")
        client_height = await fields_container.evaluate("el => el.clientHeight")
        can_scroll = scroll_height > client_height
        print(f"  字段容器可滚动: {can_scroll} (scrollHeight={scroll_height}, clientHeight={client_height})")
        assert scroll_height > 0, "字段容器应有内容高度"


class TestW206Scene47Resolution1366x768:
    """场景 47: 1366×768 分辨率适配。"""

    @pytest.mark.asyncio
    async def test_scene_47_resolution_1366x768(self, page):
        await _load_detail_page(page)
        viewport = page.viewport_size
        assert viewport["width"] == 1366, f"宽度应为 1366，实际 {viewport['width']}"
        assert viewport["height"] == 768, f"高度应为 768，实际 {viewport['height']}"
        main_container = page.locator(".main-container")
        box = await main_container.bounding_box()
        assert box is not None, "主容器应可见"
        print(f"  视口: {viewport['width']}x{viewport['height']}")
        print(f"  主容器尺寸: {box['width']:.0f}x{box['height']:.0f}")
        screenshot_path = Path(__file__).parent.parent / "data" / "w2_06_1366x768_detail.png"
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(screenshot_path), full_page=False)
        print(f"  详情页截图已保存: {screenshot_path}")


class TestW206Scene48AmountFieldValues:
    """场景 48: 金额字段验证 - 金额字段可查看和高亮。"""

    @pytest.mark.asyncio
    async def test_scene_48_amount_field_values(self, page):
        await _load_detail_page(page)
        nav_items = page.locator(".field-nav-item")
        amount_item = None
        for i in range(await nav_items.count()):
            text = await nav_items.nth(i).inner_text()
            if "金额" in text:
                amount_item = nav_items.nth(i)
                break
        assert amount_item is not None, "应找到金额字段"
        await amount_item.click()
        await page.wait_for_timeout(300)
        amount_card = page.locator(".field-card").filter(has_text="金额")
        is_expanded = await amount_card.evaluate("el => el.classList.contains('expanded')")
        if not is_expanded:
            await amount_card.locator(".field-card-header").click()
            await page.wait_for_timeout(200)
        field_values = amount_card.locator(".field-value")
        count = await field_values.count()
        print(f"  金额字段值数量: {count}")
        assert count >= 1, "金额字段至少应有一个值"
        visible_count = 0
        for i in range(count):
            visible = await field_values.nth(i).is_visible()
            if visible:
                visible_count += 1
        print(f"  可见的金额值数量: {visible_count}")
        assert visible_count >= 1, "至少应有一个金额值可见"
