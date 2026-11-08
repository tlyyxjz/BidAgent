"""打标闭环（独立入口，小接法）—— 抓取 → 打标 → 按标签筛 → 出结果。

这是"打标器接入链路"的最小验证版：不碰现有 processor_agent 主链路，
独立跑通"实时抓取 + LLM 分类筛选"闭环，验证"按标签筛比关键词准"。

用法（本地脚本，暂不接主链路）：
    python scripts/tagged_pipeline.py --industry IT
    python scripts/tagged_pipeline.py --industry 医疗 --notice-type 中标

注意：
- 会调用 LLM（打标），需要 DEEPSEEK_API_KEY；
- 实时抓取会触发域名限速（8秒间隔），抓多页会慢，属正常。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


async def _fetch_notices(limit: int = 30) -> list[dict]:
    """从库里取一批有正文的公告（先用库内数据验证闭环，实时抓取后面再切）。"""
    import sqlite3

    conn = sqlite3.connect(str(ROOT / "data" / "bidagent.db"))
    cur = conn.cursor()
    cur.execute(
        "SELECT project_name, notice_type, substr(core_content,1,500) "
        "FROM tenders WHERE core_content IS NOT NULL AND core_content != '' "
        "LIMIT ?",
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()
    return [{"title": r[0], "notice_type": r[1], "content": r[2]} for r in rows]


def _filter_by_tags(tagged: list[dict], industry: str | None, notice_type: str | None) -> list[dict]:
    """按标签筛（这就是要替换关键词 LIKE 的东西）。"""
    out = []
    for t in tagged:
        if industry and t.get("industry") != industry:
            continue
        if notice_type and t.get("notice_type") != notice_type:
            continue
        out.append(t)
    return out


async def main() -> None:
    parser = argparse.ArgumentParser(description="打标闭环：抓取→打标→按标签筛")
    parser.add_argument("--industry", default=None, help="按行业筛，如 IT/医疗/建筑/教育/能源/交通")
    parser.add_argument("--notice-type", default=None, help="按公告类型筛，如 招标/中标/更正")
    parser.add_argument("--limit", type=int, default=30, help="处理多少条公告")
    parser.add_argument("--concurrency", type=int, default=5, help="打标并发数")
    args = parser.parse_args()

    from app.tagging import tag_many_concurrent

    print(f"1. 取 {args.limit} 条公告...")
    notices = await _fetch_notices(args.limit)
    print(f"   拿到 {len(notices)} 条")

    print("2. 打标中（调 LLM）...")
    tagged = await tag_many_concurrent(notices, concurrency=args.concurrency)

    print("3. 按标签筛...")
    matched = _filter_by_tags(tagged, args.industry, args.notice_type)

    print("\n" + "=" * 60)
    print(f"筛选条件: industry={args.industry or '不限'} notice_type={args.notice_type or '不限'}")
    print(f"总 {len(tagged)} 条 → 命中 {len(matched)} 条")
    print("=" * 60)
    for t in matched:
        print(f"  [{t.get('industry')}|{t.get('notice_type')}|{t.get('region')}] {t['title'][:44]}")
    print("\n其余公告的标签分布：")
    from collections import Counter
    dist = Counter(t.get("industry", "其他") for t in tagged)
    for k, v in dist.most_common():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    asyncio.run(main())
