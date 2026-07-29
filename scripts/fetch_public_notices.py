"""W3 金标扩充抓取脚本

按 v4.1 第十章 10.1 节扩充金标数据集：
- 开发集/校准集用，K3 作为标注员 B（v4.1 10.4 允许预标注）
- 覆盖类型：招标/中标/更正
- 频率：≤1 次/8 秒（约束 #14）
- 超时：15 秒
- 403 立即停止（约束 #15）

URL 来源：ccgp.gov.cn 首页导航实测（2026-07-28）
  招标: /cggg/zygg/gkzb/  + /cggg/dfgg/gkzb/
  中标: /cggg/zygg/zbgg/  + /cggg/dfgg/zbgg/
  更正: /cggg/zygg/gzgg/  + /cggg/dfgg/gzgg/

输出：_w3_raw/ 目录，每篇一个 .txt 文件
"""
from __future__ import annotations

import argparse
import re
import time
import urllib.request
from pathlib import Path
from html import unescape

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "_w3_raw"
OUT_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# ccgp 实测可用 URL（2026-07-28 从首页导航获取）
LIST_URLS = [
    ("zy_gkzb", "http://www.ccgp.gov.cn/cggg/zygg/gkzb/"),    # 中央招标
    ("zy_zbgg", "http://www.ccgp.gov.cn/cggg/zygg/zbgg/"),    # 中央中标
    ("zy_gzgg", "http://www.ccgp.gov.cn/cggg/zygg/gzgg/"),    # 中央更正
    ("df_gkzb", "http://www.ccgp.gov.cn/cggg/dfgg/gkzb/"),    # 地方招标
    ("df_zbgg", "http://www.ccgp.gov.cn/cggg/dfgg/zbgg/"),    # 地方中标
    ("df_gzgg", "http://www.ccgp.gov.cn/cggg/dfgg/gzgg/"),    # 地方更正
]


def fetch_url(url: str, timeout: int = 15) -> str | None:
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 403:
                print(f"  403 Forbidden: {url}", flush=True)
                return None
            body = resp.read().decode("utf-8", errors="replace")
            return body
    except Exception as e:
        print(f"  ERROR {type(e).__name__}: {e}", flush=True)
        return None


def parse_list_page(html: str, list_url: str) -> list[tuple[str, str]]:
    """ccgp 实际结构：<a href="./202607/t20260729_27026540.htm">标题</a>"""
    items = []
    pattern = re.compile(
        r'<a[^>]*href="([^"]*?t\d+_\d+\.htm[^"]*)"[^>]*>([^<]+)</a>',
        re.IGNORECASE,
    )
    base = list_url.rstrip("/")
    seen = set()
    for m in pattern.finditer(html):
        href, title = m.group(1), m.group(2).strip()
        if not title or href in seen:
            continue
        seen.add(href)
        if href.startswith("./"):
            href = base + "/" + href[2:]
        elif href.startswith("/"):
            href = "http://www.ccgp.gov.cn" + href
        elif not href.startswith("http"):
            href = base + "/" + href
        items.append((title, href))
    return items


def clean_html_to_text(html: str) -> str:
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<p[^>]*>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</p>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<div[^>]*>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<li[^>]*>", "\n- ", html, flags=re.IGNORECASE)
    html = re.sub(r"<h[1-6][^>]*>", "\n## ", html, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", html)
    text = unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_detail(url: str, timeout: int = 15) -> str | None:
    html = fetch_url(url, timeout)
    if not html:
        return None
    return clean_html_to_text(html)


def classify_notice(title: str, category: str) -> str:
    if "更正" in title or "gzgg" in category:
        return "correction"
    if "中标" in title or "结果" in title or "zbgg" in category:
        return "award"
    if "招标" in title or "gkzb" in category:
        return "tender"
    return "other"


def main():
    parser = argparse.ArgumentParser(description="抓取 ccgp 公告用于 W3 金标扩充")
    parser.add_argument("--per-list", type=int, default=15, help="每个列表页抓取条数")
    parser.add_argument("--delay", type=int, default=8, help="请求间隔秒数（约束#14）")
    parser.add_argument("--timeout", type=int, default=15, help="请求超时秒数")
    args = parser.parse_args()

    print(f"=== W3 金标扩充抓取 ===", flush=True)
    print(f"输出目录: {OUT_DIR}", flush=True)
    print(f"每个列表抓取: {args.per_list} 条", flush=True)
    print(f"请求间隔: {args.delay} 秒", flush=True)
    print(f"超时: {args.timeout} 秒", flush=True)
    print(flush=True)

    all_items: list[tuple[str, str, str]] = []

    print("=== 第一步：抓取列表页 ===", flush=True)
    for category, list_url in LIST_URLS:
        print(f"[{category}] {list_url}", flush=True)
        html = fetch_url(list_url, args.timeout)
        if not html:
            print(f"  列表页抓取失败，跳过", flush=True)
            continue
        items = parse_list_page(html, list_url)
        print(f"  解析到 {len(items)} 条", flush=True)
        for title, url in items[:args.per_list]:
            ntype = classify_notice(title, category)
            all_items.append((title, url, ntype))
        time.sleep(args.delay)

    print(f"\n列表页总计: {len(all_items)} 条", flush=True)
    by_type = {}
    for _, _, t in all_items:
        by_type[t] = by_type.get(t, 0) + 1
    for t, c in sorted(by_type.items()):
        print(f"  {t}: {c}", flush=True)

    print(f"\n=== 第二步：抓取详情页（间隔 {args.delay}s）===", flush=True)
    success = 0
    failed = 0
    for i, (title, url, ntype) in enumerate(all_items, 1):
        fname = f"w3_{ntype}_{i:03d}.txt"
        fpath = OUT_DIR / fname
        if fpath.exists():
            print(f"[{i}/{len(all_items)}] SKIP (已存在): {fname}", flush=True)
            continue

        print(f"[{i}/{len(all_items)}] {ntype}: {title[:40]}...", flush=True)
        text = fetch_detail(url, args.timeout)
        if not text:
            print(f"  FAIL，跳过", flush=True)
            failed += 1
            time.sleep(args.delay)
            continue

        # 过滤太短的（可能是空页面）
        if len(text) < 200:
            print(f"  SKIP (内容太短 {len(text)} 字符)", flush=True)
            failed += 1
            time.sleep(args.delay)
            continue

        content = f"# {title}\n# URL: {url}\n# Type: {ntype}\n# Fetched: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n{text}"
        fpath.write_text(content, encoding="utf-8")
        print(f"  OK: {len(text)} 字符 → {fname}", flush=True)
        success += 1
        time.sleep(args.delay)

    print(f"\n=== 抓取完成 ===", flush=True)
    print(f"成功: {success}", flush=True)
    print(f"失败: {failed}", flush=True)
    print(f"总计: {len(all_items)}", flush=True)
    print(f"输出目录: {OUT_DIR}", flush=True)

    files = list(OUT_DIR.glob("w3_*.txt"))
    type_count = {}
    for f in files:
        parts = f.stem.split("_")
        if len(parts) >= 2:
            t = parts[1]
            type_count[t] = type_count.get(t, 0) + 1
    print(f"\n类型分布:", flush=True)
    for t, c in sorted(type_count.items()):
        print(f"  {t}: {c}", flush=True)


if __name__ == "__main__":
    main()
