# -*- coding: utf-8 -*-
"""GLM-4-flash vs Ollama(qwen2.5:7b) 打标质量对比。

取库里若干条公告，用完全相同的提示词分别打标，并排对比 + 算一致率。
在用户机器上跑（沙箱无 Ollama/HTTPS）：
    python scripts/compare_taggers.py --limit 10

判定标准：行业/地区/类型三项全部一致的比例 ≥90%，Ollama 才可接管打标。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from app.models.database import AsyncSessionLocal  # noqa: E402
from app.tagging import _build_system_prompt, _build_user_prompt, _sanitize_tags  # noqa: E402
from app.llm.provider import parse_json_lenient  # noqa: E402
from sqlalchemy import select, text  # noqa: E402
from app.models.tender import Tender  # noqa: E402

GLM_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
OLLAMA_URL = "http://localhost:11434/v1/chat/completions"


async def _call_glm(key: str, title: str, content: str) -> dict:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(GLM_URL, headers={"Authorization": f"Bearer {key}"}, json={
            "model": "glm-4-flash",
            "messages": [
                {"role": "system", "content": _build_system_prompt()},
                {"role": "user", "content": _build_user_prompt(title, content)},
            ],
            "temperature": 0,
        })
        r.raise_for_status()
        return _sanitize_tags(parse_json_lenient(r.json()["choices"][0]["message"]["content"]))


async def _call_ollama(title: str, content: str) -> dict:
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(OLLAMA_URL, json={
            "model": "qwen2.5:7b",
            "messages": [
                {"role": "system", "content": _build_system_prompt()},
                {"role": "user", "content": _build_user_prompt(title, content)},
            ],
            "stream": False,
            "options": {"temperature": 0},
        })
        r.raise_for_status()
        return _sanitize_tags(parse_json_lenient(r.json()["choices"][0]["message"]["content"]))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--all", action="store_true", help="对比全部已打标公告")
    args = ap.parse_args()

    from app.config import settings
    key = settings.ZHIPU_API_KEY
    if not key:
        raise SystemExit("ZHIPU_API_KEY 未配置")

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(Tender.project_name, Tender.core_content).limit(args.limit)
        )).fetchall()
    notices = [(r[0] or "", r[1] or "") for r in rows]
    if not notices:
        raise SystemExit("库中没有公告数据")

    same = 0
    print(f"{'公告标题':<32} {'GLM':<22} {'Ollama':<22} 一致")
    print("-" * 96)
    for title, content in notices:
        glm = await _call_glm(key, title, content)
        try:
            ollama = await _call_ollama(title, content)
        except Exception as exc:  # noqa: BLE001
            print(f"  Ollama 调用失败: {type(exc).__name__}（确认 ollama serve 已启动）")
            return
        ok = all(glm.get(k) == ollama.get(k) for k in ("industry", "region", "notice_type"))
        same += int(ok)
        g = f"{glm.get('industry')}/{glm.get('region')}/{glm.get('notice_type')}"
        o = f"{ollama.get('industry')}/{ollama.get('region')}/{ollama.get('notice_type')}"
        print(f"{title[:30]:<34} {g:<22} {o:<22} {'✓' if ok else '✗'}")
    print("-" * 96)
    rate = same / len(notices) * 100
    print(f"三项全一致率: {rate:.0f}%（≥90% Ollama 可接管打标）")


if __name__ == "__main__":
    asyncio.run(main())
