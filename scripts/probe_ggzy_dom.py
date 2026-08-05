"""ggzy.gov.cn DOM 结构探测脚本

v4.1 §5.1 要求 ggzy_national 适配器，但 app/templates/ggzy.py 选择器为推测值。
本脚本用 Playwright 实测页面 DOM，输出建议的选择器配置。

用法:
  python scripts/probe_ggzy_dom.py
  python scripts/probe_ggzy_dom.py --url http://deal.ggzy.gov.cn/ds/deal/dealList.html
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

DEFAULT_URL = "http://deal.ggzy.gov.cn/ds/deal/dealList.html"


def extract_charset(html: str) -> str:
    """从 HTML 中提取 charset。"""
    m = re.search(r'charset=["\']?([\w-]+)', html, re.IGNORECASE)
    return m.group(1) if m else "未知"


def probe_with_playwright(url: str, timeout: int) -> dict:
    """用 Playwright sync API 打开页面并分析 DOM。"""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        page.wait_for_timeout(2000)  # 等 JS 渲染

        title = page.title()
        html = page.content()

        # 用 JS 收集结构化信息
        info = page.evaluate(
            """
            () => {
              const detailLinks = [...document.querySelectorAll(
                'a[href*="dealInfo"], a[href*="notice"], a[href*="detail"]'
              )].slice(0, 10).map(a => ({
                href: a.href, text: (a.innerText || '').trim().slice(0, 80)
              }));

              const ulLists = [...document.querySelectorAll('ul, ol')].map(ul => ({
                tag: ul.tagName.toLowerCase(),
                cls: typeof ul.className === 'string' ? ul.className : '',
                liCount: ul.querySelectorAll('li').length
              })).filter(x => x.liCount > 0);

              const tables = [...document.querySelectorAll('table')].map(t => ({
                cls: typeof t.className === 'string' ? t.className : '',
                trCount: t.querySelectorAll('tr').length
              }));

              const pagers = [...document.querySelectorAll(
                'a.next, .page-next, a.next-page, a[href*="page"]'
              )].slice(0, 5).map(a => ({
                cls: a.className,
                text: (a.innerText || '').trim().slice(0, 40),
                href: a.getAttribute('href') || ''
              }));

              // 取前3个列表项 HTML
              let items = [];
              const firstLi = document.querySelector('ul li, ol li');
              const firstTr = document.querySelector('table tr');
              if (firstLi) {
                items = [...document.querySelectorAll('ul li, ol li')].slice(0, 3)
                  .map(li => li.outerHTML.slice(0, 500));
              } else if (firstTr) {
                items = [...document.querySelectorAll('table tr')].slice(1, 4)
                  .map(tr => tr.outerHTML.slice(0, 500));
              }

              return { detailLinks, ulLists, tables, pagers, items };
            }
            """
        )
        browser.close()

    return {
        "title": title,
        "charset": extract_charset(html),
        "detail_links": [(d["text"], d["href"]) for d in info["detailLinks"]],
        "ul_lists": [(u["tag"], u["cls"], u["liCount"]) for u in info["ulLists"]],
        "tables": [(t["cls"], t["trCount"]) for t in info["tables"]],
        "pagers": info["pagers"],
        "item_snippets": info["items"],
        "js_rendered": True,
    }


def probe_with_httpx(url: str, timeout: int) -> dict:
    """fallback: 用 httpx 获取原始 HTML（无 JS 渲染）。"""
    import httpx

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36",
    }
    resp = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    html = resp.text

    # 标题
    title_m = re.search(r"<title[^>]*>([^<]*)</title>", html, re.IGNORECASE)
    title = title_m.group(1).strip() if title_m else "(无标题)"

    # 含 dealInfo/notice/detail 的链接
    detail_links = re.findall(
        r'<a[^>]*href="([^"]*(?:dealInfo|notice|detail)[^"]*)"[^>]*>([^<]*)</a>',
        html, re.IGNORECASE,
    )

    # ul/ol 列表 li 数量
    ul_lists = []
    for m in re.finditer(r'<(ul|ol)[^>]*class="([^"]*)"[^>]*>(.*?)</\1>', html, re.IGNORECASE | re.DOTALL):
        tag, cls, inner = m.group(1), m.group(2), m.group(3)
        li_n = len(re.findall(r"<li[^>]*>", inner, re.IGNORECASE))
        if li_n > 0:
            ul_lists.append((tag, cls, li_n))

    # table tr 数量
    tables = []
    for m in re.finditer(r'<table[^>]*class="([^"]*)"[^>]*>(.*?)</table>', html, re.IGNORECASE | re.DOTALL):
        cls, inner = m.group(1), m.group(2)
        tr_n = len(re.findall(r"<tr[^>]*>", inner, re.IGNORECASE))
        if tr_n > 0:
            tables.append((cls, tr_n))

    # 分页控件
    pagers = []
    for m in re.finditer(
        r'<a[^>]*(?:class="[^"]*(?:next|page|pager)[^"]*"|href="[^"]*page[^"]*")[^>]*>([^<]*)</a>',
        html, re.IGNORECASE,
    ):
        pagers.append({"text": m.group(1).strip(), "cls": "", "href": ""})

    return {
        "title": title,
        "charset": extract_charset(html),
        "detail_links": detail_links,
        "ul_lists": ul_lists,
        "tables": tables,
        "pagers": pagers,
        "item_snippets": [],
        "js_rendered": False,
        "html_preview": html[:5000],
    }


def build_report(url: str, result: dict) -> str:
    """构建分析报告文本。"""
    lines = []
    lines.append("=== GGZY DOM 分析报告 ===")
    lines.append(f"URL: {url}")
    lines.append(f"标题: {result['title']}")
    lines.append(f"charset: {result['charset']}")
    lines.append(f"是否需要JS渲染: {'是' if result['js_rendered'] else '否（仅原始HTML）'}")

    # 列表项选择器建议
    ul_lists = result.get("ul_lists", [])
    tables = result.get("tables", [])
    if ul_lists:
        best = max(ul_lists, key=lambda x: x[-1])
        cls = best[1] if best[1] else "news-list"
        list_sel = f"ul.{cls} li"
    elif tables:
        list_sel = "table tr"
    else:
        list_sel = "(未检测到列表)"

    lines.append(f"列表项选择器建议: {list_sel}")

    item_count = max(
        (x[-1] for x in ul_lists if x[-1] > 0),
        default=max((x[-1] for x in tables), default=0),
    )
    lines.append(f"列表项数量: {item_count}")

    detail_links = result.get("detail_links", [])
    if detail_links:
        lines.append(f'详情链接选择器: a[href*="dealInfo"]  (检测到 {len(detail_links)} 个)')
    else:
        lines.append("详情链接选择器: (未检测到 dealInfo/notice/detail 链接)")

    pagers = result.get("pagers", [])
    if pagers:
        lines.append(f"翻页选择器: a.next-page  (检测到 {len(pagers)} 个分页元素)")
    else:
        lines.append("翻页选择器: (未检测到分页控件)")

    # 前3个列表项HTML
    lines.append("")
    lines.append("=== 前3个列表项HTML ===")
    snippets = result.get("item_snippets", [])
    if snippets:
        for i, s in enumerate(snippets, 1):
            lines.append(f"--- 项 {i} ---")
            lines.append(s)
    else:
        lines.append("(Playwright 不可用或未检测到列表项)")

    # 建议 ggzy.py 配置
    lines.append("")
    lines.append("=== 建议 app/templates/ggzy.py 配置 ===")
    wait_sel = list_sel if list_sel != "(未检测到列表)" else "body"
    lines.append("GGZY_TEMPLATE = ScrapeTemplate(")
    lines.append('    name="ggzy",')
    lines.append('    selectors={')
    lines.append('        "title": "a, h3, .title",')
    lines.append('        "publish_time": ".time, .date, span.time",')
    lines.append('        "detail_url": "a",')
    lines.append('        "content": ".content, .summary, p",')
    lines.append('    },')
    lines.append(f'    list_selector="{list_sel}",')
    lines.append(f'    wait_for_selector="{wait_sel}",')
    lines.append('    next_page_selector="a.next-page",')
    lines.append('    max_pages=1,')
    lines.append(')')

    # HTML 预览（仅 fallback 模式）
    if not result["js_rendered"]:
        lines.append("")
        lines.append("=== HTML 前 5000 字符 ===")
        lines.append(result.get("html_preview", ""))

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ggzy.gov.cn DOM 结构探测脚本"
    )
    parser.add_argument("--url", default=DEFAULT_URL, help=f"要探测的 ggzy 页面 URL（默认 {DEFAULT_URL}）")
    parser.add_argument("--timeout", type=int, default=30, help="超时秒（默认30）")
    parser.add_argument("--output", type=str, default=None, help="输出 DOM 分析报告到文件（可选）")
    args = parser.parse_args()

    print("=== ggzy DOM 探测 ===", flush=True)
    print(f"URL: {args.url}", flush=True)
    print(f"超时: {args.timeout}s", flush=True)
    print(flush=True)

    try:
        result = probe_with_playwright(args.url, args.timeout)
        print("[Playwright] 页面已加载", flush=True)
    except ImportError:
        print("[fallback] Playwright 不可用，改用 httpx 获取原始 HTML（无JS渲染）", flush=True)
        result = probe_with_httpx(args.url, args.timeout)
    except Exception as e:
        print(f"[fallback] Playwright 失败 ({type(e).__name__}: {e})，改用 httpx", flush=True)
        result = probe_with_httpx(args.url, args.timeout)

    report = build_report(args.url, result)
    print(report, flush=True)

    if args.output:
        out_path = Path(args.output)
        if not out_path.is_absolute():
            out_path = Path(__file__).resolve().parent.parent / args.output
        out_path.write_text(report, encoding="utf-8")
        print(f"\n报告已保存: {out_path}", flush=True)


if __name__ == "__main__":
    main()
