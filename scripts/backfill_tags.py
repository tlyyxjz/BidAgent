"""回填打标：给库里已有公告打标并入库（复赛演示数据准备）。

用法：
    python scripts/backfill_tags.py --limit 50            # 给最早的 50 条未打标公告打标
    python scripts/backfill_tags.py --limit 20 --concurrency 3

需要 .env 里的 LLM API Key 可用；打标失败只记日志不中断（service 兜底）。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


async def _main_async(limit: int, concurrency: int) -> dict[str, int]:
    from app.models.database import AsyncSessionLocal, init_database
    from app.tagging.service import tag_pending_tenders

    # 先确保库结构完整（notice_tags 等表；幂等，不会动已有数据）
    await init_database()

    async with AsyncSessionLocal() as db:
        return await tag_pending_tenders(
            db, limit=limit, concurrency=concurrency,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="回填打标")
    parser.add_argument("--limit", type=int, default=50, help="最多打标条数")
    parser.add_argument("--concurrency", type=int, default=5, help="并发数（防 API 限流）")
    args = parser.parse_args()

    summary = asyncio.run(_main_async(args.limit, args.concurrency))
    print(f"回填完成: 新打标 {summary['tagged']} 条 / 失败 {summary['errors']} 条")


if __name__ == "__main__":
    main()
