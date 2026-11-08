# -*- coding: utf-8 -*-
"""大件一-③：award 缺口混合回填器（LLM 提议 + 规则裁判）。

架构（与生产链路一致："LLM 提议、规则裁判"）：
1. LLM 只出候选值（win_company / win_amount_raw）+ 原文证据片段
2. 规则裁判：
   - 公司名必须是原文精确子串且以机构后缀收尾
   - 金额数字串必须原文可见，单位换算与原文单位一致
   - 证据片段必须是原文精确子串（定位不上即拒收）
3. 只填空（win_company/win_amount 已有值绝不覆盖）

用法：
    python scripts/backfill_award_hybrid.py [--limit N] [--fresh] [--workers 3]
断点续跑：_w3_outputs/award_hybrid_ckpt.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.llm.provider import (  # noqa: E402
    build_chat_payload,
    chat_endpoint,
    extract_content_and_usage,
    parse_json_lenient,
    resolve_provider,
)

DB = ROOT / "data" / "bidagent.db"
CKPT_DIR = ROOT / "_w3_outputs"
CKPT = CKPT_DIR / "award_hybrid_ckpt.jsonl"

SYSTEM_PROMPT = (
    "你是招投标公告信息抽取器。只输出 JSON，禁止 markdown 围栏与解释。\n"
    "从中标公告正文抽取：\n"
    '1. win_company：中标（成交）供应商全称，必须是原文中逐字出现的连续片段；\n'
    '2. win_amount_raw：中标（成交）金额含单位的原文写法（如"120.5万元"、"1,234,567.00元"），'
    "必须是原文中逐字出现的数字与单位；\n"
    "3. company_evidence / amount_evidence：包含对应值的原文连续片段（≤60字，逐字复制不得改写）。\n"
    "找不到就置 null。输出格式：\n"
    '{"win_company": str|null, "win_amount_raw": str|null, '
    '"company_evidence": str|null, "amount_evidence": str|null}'
)

_ORG_TAIL = re.compile(
    r"(?:公司|中心|大学|学院|医院|集团|研究院|研究所|事务所|合作社|银行|厂|店|站|馆)$"
)
_NUM_CORE = re.compile(r"\d+(?:[,.]\d+)*(?:\.\d+)?")
# 值前粘连的标签前缀（如“牵头供应商：XX公司”→“XX公司”）
_LABEL_PREFIX = re.compile(r"^.*?[:：]\s*")


def _load_ckpt() -> dict[int, dict]:
    done: dict[int, dict] = {}
    if CKPT.exists():
        for line in CKPT.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
                done[int(rec["id"])] = rec
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
    return done


def _adjudicate(body: str, prop: dict) -> dict:
    """规则裁判：返回 {company, amount, reasons}。定位不上即拒收。"""
    reasons: list[str] = []
    company = None
    amount_num = None

    wc = prop.get("win_company")
    if isinstance(wc, str) and wc.strip():
        wc = wc.strip()
        wc = _LABEL_PREFIX.sub("", wc).strip()  # 剪标签前缀
        if wc and re.search(r"\s", wc):
            wc = wc.split()[0]  # 联合体等多值场景取首段（牵头供应商即中标人）
        # 首字必须是汉字（拒收符号/字母开头的碎片），且无内部空白
        clean = bool(wc) and "\u4e00" <= wc[0] <= "\u9fff" and not re.search(r"\s", wc)
        if clean and wc in body and _ORG_TAIL.search(wc):
            ce = prop.get("company_evidence")
            if isinstance(ce, str) and ce.strip() and ce.strip() in body and wc in ce:
                company = wc
            else:
                reasons.append("company_evidence_not_locatable")
        else:
            reasons.append("company_not_exact_substring_or_no_org_tail")

    wa = prop.get("win_amount_raw")
    if isinstance(wa, str) and wa.strip():
        wa = wa.strip()
        m = _NUM_CORE.search(wa)
        digits = m.group(0) if m else None
        if digits and digits in body:
            ae = prop.get("amount_evidence")
            # 数字后紧跟括号单位说明是表头变体（如“3055.0000（万元）”），单位归属不明 → 拒收
            after = wa[wa.index(digits) + len(digits):]
            if isinstance(ae, str) and ae.strip() and ae.strip() in body and digits in ae \
                    and not re.match(r"\s*[（(]", after):
                # 单位换算：与 tender_utils._parse_decimal 同规则
                num = digits.replace(",", "").replace("，", "")
                try:
                    from decimal import Decimal

                    mult = Decimal(1)
                    if "亿" in wa:
                        mult = Decimal("100000000")
                    elif "万" in wa:
                        mult = Decimal("10000")
                    amount_num = Decimal(num) * mult
                    if amount_num <= 0:
                        amount_num = None
                        reasons.append("amount_non_positive")
                except Exception:  # noqa: BLE001
                    reasons.append("amount_parse_failed")
            else:
                reasons.append("amount_evidence_not_locatable")
        else:
            reasons.append("amount_digits_not_in_body")

    return {"company": company, "amount": amount_num, "reasons": reasons}


async def _propose(client: httpx.AsyncClient, provider, body: str) -> dict:
    payload = build_chat_payload(
        provider, SYSTEM_PROMPT, f"中标公告正文：\n{body[:6000]}",
        temperature=0.0, max_tokens=600,
    )
    headers = {
        "Authorization": f"Bearer {provider.api_key}",
        "Content-Type": "application/json",
    }
    resp = await client.post(chat_endpoint(provider), headers=headers, json=payload)
    resp.raise_for_status()
    content, _ = extract_content_and_usage(resp.json())
    return parse_json_lenient(content)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="仅处理前 N 条（0=全部）")
    ap.add_argument("--fresh", action="store_true", help="忽略断点重跑")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--min-body-len", type=int, default=150,
                    help="正文长度下限（短于此为采集侧截断，无信息可抽）")
    args = ap.parse_args()

    provider = resolve_provider("extraction")
    print(f"provider={provider.name} model={provider.model}")

    c = sqlite3.connect(DB)
    rows = c.execute(
        "SELECT id, COALESCE(NULLIF(core_content,''), source_raw_text, '') AS body, "
        "win_company, win_amount FROM tenders WHERE notice_type='award' "
        "AND ((win_company IS NULL OR win_company='') OR win_amount IS NULL) "
        "AND LENGTH(COALESCE(NULLIF(core_content,''), source_raw_text, '')) >= ?",
        (args.min_body_len,),
    ).fetchall()
    if args.limit > 0:
        rows = rows[: args.limit]

    done = {} if args.fresh else _load_ckpt()
    if args.fresh and CKPT.exists():
        CKPT.unlink()
    CKPT_DIR.mkdir(parents=True, exist_ok=True)

    sem = asyncio.Semaphore(args.workers)
    stats = {"filled_company": 0, "filled_amount": 0, "rejected": 0, "error": 0}
    ckpt_f = CKPT.open("a", encoding="utf-8")

    async def work(tid: int, body: str, has_co: bool, has_amt: bool) -> None:
        if tid in done:
            return
        async with sem:
            try:
                async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT_SECONDS) as client:
                    prop = await _propose(client, provider, body)
            except Exception as exc:  # noqa: BLE001
                stats["error"] += 1
                rec = {"id": tid, "status": "error", "error": str(exc)[:200]}
                ckpt_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                ckpt_f.flush()
                return
        verdict = _adjudicate(body, prop)
        updates = {}
        if not has_co and verdict["company"]:
            updates["win_company"] = verdict["company"]
            stats["filled_company"] += 1
        if not has_amt and verdict["amount"] is not None:
            v = verdict["amount"]
            updates["win_amount"] = int(v) if v == v.to_integral_value() else float(v)
            stats["filled_amount"] += 1
        if updates:
            sets = ", ".join(f"{k} = ?" for k in updates)
            c.execute(f"UPDATE tenders SET {sets} WHERE id = ?",
                      (*updates.values(), tid))
            c.commit()
        elif not verdict["company"] and verdict["amount"] is None:
            stats["rejected"] += 1
        rec = {
            "id": tid, "status": "ok",
            "proposed_company": prop.get("win_company"),
            "proposed_amount": prop.get("win_amount_raw"),
            "accepted_company": verdict["company"],
            "accepted_amount": str(verdict["amount"]) if verdict["amount"] else None,
            "reasons": verdict["reasons"],
        }
        ckpt_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        ckpt_f.flush()

    tasks = [work(tid, body, bool(co), amt is not None) for tid, body, co, amt in rows]
    await asyncio.gather(*tasks)
    ckpt_f.close()

    total = c.execute("SELECT COUNT(*) FROM tenders WHERE notice_type='award'").fetchone()[0]
    have_co = c.execute(
        "SELECT COUNT(*) FROM tenders WHERE notice_type='award' "
        "AND win_company IS NOT NULL AND win_company != ''"
    ).fetchone()[0]
    have_amt = c.execute(
        "SELECT COUNT(*) FROM tenders WHERE notice_type='award' AND win_amount IS NOT NULL"
    ).fetchone()[0]
    print(f"处理 {len(rows)} 条（正文>={args.min_body_len} 字）")
    print(f"本轮: {stats}")
    print(f"win_company 覆盖率: {have_co}/{total} = {have_co/total:.1%}")
    print(f"win_amount 覆盖率: {have_amt}/{total} = {have_amt/total:.1%}")


if __name__ == "__main__":
    asyncio.run(main())
