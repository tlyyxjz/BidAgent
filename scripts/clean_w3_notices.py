"""清洗90篇W3公告: 剥离ccgp.gov.cn网页模板噪声.

ccgp详情页结构:
- 页头: "财政部唯一指定..." / 服务热线 / 导航菜单 (首页/政采法规/购买服务...)
- 面包屑: "当前位置：首页 » 政采公告 » 中央公告 » ..."
- 正文: ## 标题 + 公告内容
- 页脚: "相关公告" / "主办单位" / 网站标识码 / 版权信息

清洗策略:
1. 定位 "## " 起始的正文标题 (公告真正开始)
2. 定位 "相关公告" 或 "主办单位" 结束位置 (正文结束)
3. 剥离中间内容的空行和导航噪声
4. 保留前4行元数据不动
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

RAW_DIR = Path(r"C:\Users\Lenovo\Desktop\BidAgent\_w3_raw")

# 正文起始标记: "## " 开头的标题行 (公告真正开始)
BODY_START_RE = re.compile(r"^##\s+", re.MULTILINE)

# 正文结束标记 (按优先级)
BODY_END_MARKERS = [
    "\n相关公告\n",
    "\n主办单位：",
    "\n网站标识码：",
    "\n相关公告",
]

# 需要剥离的噪声行 (单行)
NOISE_LINE_PATTERNS = [
    re.compile(r"^财政部唯一指定"),
    re.compile(r"^服务热线"),
    re.compile(r"^服务投诉"),
    re.compile(r"^当前位置"),
    re.compile(r"^首页$"),
    re.compile(r"^政采法规$"),
    re.compile(r"^购买服务$"),
    re.compile(r"^监督检查$"),
    re.compile(r"^信息公告$"),
    re.compile(r"^国际专栏$"),
    re.compile(r"^中央公告$"),
    re.compile(r"^政采公告$"),
    re.compile(r"^地方公告$"),
    re.compile(r"^招标公告$"),
    re.compile(r"^中标公告$"),
    re.compile(r"^更正公告$"),
    re.compile(r"^- $"),
    re.compile(r"^-$"),
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
    """清洗正文, 剥离模板噪声."""
    # 1. 定位正文起始 (## 标题)
    start_match = BODY_START_RE.search(content)
    if not start_match:
        # 找不到 ## 标题, 尝试找公告标题特征
        # ccgp 正文通常以公告名开头
        return content.strip()

    body = content[start_match.start():]

    # 2. 定位正文结束
    end_pos = len(body)
    for marker in BODY_END_MARKERS:
        idx = body.find(marker)
        if idx >= 0 and idx < end_pos:
            end_pos = idx
    body = body[:end_pos]

    # 3. 逐行过滤噪声
    lines = body.split("\n")
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        # 跳过噪声行
        if any(p.match(stripped) for p in NOISE_LINE_PATTERNS):
            continue
        # 跳过纯空白和只有\xa0的行
        if not stripped or stripped == "\u3000" or stripped == "\xa0":
            continue
        cleaned_lines.append(line.rstrip())

    # 4. 合并连续空行 (最多保留1个)
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


def process_file(fpath: Path) -> tuple[int, int]:
    """处理单个公告文件.

    Returns:
        (原正文长度, 清洗后正文长度)
    """
    text = fpath.read_text(encoding="utf-8")
    lines = text.split("\n", 4)
    if len(lines) < 5:
        return (0, 0)

    meta = "\n".join(lines[:4])
    content = lines[4]
    original_len = len(content)

    cleaned = clean_content(content)
    cleaned_len = len(cleaned)

    # 只在清洗后内容不为空时写入
    if cleaned_len < 100:
        print(f"  WARNING: {fpath.name} 清洗后内容过短 ({cleaned_len}字符), 保留原文")
        return (original_len, original_len)

    new_text = meta + "\n" + cleaned + "\n"
    fpath.write_text(new_text, encoding="utf-8")
    return (original_len, cleaned_len)


def main() -> None:
    files = sorted(RAW_DIR.glob("*.txt"))
    print(f"清洗 {len(files)} 篇公告")
    print("=" * 70)

    total_orig = 0
    total_cleaned = 0
    stats = []

    for f in files:
        orig, cleaned = process_file(f)
        total_orig += orig
        total_cleaned += cleaned
        reduction = orig - cleaned
        pct = reduction * 100 / orig if orig > 0 else 0
        stats.append((f.name, orig, cleaned, reduction, pct))
        if reduction > 0:
            print(f"  {f.name}: {orig} → {cleaned} (减少 {reduction} 字符, {pct:.1f}%)")

    print("=" * 70)
    print(f"总计: {len(files)} 篇")
    print(f"原始总字符: {total_orig}")
    print(f"清洗后总字符: {total_cleaned}")
    print(f"减少总字符: {total_orig - total_cleaned}")
    avg_reduction = (total_orig - total_cleaned) * 100 / total_orig if total_orig > 0 else 0
    print(f"平均减少率: {avg_reduction:.1f}%")

    # 检查清洗后是否还有噪声关键词
    print("\n--- 清洗后噪声检查 ---")
    noise_keywords = ["财政部唯一指定", "服务热线", "政采法规", "当前位置", "网站标识码", "京ICP备"]
    for f in files:
        text = f.read_text(encoding="utf-8")
        lines = text.split("\n", 4)
        if len(lines) < 5:
            continue
        content = lines[4]
        found = [kw for kw in noise_keywords if kw in content]
        if found:
            print(f"  {f.name}: 仍有噪声 {found}")

    print("\n清洗完成")


if __name__ == "__main__":
    main()
