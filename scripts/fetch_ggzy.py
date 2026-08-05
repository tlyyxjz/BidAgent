"""ggzy.gov.cn 公告抓取脚本

v4.1 §5.1 要求 ggzy_national 适配器。
选择器参数化，实测后通过 --list-selector 等参数调整。

前置步骤: 先运行 scripts/probe_ggzy_dom.py 实测页面DOM结构

用法:
  # 列表页模式
  python scripts/fetch_ggzy.py --max-pages 3 --per-list 20
  # URL清单模式
  python scripts/fetch_ggzy.py --urls-file ggzy_urls.txt
  # 自定义选择器（probe_ggzy_dom.py输出后）
  python scripts/fetch_ggzy.py --list-selector "ul.news-list li" --link-selector "a[href*='dealInfo']"
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from html import unescape
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fetch_public_notices import clean_html_to_text  # noqa: E402  复用清洗逻辑

DEFAULT_LIST_URL = "http://deal.ggzy.gov.cn/ds/deal/dealList.html"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


class StopFetching(Exception):
    """403 等致命错误，立即停止抓取（约束 #15）。"""


def check_robots(url: str) -> bool:
    """调用 app/core/robots_checker.py 检查是否允许抓取。不可达时默认放行。"""
    if not url:
        return True
    try:
        from app.core.robots_checker import robots_checker
        return asyncio.run(robots_checker.is_allowed(url, user_agent=USER_AGENT))
    except Exception as e:
        print(f"  [robots] 检查异常 {type(e).__name__}: {e}，默认放行", flush=True)
        return True


def fetch_url_with_playwright(url: str, timeout: int) -> tuple[str, bool]:
    """用 Playwright 打开页面，返回 (html, js_rendered=True)。403 抛 StopFetching。"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            if response is not None and response.status == 403:
                raise StopFetching(f"403 Forbidden: {url}")
            try: page.wait_for_load_state("networkidle", timeout=timeout * 1000)
            except Exception: pass
            page.wait_for_timeout(1500)
            return page.content(), True
        finally:
            browser.close()


def crawl_list_playwright(list_url: str, max_pages: int, per_list: int, list_selector: str,
                          link_selector: str, next_selector: str, timeout: int) -> list[tuple[str, str]]:
    """用 Playwright 抓列表页并翻页，返回 [(title, url)]。"""
    from playwright.sync_api import sync_playwright
    items: list[tuple[str, str]] = []
    seen: set[str] = set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            response = page.goto(list_url, wait_until="domcontentloaded", timeout=timeout * 1000)
            if response is not None and response.status == 403:
                raise StopFetching(f"403 Forbidden: {list_url}")
            try: page.wait_for_load_state("networkidle", timeout=timeout * 1000)
            except Exception: pass
            page.wait_for_timeout(2000)
            for pi in range(1, max_pages + 1):
                rows = page.query_selector_all(list_selector)
                page_items = 0
                for row in rows:
                    link = row.query_selector(link_selector) or (row if row.evaluate("el => el.tagName") == "A" else None)
                    if link is None:
                        continue
                    href = link.get_attribute("href") or ""
                    title = (link.inner_text() or "").strip()
                    if not href or href in seen:
                        continue
                    seen.add(href)
                    items.append((title, urljoin(list_url, href)))
                    page_items += 1
                    if page_items >= per_list:
                        break
                print(f"  page {pi}: 解析到 {page_items} 条", flush=True)
                if pi >= max_pages:
                    break
                nxt = page.query_selector(next_selector)
                if nxt is None:
                    print("  无下一页控件，停止翻页", flush=True)
                    break
                nxt.click()
                page.wait_for_timeout(1500)
        finally:
            browser.close()
    return items


def fetch_url_with_httpx(url: str, timeout: int) -> tuple[str, bool]:
    """httpx 获取原始 HTML（无 JS 渲染）。403 抛 StopFetching。"""
    import httpx
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True) as client:
        resp = client.get(url)
        if resp.status_code == 403:
            raise StopFetching(f"403 Forbidden: {url}")
        resp.raise_for_status()
        return resp.text, False


def parse_list_from_html(html: str, link_selector: str, base_url: str) -> list[tuple[str, str]]:
    """从 HTML 解析列表项（httpx fallback）：从 link_selector 提取 href 关键词做正则匹配。"""
    m = re.search(r'href\*=["\']([^"\']+)["\']', link_selector, re.IGNORECASE)
    keyword = m.group(1) if m else "dealInfo"
    items: list[tuple[str, str]] = []
    seen: set[str] = set()
    pattern = re.compile(r'<a[^>]*href="([^"]*?' + re.escape(keyword) + r'[^"]*)"[^>]*>([^<]+)</a>', re.IGNORECASE)
    for mm in pattern.finditer(html):
        href = mm.group(1)
        title = unescape(mm.group(2)).strip()
        if not title or href in seen:
            continue
        seen.add(href)
        items.append((title, urljoin(base_url, href)))
    return items


def classify_notice(title: str, url: str) -> str:
    """含 jypt/招标→tender；含 jggs/中标/结果→award；含 更正→correction。"""
    text = f"{url} {title}".lower()
    if "更正" in title: return "correction"
    if "中标" in title or "结果" in title or "jggs" in text: return "award"
    if "招标" in title or "jypt" in text: return "tender"
    return "other"


def parse_urls_file(filepath: Path) -> list[tuple[str, str, str]]:
    """每行 `type|url` 或 `url`（type 可选，自动推断）。# 开头为注释。"""
    items: list[tuple[str, str, str]] = []
    for raw in filepath.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "|" in line:
            ntype, url = line.split("|", 1)
            ntype, url = ntype.strip().lower(), url.strip()
            if ntype not in ("tender", "award", "correction", "other"):
                ntype = classify_notice("", url)
        else:
            url, ntype = line, classify_notice("", line)
        if url:
            items.append(("", url, ntype))
    return items


def get_next_index(out_dir: Path, prefix: str, ntype: str) -> int:
    """断点续抓：该类型下一个可用索引（已有文件最大值 + 1）。"""
    nums = []
    for f in out_dir.glob(f"{prefix}_{ntype}_*.txt"):
        try: nums.append(int(f.stem.rsplit("_", 1)[-1]))
        except ValueError: pass
    return (max(nums) if nums else 0) + 1


def save_detail(title: str, url: str, ntype: str, idx: int, out_dir: Path,
                prefix: str, timeout: int, use_playwright: bool) -> bool:
    """抓取单篇详情并保存，返回是否成功。"""
    html, js_ok = None, False
    try:
        if use_playwright:
            html, js_ok = fetch_url_with_playwright(url, timeout)
        else:
            html, js_ok = fetch_url_with_httpx(url, timeout)
    except StopFetching:
        raise
    except Exception as e:
        print(f"  ERROR {type(e).__name__}: {e}", flush=True)
        return False
    if not title:
        m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
        title = m.group(1).strip() if m else "untitled"
    text = clean_html_to_text(html)
    if not js_ok:
        text = f"[注意: 无JS渲染，内容可能不完整]\n\n{text}"
    if len(text) < 200:
        print(f"  SKIP (内容太短 {len(text)} 字符)", flush=True)
        return False
    fname = f"{prefix}_{ntype}_{idx:03d}.txt"
    content = f"# {title}\n# URL: {url}\n# Type: {ntype}\n# Fetched: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n{text}"
    (out_dir / fname).write_text(content, encoding="utf-8")
    print(f"  OK: {len(text)} 字符 → {fname}", flush=True)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="ggzy.gov.cn 公告抓取脚本（v4.1 §5.1 ggzy_national 适配器）")
    parser.add_argument("--list-url", default=DEFAULT_LIST_URL, help=f"列表页URL（默认 {DEFAULT_LIST_URL}）")
    parser.add_argument("--max-pages", type=int, default=3, help="翻页数（默认3）")
    parser.add_argument("--per-list", type=int, default=20, help="每页抓取条数（默认20）")
    parser.add_argument("--delay", type=int, default=8, help="请求间隔秒（默认8）")
    parser.add_argument("--timeout", type=int, default=30, help="超时秒（默认30）")
    parser.add_argument("--out-dir", default="_w4_raw", help="输出目录（默认_w4_raw）")
    parser.add_argument("--prefix", default="w4", help="文件名前缀（默认w4）")
    parser.add_argument("--list-selector", default="ul.news-list li", help="列表项CSS选择器（probe_ggzy_dom.py实测后调整）")
    parser.add_argument("--link-selector", default='a[href*="dealInfo"]', help="详情链接CSS选择器")
    parser.add_argument("--next-selector", default="a.next-page", help="下一页CSS选择器")
    parser.add_argument("--urls-file", default=None, help="URL清单文件（每行一个URL），使用此模式时跳过列表页")
    args = parser.parse_args()

    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"=== ggzy.gov.cn 公告抓取 ===\n输出目录: {out_dir}  列表选择器: {args.list_selector}  链接选择器: {args.link_selector}", flush=True)

    use_playwright = True
    try:
        import playwright  # noqa: F401
    except ImportError:
        use_playwright = False
        print("[fallback] Playwright 不可用，改用 httpx（无JS渲染，可能拿不到完整内容）", flush=True)

    items: list[tuple[str, str, str]] = []
    check_target = ""
    if args.urls_file:
        urls_path = Path(args.urls_file)
        if not urls_path.is_absolute(): urls_path = ROOT / args.urls_file
        if not urls_path.exists():
            print(f"URL清单不存在: {urls_path}", flush=True)
            return
        items = parse_urls_file(urls_path)
        print(f"URL清单模式: {urls_path} → {len(items)} 条", flush=True)
        check_target = items[0][1] if items else ""
    else:
        print("\n=== 第一步：抓取列表页 ===", flush=True)
        if not check_robots(args.list_url):
            print(f"robots.txt 禁止抓取: {args.list_url}", flush=True)
            return
        try:
            if use_playwright:
                raw_items = crawl_list_playwright(args.list_url, args.max_pages, args.per_list,
                                                  args.list_selector, args.link_selector, args.next_selector, args.timeout)
            else:
                html, _ = fetch_url_with_httpx(args.list_url, args.timeout)
                raw_items = parse_list_from_html(html, args.link_selector, args.list_url)
        except StopFetching as e:
            print(f"致命错误，停止: {e}", flush=True)
            return
        for title, url in raw_items:
            items.append((title, url, classify_notice(title, url)))

    if check_target and not check_robots(check_target):
        print(f"robots.txt 禁止抓取: {check_target}", flush=True)
        return

    print(f"\n列表总计: {len(items)} 条", flush=True)
    by_type: dict[str, int] = {}
    for _, _, t in items: by_type[t] = by_type.get(t, 0) + 1
    for t, c in sorted(by_type.items()): print(f"  {t}: {c}", flush=True)

    print(f"\n=== 第二步：抓取详情页（间隔 {args.delay}s）===", flush=True)
    type_counters = {nt: get_next_index(out_dir, args.prefix, nt) for nt in set(t for _, _, t in items)}
    success = failed = skipped = 0
    total = len(items)
    for i, (title, url, ntype) in enumerate(items, 1):
        idx = type_counters[ntype]
        fpath = out_dir / f"{args.prefix}_{ntype}_{idx:03d}.txt"
        if fpath.exists():
            print(f"[{i}/{total}] SKIP (已存在): {fpath.name}", flush=True)
            type_counters[ntype] += 1; skipped += 1; continue
        print(f"[{i}/{total}] {ntype}: {(title or url)[:50]}", flush=True)
        try:
            ok = save_detail(title, url, ntype, idx, out_dir, args.prefix, args.timeout, use_playwright)
        except StopFetching as e:
            print(f"致命错误，立即停止: {e}", flush=True)
            break
        success += 1 if ok else 0
        failed += 0 if ok else 1
        type_counters[ntype] += 1
        time.sleep(args.delay)

    print(f"\n=== 抓取完成 ===\n成功: {success}  失败: {failed}  跳过: {skipped}  总计: {total}\n输出目录: {out_dir}", flush=True)


if __name__ == "__main__":
    main()
