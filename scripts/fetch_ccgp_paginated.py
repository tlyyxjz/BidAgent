"""ccgp 翻页扩展抓取脚本

v4.1 §10.1 金标数据集扩充（目标300+篇）
- 支持列表页翻页（index_N.htm）
- 支持 URL 列表定点抓取（--urls-file）
- 支持自动清洗（复用 clean_w3_notices 逻辑）
- 断点续抓（已存在文件自动跳过）

频率: ≤1次/8秒  超时: 15秒  403立即停止
输出: _w4_raw/ 目录
"""
from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

from fetch_public_notices import (
    fetch_url,
    parse_list_page,
    clean_html_to_text,
    classify_notice,
)
ROOT = Path(__file__).resolve().parent.parent
LIST_URLS = [
    ("zy_gkzb", "http://www.ccgp.gov.cn/cggg/zygg/gkzb/"),    # 中央招标
    ("zy_zbgg", "http://www.ccgp.gov.cn/cggg/zygg/zbgg/"),    # 中央中标
    ("zy_gzgg", "http://www.ccgp.gov.cn/cggg/zygg/gzgg/"),    # 中央更正
    ("df_gkzb", "http://www.ccgp.gov.cn/cggg/dfgg/gkzb/"),    # 地方招标
    ("df_zbgg", "http://www.ccgp.gov.cn/cggg/dfgg/zbgg/"),    # 地方中标
    ("df_gzgg", "http://www.ccgp.gov.cn/cggg/dfgg/gzgg/"),    # 地方更正
]

# === 内联 clean_w3_notices.clean_content（避免硬编码路径依赖） ===
BODY_START_RE = re.compile(r"^##\s+", re.MULTILINE)
BODY_END_MARKERS = ["\n相关公告\n", "\n主办单位：", "\n网站标识码：", "\n相关公告"]
NOISE_LINE_PATTERNS = [
    re.compile(r"^财政部唯一指定"),
    re.compile(r"^服务热线"),
    re.compile(r"^服务投诉"),
    re.compile(r"^当前位置"),
    re.compile(r"^(首页|政采法规|购买服务|监督检查|信息公告|国际专栏|中央公告|政采公告|地方公告|招标公告|中标公告|更正公告)$"),
    re.compile(r"^- ?$"),
    re.compile(r"^京ICP备"),
    re.compile(r"^京公网安备"),
    re.compile(r"^版权所有"),
    re.compile(r"^联系我们"),
    re.compile(r"^意见反馈"),
    re.compile(r"^网站标识码"),
    re.compile(r"^主办单位"),
    re.compile(r"^© \d{4}"),
]


def clean_content(content: str) -> str:
    """清洗正文，剥离 ccgp 模板噪声（内联自 clean_w3_notices）。"""
    start_match = BODY_START_RE.search(content)
    if not start_match:
        return content.strip()
    body = content[start_match.start():]
    end_pos = len(body)
    for marker in BODY_END_MARKERS:
        idx = body.find(marker)
        if idx >= 0 and idx < end_pos:
            end_pos = idx
    body = body[:end_pos]
    cleaned_lines = []
    for line in body.split("\n"):
        stripped = line.strip()
        if any(p.match(stripped) for p in NOISE_LINE_PATTERNS):
            continue
        if not stripped or stripped == "\u3000" or stripped == "\xa0":
            continue
        cleaned_lines.append(line.rstrip())
    result = []
    prev_blank = False
    for line in cleaned_lines:
        if not line.strip():
            if prev_blank:
                continue
            prev_blank = True
        else:
            prev_blank = False
        result.append(line)
    return "\n".join(result).strip()


def build_page_urls(base_url: str, max_pages: int) -> list[str]:
    """构造翻页 URL 列表。

    ccgp 翻页格式: index_2.htm、index_3.htm ...（page 1 为基础 URL）
    例如: http://www.ccgp.gov.cn/cggg/zygg/gkzb/index_2.htm
    """
    urls = [base_url]
    base = base_url.rstrip("/")
    for page in range(2, max_pages + 1):
        urls.append(f"{base}/index_{page}.htm")
    return urls


def classify_by_url(url: str) -> str:
    """仅凭 URL 关键词推断公告类型（URL 列表模式无标题时使用）。"""
    lower = url.lower()
    if "gzgg" in lower or "更正" in url:
        return "correction"
    if "zbgg" in lower or "中标" in url:
        return "award"
    if "gkzb" in lower or "招标" in url:
        return "tender"
    return "other"


def extract_title(html: str) -> str:
    """从详情页 HTML 提取标题（URL 列表模式无列表页标题时使用）。"""
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r"<h[12][^>]*>([^<]+)</h[12]>", html, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return "untitled"


def parse_urls_file(filepath: Path) -> list[tuple[str, str, str]]:
    """解析 URL 清单文件。每行格式: `type|url` 或 `url`（type 可选）。"""
    items: list[tuple[str, str, str]] = []
    for raw in filepath.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "|" in line:
            ntype, url = line.split("|", 1)
            ntype = ntype.strip().lower()
            url = url.strip()
            if ntype not in ("tender", "award", "correction", "other"):
                ntype = classify_by_url(url)
        else:
            url = line
            ntype = classify_by_url(url)
        if url:
            items.append(("", url, ntype))
    return items


def get_next_index(out_dir: Path, prefix: str, ntype: str) -> int:
    """获取该类型下一个可用索引（已有文件最大值 + 1）。"""
    max_idx = 0
    for f in out_dir.glob(f"{prefix}_{ntype}_*.txt"):
        try:
            idx = int(f.stem.rsplit("_", 1)[-1])
            if idx > max_idx:
                max_idx = idx
        except ValueError:
            continue
    return max_idx + 1


def fetch_list_items(max_pages: int, per_list: int, timeout: int, delay: int) -> list[tuple[str, str, str]]:
    """抓取所有列表页（含翻页），汇总条目。"""
    all_items: list[tuple[str, str, str]] = []
    for category, list_url in LIST_URLS:
        page_urls = build_page_urls(list_url, max_pages)
        print(f"[{category}] 共 {len(page_urls)} 页: {list_url}", flush=True)
        for pi, purl in enumerate(page_urls, 1):
            html = fetch_url(purl, timeout)
            if not html:
                print(f"  page {pi} 抓取失败，跳过", flush=True)
                time.sleep(delay)
                continue
            items = parse_list_page(html, list_url)
            print(f"  page {pi}: 解析到 {len(items)} 条", flush=True)
            for title, url in items[:per_list]:
                ntype = classify_notice(title, category)
                all_items.append((title, url, ntype))
            time.sleep(delay)
    return all_items


def save_detail(title: str, url: str, ntype: str, idx: int, out_dir: Path,
                prefix: str, timeout: int, do_clean: bool) -> bool:
    """抓取单篇详情并保存，返回是否成功。"""
    html = fetch_url(url, timeout)
    if not html:
        print(f"  FAIL: {url}", flush=True)
        return False
    if not title:
        title = extract_title(html)
    text = clean_html_to_text(html)
    if len(text) < 200:
        print(f"  SKIP (内容太短 {len(text)} 字符)", flush=True)
        return False
    if do_clean:
        text = clean_content(text)
        if len(text) < 100:
            print(f"  SKIP (清洗后过短)", flush=True)
            return False
    fname = f"{prefix}_{ntype}_{idx:03d}.txt"
    content = (
        f"# {title}\n"
        f"# URL: {url}\n"
        f"# Type: {ntype}\n"
        f"# Fetched: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"{text}"
    )
    (out_dir / fname).write_text(content, encoding="utf-8")
    print(f"  OK: {len(text)} 字符 → {fname}", flush=True)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ccgp 翻页扩展抓取脚本（v4.1 §10.1 金标扩充）"
    )
    parser.add_argument("--max-pages", type=int, default=2, help="每个列表翻页数（默认2）")
    parser.add_argument("--per-list", type=int, default=20, help="每页抓取条数（默认20）")
    parser.add_argument("--delay", type=int, default=8, help="请求间隔秒（默认8）")
    parser.add_argument("--timeout", type=int, default=15, help="超时秒（默认15）")
    parser.add_argument("--out-dir", type=str, default="_w4_raw", help="输出目录（默认_w4_raw）")
    parser.add_argument("--urls-file", type=str, default=None, help="URL清单文件（每行一个URL），使用此模式时跳过列表页")
    parser.add_argument("--prefix", type=str, default="w4", help="输出文件名前缀（默认w4）")
    parser.add_argument("--clean", type=str, default="True", help="是否自动清洗（默认True）")
    args = parser.parse_args()

    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    do_clean = str(args.clean).lower() in ("true", "1", "yes")

    print("=== ccgp 翻页扩展抓取 ===", flush=True)
    print(f"输出目录: {out_dir}", flush=True)
    print(f"翻页数: {args.max_pages}  每页: {args.per_list}  间隔: {args.delay}s  超时: {args.timeout}s", flush=True)
    print(f"自动清洗: {do_clean}", flush=True)

    # 收集条目
    if args.urls_file:
        urls_path = Path(args.urls_file)
        if not urls_path.is_absolute():
            urls_path = ROOT / args.urls_file
        if not urls_path.exists():
            print(f"URL清单不存在: {urls_path}", flush=True)
            return
        items = parse_urls_file(urls_path)
        print(f"URL清单模式: {urls_path} → {len(items)} 条", flush=True)
    else:
        print("\n=== 第一步：抓取列表页（含翻页）===", flush=True)
        items = fetch_list_items(args.max_pages, args.per_list, args.timeout, args.delay)

    print(f"\n列表总计: {len(items)} 条", flush=True)
    by_type: dict[str, int] = {}
    for _, _, t in items:
        by_type[t] = by_type.get(t, 0) + 1
    for t, c in sorted(by_type.items()):
        print(f"  {t}: {c}", flush=True)

    # 按类型分组，索引从已有最大值+1开始
    print(f"\n=== 第二步：抓取详情页（间隔 {args.delay}s）===", flush=True)
    type_counters: dict[str, int] = {}
    for ntype in set(t for _, _, t in items):
        type_counters[ntype] = get_next_index(out_dir, args.prefix, ntype)

    success = 0
    failed = 0
    skipped = 0
    total = len(items)
    for i, (title, url, ntype) in enumerate(items, 1):
        idx = type_counters[ntype]
        fpath = out_dir / f"{args.prefix}_{ntype}_{idx:03d}.txt"
        if fpath.exists():
            print(f"[{i}/{total}] SKIP (已存在): {fpath.name}", flush=True)
            type_counters[ntype] += 1
            skipped += 1
            continue
        print(f"[{i}/{total}] {ntype}: {(title or url)[:50]}", flush=True)
        ok = save_detail(title, url, ntype, idx, out_dir, args.prefix, args.timeout, do_clean)
        if ok:
            success += 1
        else:
            failed += 1
        type_counters[ntype] += 1
        time.sleep(args.delay)

    print(f"\n=== 抓取完成 ===", flush=True)
    print(f"成功: {success}  失败: {failed}  跳过: {skipped}  总计: {total}", flush=True)
    print(f"输出目录: {out_dir}", flush=True)

    files = list(out_dir.glob(f"{args.prefix}_*.txt"))
    type_count: dict[str, int] = {}
    for f in files:
        parts = f.stem.split("_")
        if len(parts) >= 2:
            type_count[parts[1]] = type_count.get(parts[1], 0) + 1
    print("\n类型分布:", flush=True)
    for t, c in sorted(type_count.items()):
        print(f"  {t}: {c}", flush=True)


if __name__ == "__main__":
    main()
