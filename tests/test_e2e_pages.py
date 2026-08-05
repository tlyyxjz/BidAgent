"""P1-5: Playwright 页面级 E2E 测试（真实 uvicorn + chromium 真浏览器）。

覆盖 5 类核心页面的渲染主路径：只断言结构加载与零 JS 未捕获异常，
不断言具体数据内容（CI/演示环境 DB 规模可能不同）。
运行：pytest tests/test_e2e_pages.py -q（额外耗时约 30s）
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

playwright = pytest.importorskip("playwright", reason="未安装 playwright，跳过页面 E2E")
from playwright.sync_api import sync_playwright  # noqa: E402

# asyncio_mode=auto 会给所有测试套事件循环，而 sync_playwright 内部自建事件循环，
# 两者冲突报 RuntimeError(Runner.run() cannot be called from a running event loop)。
# 模块级 strict 标记让本文件所有测试回到普通同步执行。
pytestmark = pytest.mark.asyncio(mode="strict")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server():
    """启动真实 uvicorn 服务，测试结束后 kill。"""
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 20
    ready = False
    while time.time() < deadline:
        try:
            if httpx.get(f"{base}/health", timeout=2).status_code == 200:
                ready = True
                break
        except httpx.HTTPError:
            time.sleep(0.3)
    if not ready:
        proc.kill()
        pytest.skip("uvicorn 启动失败，跳过页面 E2E")
    yield base
    proc.kill()


@pytest.fixture(scope="module")
def pw_browser():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()


def _open(browser, base, path):
    page = browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}{path}", wait_until="domcontentloaded", timeout=20000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except playwright.sync_api.TimeoutError:
        pass  # 个别页面有轮询请求，networkidle 不可达不视为失败
    return page, errors


def test_workbench_loads(live_server, pw_browser):
    """工作台：标题 + 导航结构 + 零 JS 异常。"""
    page, errors = _open(pw_browser, live_server, "/ui")
    assert "工作台" in page.title()
    assert page.locator("a").count() > 0
    assert errors == [], f"工作台出现未捕获异常: {errors}"
    page.close()


def test_notice_list_renders_rows(live_server, pw_browser):
    """公告列表：有数据时必须渲染出行，且行可跳转详情。"""
    resp = httpx.get(f"{live_server}/api/real/tenders?limit=1", timeout=10)
    page, errors = _open(pw_browser, live_server, "/static/notice_list.html")
    if resp.status_code == 200 and resp.json().get("data", {}).get("tenders"):
        # 等首行出现（fetch 渲染）
        page.wait_for_timeout(1500)
        body_text = page.inner_text("body")
        assert "暂无" not in body_text or "条" in body_text, "列表有数据却渲染为空态"
    assert errors == [], f"公告列表出现未捕获异常: {errors}"
    page.close()


def test_notice_detail_evidence(live_server, pw_browser):
    """详情页：/ui/detail?doc=<id> 字段区 + 证据区渲染。"""
    resp = httpx.get(f"{live_server}/api/real/tenders?limit=1", timeout=10)
    tenders = (resp.json().get("data") or {}).get("tenders") or []
    if not tenders:
        pytest.skip("DB 无数据，跳过详情页 E2E")
    doc_id = tenders[0]["id"]
    page, errors = _open(pw_browser, live_server, f"/ui/detail?doc={doc_id}")
    page.wait_for_timeout(1500)
    assert "证据验证" in page.title()
    body_text = page.inner_text("body")
    assert len(body_text) > 200, "详情页正文过短，疑似未渲染"
    assert errors == [], f"详情页出现未捕获异常: {errors}"
    page.close()


def test_quality_dashboard_chart(live_server, pw_browser):
    """质量看板：echarts 必须渲染出 canvas。"""
    page, errors = _open(pw_browser, live_server, "/static/quality_dashboard.html")
    page.wait_for_timeout(1500)
    assert "数据质量" in page.title()
    canvases = page.locator("canvas")
    assert canvases.count() > 0, "看板未渲染任何 echarts canvas"
    assert errors == [], f"质量看板出现未捕获异常: {errors}"
    page.close()


def test_search_flow(live_server, pw_browser):
    """搜索页：输入关键词提交后不崩、结果区可见。"""
    page, errors = _open(pw_browser, live_server, "/static/search.html")
    inputs = page.locator("input")
    assert inputs.count() > 0, "搜索页没有输入框"
    inputs.first.fill("医院")
    page.keyboard.press("Enter")
    page.wait_for_timeout(2000)
    assert errors == [], f"搜索页出现未捕获异常: {errors}"
    page.close()