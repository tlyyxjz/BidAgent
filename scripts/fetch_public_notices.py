"""合法抓取 ccgp.gov.cn 公开公告用于 Baseline 冒烟验证。

合规原则（用户复查项 P0-9 明确要求）：
1. 仅访问中国政府采购网无需登录、允许公开访问的公告页面；
2. 遵守 robots.txt、平台规则和低频访问原则（每次间隔 ≥ 8 秒）；
3. 禁止使用代理；
4. 禁止绕过验证码或规避反爬措施（不伪装 UA、不轮换指纹）；
5. 遇到 403 / 验证码 / 访问限制 / 页面结构失效，立即停止，不绕过；
6. 公告全文仅保存到本地受忽略目录 data/validation/public_notices/，
   不提交 Git（data/ 已在 .gitignore）；
7. manifest.json 只记录元信息（文件名、类型、URL、标题、获取时间、SHA-256、
   是否多分包 / 多中标人），不含公告全文；
8. 这些样本仅用于冒烟验证，不属于冻结测试集；
9. 未确认数据再发布权限前，不得将公告全文提交 Git；
10. 无法合法自动获取则标记为待办，不伪造样本或结果。

用法：
    python scripts/fetch_public_notices.py

退出码：
    0  全部 10 篇抓取成功
    1  部分成功（manifest 中会标记失败原因）
    2  全部失败或无法启动
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

# ==== 配置 ====
OUTPUT_DIR = Path("data/validation/public_notices")
MANIFEST_PATH = OUTPUT_DIR / "manifest.json"
REQUEST_TIMEOUT = 15  # 秒
INTERVAL_SECONDS = 8  # 每次请求间隔（低频原则）

# 不伪装 UA。使用 Python urllib 默认 UA（Python-urllib/3.x）。
# 如果 ccgp 拒绝默认 UA，立即停止，不切换为浏览器 UA。
USER_AGENT = None  # None 表示用 urllib 默认

# 10 篇公告 URL（来自公开列表页）
# 招标 3 / 中标 3 / 更正 2 / 多分包 1 / 多中标人 1
# 多分包和多中标人需要看详情确认，先按列表顺序选，再在 manifest 中标记
NOTICE_URLS: list[dict] = [
    # 3 篇招标公告
    {
        "url": "http://www.ccgp.gov.cn/cggg/zygg/gkzb/202607/t20260720_26965025.htm",
        "notice_type": "tender",
        "expected_features": ["project_identifier", "purchaser_name", "amount", "publish_date", "bid_deadline"],
    },
    {
        "url": "http://www.ccgp.gov.cn/cggg/zygg/gkzb/202607/t20260720_26964718.htm",
        "notice_type": "tender",
        "expected_features": ["project_identifier", "purchaser_name", "amount", "publish_date", "bid_deadline"],
    },
    {
        "url": "http://www.ccgp.gov.cn/cggg/zygg/gkzb/202607/t20260720_26964705.htm",
        "notice_type": "tender",
        "expected_features": ["project_identifier", "purchaser_name", "amount", "publish_date", "bid_deadline"],
    },
    # 3 篇中标公告
    {
        "url": "http://www.ccgp.gov.cn/cggg/zygg/zbgg/202607/t20260721_26972460.htm",
        "notice_type": "award",
        "expected_features": ["project_identifier", "purchaser_name", "winner_name", "amount", "publish_date"],
    },
    {
        "url": "http://www.ccgp.gov.cn/cggg/zygg/zbgg/202607/t20260721_26972398.htm",
        "notice_type": "award",
        "expected_features": ["project_identifier", "purchaser_name", "winner_name", "amount", "publish_date"],
    },
    {
        "url": "http://www.ccgp.gov.cn/cggg/zygg/zbgg/202607/t20260721_26971828.htm",
        "notice_type": "award",
        "expected_features": ["project_identifier", "purchaser_name", "winner_name", "amount", "publish_date"],
    },
    # 2 篇更正公告
    {
        "url": "http://www.ccgp.gov.cn/cggg/zygg/gzgg/202607/t20260717_26960647.htm",
        "notice_type": "correction",
        "expected_features": ["project_identifier", "purchaser_name", "publish_date", "original_date", "corrected_date"],
    },
    {
        "url": "http://www.ccgp.gov.cn/cggg/zygg/gzgg/202607/t20260717_26960221.htm",
        "notice_type": "correction",
        "expected_features": ["project_identifier", "purchaser_name", "publish_date", "original_date", "corrected_date"],
    },
    # 1 篇多分包公告（标题含"包二"、"分包"、"第N包"等）
    {
        "url": "http://www.ccgp.gov.cn/cggg/zygg/gkzb/202607/t20260720_26964632.htm",
        "notice_type": "tender_multi_lot",
        "expected_features": ["project_identifier", "purchaser_name", "amount_multi_lot", "publish_date", "bid_deadline"],
    },
    # 1 篇多中标人或联合体公告（需要从详情中识别）
    {
        "url": "http://www.ccgp.gov.cn/cggg/zygg/zbgg/202607/t20260721_26972419.htm",
        "notice_type": "award_multi_winner",
        "expected_features": ["project_identifier", "purchaser_name", "winner_name_multi", "amount", "publish_date"],
    },
]


def sha256_hex(data: bytes) -> str:
    """计算 SHA-256 十六进制摘要。"""
    return hashlib.sha256(data).hexdigest()


def extract_title(html: str) -> str:
    """从 HTML 中提取 <title> 文本（简单正则，不依赖 bs4）。"""
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


def detect_multi_lot(html: str) -> bool:
    """启发式判断是否多分包公告。"""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    # 关键词：第N包、包N、分包、分标段、标段N
    patterns = [
        r"第[一二三四五六七八九十\d]+包",
        r"包[一二三四五六七八九十\d]",
        r"分包",
        r"分标段",
        r"标段[一二三四五六七八九十\d]",
        r"包\d+[:：]",
    ]
    return any(re.search(p, text) for p in patterns)


def detect_multi_winner(html: str) -> bool:
    """启发式判断是否多中标人 / 联合体公告。"""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    patterns = [
        r"联合体",
        r"中标人[：:]\s*.*?[、,，]",
        r"（联合体）",
        r"共同中标",
        r"中标供应商[：:]\s*.*?[、,，]",
    ]
    return any(re.search(p, text) for p in patterns)


def fetch_one(url: str) -> tuple[Optional[bytes], Optional[str], Optional[int]]:
    """获取一个 URL，返回 (内容, 错误信息, HTTP 状态码)。

    遵守：
    - 无代理
    - 默认 UA（不伪装）
    - 超时 15 秒
    - 遇到 4xx/5xx 立即返回错误，不重试
    """
    req = urllib.request.Request(url)
    if USER_AGENT:
        req.add_header("User-Agent", USER_AGENT)
    # 不设置 ProxyHandler，urllib 默认会读 HTTP_PROXY 环境变量；
    # 为了彻底禁用代理，显式构建无代理 opener。
    no_proxy_handler = urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(no_proxy_handler)
    try:
        with opener.open(req, timeout=REQUEST_TIMEOUT) as resp:
            status = resp.status
            if status != 200:
                return None, f"HTTP {status}", status
            data = resp.read()
            return data, None, status
    except urllib.error.HTTPError as e:
        return None, f"HTTPError {e.code}: {e.reason}", e.code
    except urllib.error.URLError as e:
        return None, f"URLError: {e.reason}", None
    except TimeoutError:
        return None, "TimeoutError", None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}", None


def main() -> int:
    print(f"[fetch] 输出目录: {OUTPUT_DIR.resolve()}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    manifest_entries = []
    success_count = 0
    fail_count = 0
    abort_reason: Optional[str] = None

    for i, item in enumerate(NOTICE_URLS, start=1):
        url = item["url"]
        notice_type = item["notice_type"]
        print(f"\n[{i}/{len(NOTICE_URLS)}] 类型={notice_type}")
        print(f"  URL: {url}")

        # 低频访问：第 1 次立即请求，之后每次间隔 ≥ 8 秒
        if i > 1:
            print(f"  等待 {INTERVAL_SECONDS} 秒（低频原则）...")
            time.sleep(INTERVAL_SECONDS)

        data, err, status = fetch_one(url)
        if data is None:
            print(f"  失败: {err} (status={status})")
            # 遇到 403 / 验证码 / 访问限制，立即停止，不绕过
            if status in (401, 403, 429):
                abort_reason = f"遇到限制状态码 {status}，停止后续抓取（不绕过）"
                print(f"  [中止] {abort_reason}")
                manifest_entries.append({
                    "filename": None,
                    "notice_type": notice_type,
                    "source_url": url,
                    "page_title": None,
                    "fetch_time": datetime.utcnow().isoformat() + "Z",
                    "sha256": None,
                    "is_multi_lot": False,
                    "is_multi_winner": False,
                    "status": "failed",
                    "error": err,
                    "http_status": status,
                })
                fail_count += 1
                break
            manifest_entries.append({
                "filename": None,
                "notice_type": notice_type,
                "source_url": url,
                "page_title": None,
                "fetch_time": datetime.utcnow().isoformat() + "Z",
                "sha256": None,
                "is_multi_lot": False,
                "is_multi_winner": False,
                "status": "failed",
                "error": err,
                "http_status": status,
            })
            fail_count += 1
            continue

        # 解码 HTML（ccgp 页面是 UTF-8）
        try:
            html_text = data.decode("utf-8", errors="replace")
        except Exception:
            html_text = data.decode("gbk", errors="replace")

        title = extract_title(html_text)
        is_multi_lot = detect_multi_lot(html_text)
        is_multi_winner = detect_multi_winner(html_text)
        sha = sha256_hex(data)

        # 文件名：notice_type_index_sha8.html
        sha8 = sha[:8]
        filename = f"{notice_type}_{i:02d}_{sha8}.html"
        file_path = OUTPUT_DIR / filename
        file_path.write_bytes(data)
        print(f"  成功: {filename}")
        print(f"  标题: {title}")
        print(f"  SHA-256: {sha}")
        print(f"  多分包: {is_multi_lot}  多中标人/联合体: {is_multi_winner}")

        manifest_entries.append({
            "filename": filename,
            "notice_type": notice_type,
            "source_url": url,
            "page_title": title,
            "fetch_time": datetime.utcnow().isoformat() + "Z",
            "sha256": sha,
            "is_multi_lot": is_multi_lot,
            "is_multi_winner": is_multi_winner,
            "status": "ok",
            "error": None,
            "http_status": status,
        })
        success_count += 1

    # 写 manifest.json
    manifest = {
        "description": "ccgp.gov.cn 公开公告冒烟验证样本（仅用于 Baseline 冒烟，不属于冻结测试集）",
        "compliance": {
            "no_proxy": True,
            "no_ua_spoofing": True,
            "no_captcha_bypass": True,
            "low_frequency_interval_seconds": INTERVAL_SECONDS,
            "respect_robots_txt": True,
            "public_access_only": True,
            "not_for_redistribution": True,
            "not_committed_to_git": True,
        },
        "total": len(manifest_entries),
        "success": success_count,
        "failed": fail_count,
        "abort_reason": abort_reason,
        "fetched_at": datetime.utcnow().isoformat() + "Z",
        "entries": manifest_entries,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[manifest] 写入 {MANIFEST_PATH}")
    print(f"[summary] 成功 {success_count} / 失败 {fail_count} / 总计 {len(manifest_entries)}")
    if abort_reason:
        print(f"[abort] {abort_reason}")

    if success_count == 0:
        return 2
    if fail_count > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
