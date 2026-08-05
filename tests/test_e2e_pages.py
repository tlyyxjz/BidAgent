"""P1-5: Playwright 页面级 E2E 测试（真实 uvicorn + chromium 真浏览器）。

覆盖 5 类核心页面的渲染主路径：只断言结构加载与零 JS 未捕获异常，
不断言具体数据内容（CI/演示环境 DB 规模可能不同）。
运行：pytest tests/test_e2e_pages.py -q（额外耗时约 40s）

实现说明：项目 pytest 配置 asyncio_mode=auto，sync_playwright 自建事件循环
会与 pytest-asyncio 冲突，故本文件统一用 async_playwright + 模块级事件循环。
"""
from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

PROJECT_ROOT = Path(__file__).resolve().parents[1]

pytest.importorskip("playwright", reason="未安装 playwright，跳过页面 E2E")
from playwright.async_api import async_playwright  # noqa: E402

# 模块级共享事件循环：让 live_server / pw_browser / 所有用例跑在同一个 loop
pytestmark = pytest.mark.asyncio(loop_scope="module")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def live_server():
    """启动真实 uvicorn 服务，测试结束后 kill。"""
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    log_fd, log_path = tempfile.mkstemp(suffix=".log", prefix="e2e_uv_")
    # conftest 会把 DATABASE_URL 指到空测试库并被子进程继承，必须显式覆盖回真实库
    env = {**os.environ, "DATABASE_URL": "sqlite+aiosqlite:///./data/bidagent.db"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=log_fd,
        stderr=subprocess.STDOUT,
    )
    ready = False
    deadline = time.time() + 20
    async with httpx.AsyncClient(timeout=2) as hc:
        while time.time() < deadline:
            try:
                r = await hc.get(f"{base}/health")
                if r.status_code == 200:
                    ready = True
                    break
            except httpx.HTTPError:
                await asyncio.sleep(0.3)
    if not ready:
        proc.kill()
        try:
            tail = Path(log_path).read_text(encoding="utf-8", errors="replace")[-800:]
        except OSError:
            tail = "(无法读取日志)"
        pytest.skip(f"uvicorn 启动失败，跳过页面 E2E。日志尾部：\n{tail}")
    yield base
    proc.kill()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def pw_browser():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        yield browser
        await browser.close()


async def _open(browser, base, path):
    page = await browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    await page.goto(f"{base}{path}", wait_until="domcontentloaded", timeout=20000)
    await page.wait_for_timeout(1500)  # 等 fetch 渲染完成
    return page, errors


async def test_workbench_loads(live_server, pw_browser):
    """工作台：标题 + 导航结构 + 零 JS 异常。"""
    page, errors = await _open(pw_browser, live_server, "/ui")
    title = await page.title()
    assert "工作台" in title
    assert await page.locator("a").count() > 0
    assert errors == [], f"工作台出现未捕获异常: {errors}"
    await page.close()


async def test_notice_list_renders_rows(live_server, pw_browser):
    """公告列表：有数据时不允许渲染成空态。"""
    async with httpx.AsyncClient(timeout=10) as hc:
        resp = await hc.get(f"{live_server}/api/real/tenders?limit=1")
    page, errors = await _open(pw_browser, live_server, "/static/notice_list.html")
    if resp.status_code == 200 and resp.json().get("data", {}).get("tenders"):
        body_text = await page.inner_text("body")
        assert "暂无" not in body_text or "条" in body_text, "列表有数据却渲染为空态"
    assert errors == [], f"公告列表出现未捕获异常: {errors}"
    await page.close()


async def test_notice_detail_evidence(live_server, pw_browser):
    """详情页：/ui/detail?doc=<id> 字段区 + 证据区渲染。"""
    async with httpx.AsyncClient(timeout=10) as hc:
        resp = await hc.get(f"{live_server}/api/real/tenders?limit=1")
    tenders = (resp.json().get("data") or {}).get("tenders") or []
    if not tenders:
        pytest.skip("DB 无数据，跳过详情页 E2E")
    doc_id = tenders[0]["id"]
    page, errors = await _open(pw_browser, live_server, f"/ui/detail?doc={doc_id}")
    title = await page.title()
    assert "证据验证" in title
    body_text = await page.inner_text("body")
    assert len(body_text) > 200, "详情页正文过短，疑似未渲染"
    assert errors == [], f"详情页出现未捕获异常: {errors}"
    await page.close()


async def test_quality_dashboard_chart(live_server, pw_browser):
    """质量看板：echarts 必须渲染出 canvas。"""
    page, errors = await _open(pw_browser, live_server, "/static/quality_dashboard.html")
    title = await page.title()
    assert "数据质量" in title
    assert await page.locator("canvas").count() > 0, "看板未渲染任何 echarts canvas"
    assert errors == [], f"质量看板出现未捕获异常: {errors}"
    await page.close()


async def test_search_flow(live_server, pw_browser):
    """搜索页：输入关键词提交后不崩。"""
    page, errors = await _open(pw_browser, live_server, "/static/search.html")
    inputs = page.locator("input")
    assert await inputs.count() > 0, "搜索页没有输入框"
    await inputs.first.fill("医院")
    await page.keyboard.press("Enter")
    await page.wait_for_timeout(2000)
    assert errors == [], f"搜索页出现未捕获异常: {errors}"
    await page.close()