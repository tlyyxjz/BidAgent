# -*- coding: utf-8 -*-
"""前后端数据对齐门禁：按**接口真实链路**逐条校验证据偏移。

与 `check_evidence_offsets.py` 的区别（两者互补，别互相替代）：
- `check_evidence_offsets.py` 走 `evidence.tender_id` 直查，看**库内**数据卫生。
- 本脚本走**前端真正消费的链路**：
      field_evidence_links → extracted_fields → evidence → tenders.core_content
  并调用与接口同一份解析函数 `app.api.evidence_span.resolve_evidence_span`。
  换句话说：它断言的是「打开 notice_detail.html 时，高亮能不能落在正确的字上」。

检查项：
1. 链接完整性：`field_evidence_links` 无悬空外键；每条 evidence 都被引用。
2. 无跨公告挂靠：`field.tender_id == evidence.tender_id`。
3. 偏移可解析：每条证据按 clean_raw_text(=core_content) 基准必须 locatable；
   允许已登记的历史 STALE 公告（tender_id 1-5，正文被重抓覆盖、不可恢复）。
4. 回归护栏：出现**新增**的不可定位证据 / 悬空外键 / 跨公告挂靠即 fail。

用法:
    python scripts/check_front_back_alignment.py [--json out.json] [--allow-stale 1,2,3,4,5]
退出码: 0=对齐, 1=发现对齐问题
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.api.evidence_span import resolve_evidence_span  # noqa: E402

DB = ROOT / "data" / "bidagent.db"

# 已登记的历史失配（两类孤儿，均不可恢复，2026-10-08 核实）：
#  A 类 = tender_id 1-5：正文于 2026-08-05 被重抓覆盖成别的公告，旧证据文本已不在任何字段
#         与 data/snapshots/ 快照中。涉及 42 条证据。
#  B 类 = tender_id 115/116/117/118/120/123/128：公告本身已不在 tenders 表，字段（全是
#         publish_date）与证据成孤儿。涉及 7 字段 + 7 证据。
# 门禁对这两类只做**登记**，对任何**新增**失配报警。
DEFAULT_ALLOW_STALE = {1, 2, 3, 4, 5}
DEFAULT_ALLOW_MISSING_TENDER = {115, 116, 117, 118, 120, 123, 128}

# 已登记的「多值字段被标成 present 而被压平」（2026-10-08 核实）：
#  写入端 evidence_writes.py:111-113 对 multi_value 有保护（不触发 _deprecate_old_versions），
#  抽取规范 extractor_prompts.py:37/39 也要求多标段/多中标人输出 multi_value；
#  但个别公告被标成 present ⇒ 每写一行把前一行 is_current 翻 0，真值被压平。
#  全库仅 tid=39 的 amount / winner_name 两组命中（其余同场景组合均为 multi_value 且全保）。
#  订正脚本：scripts/fix_multi_value_status.py。此处只做登记，对**新增**压平报警。
DEFAULT_ALLOW_FLATTENED = {(39, "amount"), (39, "winner_name")}

FLATTENED_SQL = """
select f.tender_id, f.field_name, count(*) as n,
       sum(case when f.field_status = 'present' then 1 else 0 end) as present_n,
       sum(f.is_current) as cur
  from extracted_fields f
 group by f.tender_id, f.field_name
having count(*) > 1
"""

CHAIN_SQL = """
select l.id as link_id,
       f.id as field_id, f.tender_id as field_tender, f.field_name,
       e.id as ev_id, e.tender_id as ev_tender,
       e.raw_start, e.raw_end, e.evidence_text,
       t.id as tender_id, t.core_content
  from field_evidence_links l
  left join extracted_fields f on f.id = l.field_id
  left join evidence e on e.id = l.evidence_id
  left join tenders t on t.id = f.tender_id
"""


def audit(allow_stale, allow_missing, allow_flattened):
    con = sqlite3.connect(DB)
    cur = con.cursor()

    dangling_link = cur.execute(
        "select count(*) from field_evidence_links l"
        " left join evidence e on e.id=l.evidence_id"
        " left join extracted_fields f on f.id=l.field_id"
        " where e.id is null or f.id is null"
    ).fetchone()[0]

    orphan_evidence = cur.execute(
        "select count(*) from evidence e"
        " left join field_evidence_links l on l.evidence_id=e.id"
        " where l.id is null"
    ).fetchone()[0]

    dangling_evidence = cur.execute(
        "select count(*) from evidence e left join tenders t on t.id=e.tender_id"
        " where t.id is null"
    ).fetchone()[0]

    cross_tender = cur.execute(
        "select count(*) from field_evidence_links l"
        " join extracted_fields f on f.id=l.field_id"
        " join evidence e on e.id=l.evidence_id"
        " where f.tender_id != e.tender_id"
    ).fetchone()[0]

    # 多值字段被压平：同 (tender_id, field_name) 多行、组内全为 present、且 is_current 之和 < 行数
    flattened = {
        (tid, fname): {"n": n, "kept": int(cur_sum or 0)}
        for (tid, fname, n, present_n, cur_sum) in cur.execute(FLATTENED_SQL).fetchall()
        if present_n == n and (cur_sum or 0) < n
    }

    rows = cur.execute(CHAIN_SQL).fetchall()
    con.close()

    stats = Counter()
    bad_by_tender = defaultdict(list)
    missing_tender = defaultdict(list)
    for (_lid, _fid, ftd, fname, _eid, etd, s, en, txt, tid, core) in rows:
        if tid is None:  # 字段所挂 tender 不存在（B 类孤儿）
            stats["field_missing_tender"] += 1
            missing_tender[ftd].append((fname, s, en, "字段所挂公告不存在", txt))
            continue
        rs, re_, slice_ok, relocated, locatable = resolve_evidence_span(core, s, en, txt)
        if locatable:
            stats["relocated" if relocated else "ok"] += 1
        else:
            stats["orphan"] += 1
            bad_by_tender[tid].append((fname, s, en, "证据文本不在原文中", txt))

    new_bad = {t: v for t, v in bad_by_tender.items() if t not in allow_stale}
    new_missing = {t: v for t, v in missing_tender.items() if t not in allow_missing}

    return {
        "rows": len(rows),
        "stats": dict(stats),
        "dangling_link": dangling_link,
        "orphan_evidence": orphan_evidence,
        "dangling_evidence": dangling_evidence,
        "cross_tender": cross_tender,
        "flattened_by_group": {
            f"{tid}/{fname}": v for (tid, fname), v in sorted(flattened.items())
        },
        "new_flattened": sorted(
            f"{tid}/{fname}" for (tid, fname) in flattened if (tid, fname) not in allow_flattened
        ),
        "bad_by_tender": {str(k): len(v) for k, v in sorted(bad_by_tender.items(), key=lambda kv: str(kv[0]))},
        "missing_tender_by_id": {str(k): len(v) for k, v in sorted(missing_tender.items(), key=lambda kv: str(kv[0]))},
        "new_bad_tenders": sorted(str(k) for k in new_bad),
        "new_missing_tenders": sorted(str(k) for k in new_missing),
        "new_bad_samples": [
            {"tender": k, "field": f, "start": s, "end": e, "reason": r, "text": (t or "")[:60]}
            for k, v in sorted(new_bad.items(), key=lambda kv: str(kv[0]))
            for (f, s, e, r, t) in v[:3]
        ],
        "new_missing_samples": [
            {"tender": k, "field": f, "start": s, "end": e, "reason": r, "text": (t or "")[:60]}
            for k, v in sorted(new_missing.items(), key=lambda kv: str(kv[0]))
            for (f, s, e, r, t) in v[:3]
        ],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None, help="问题清单输出 JSON 路径")
    ap.add_argument("--allow-stale", default=None,
                    help="已登记的历史失配公告 id，逗号分隔（默认 1,2,3,4,5）")
    ap.add_argument("--allow-missing-tender", default=None,
                    help="已登记的「字段挂在不存在公告上」id，逗号分隔（默认 115,116,117,118,120,123,128）")
    ap.add_argument("--allow-flattened", default=None,
                    help="已登记的「多值字段被压平」组，格式 tender_id:field_name 逗号分隔"
                         "（默认 39:amount,39:winner_name）")
    args = ap.parse_args()

    allow = DEFAULT_ALLOW_STALE
    if args.allow_stale is not None:
        allow = {int(x) for x in args.allow_stale.split(",") if x.strip()}
    allow_missing = DEFAULT_ALLOW_MISSING_TENDER
    if args.allow_missing_tender is not None:
        allow_missing = {int(x) for x in args.allow_missing_tender.split(",") if x.strip()}
    allow_flattened = DEFAULT_ALLOW_FLATTENED
    if args.allow_flattened is not None:
        allow_flattened = {
            (int(x.split(":", 1)[0]), x.split(":", 1)[1])
            for x in args.allow_flattened.split(",") if x.strip()
        }

    r = audit(allow, allow_missing, allow_flattened)
    st = r["stats"]
    print(f"接口链路行数        : {r['rows']}")
    print(f"  切片逐字一致      : {st.get('ok', 0)}")
    print(f"  已安全重定位      : {st.get('relocated', 0)}")
    print(f"  不可定位(孤儿)    : {st.get('orphan', 0)}")
    print(f"  字段挂空公告      : {st.get('field_missing_tender', 0)}")
    print(f"链接悬空外键        : {r['dangling_link']}")
    print(f"未被引用的 evidence : {r['orphan_evidence']}")
    print(f"指向不存在公告      : {r['dangling_evidence']}")
    print(f"跨公告挂靠          : {r['cross_tender']}")
    print(f"失配按公告分布      : {r['bad_by_tender']}")
    print(f"挂空公告按 id 分布  : {r['missing_tender_by_id']}")
    print(f"已登记(A类STALE)    : {sorted(allow)}")
    print(f"已登记(B类无公告)   : {sorted(allow_missing)}")
    print(f"多值被压平(登记)    : {r['flattened_by_group']}")
    print(f"已登记(压平基线)    : {sorted(f'{t}/{f}' for t, f in allow_flattened)}")

    fail = False
    if r["dangling_link"] or r["cross_tender"]:
        print(f"\n[FAIL] 链路结构异常：悬空链接 {r['dangling_link']} / 跨公告挂靠 {r['cross_tender']}")
        fail = True
    if r["new_bad_tenders"]:
        print(f"\n[FAIL] 出现**新增**不可定位证据的公告: {r['new_bad_tenders']}")
        for s in r["new_bad_samples"]:
            print(f"   t={s['tender']} {s['field']} [{s['start']},{s['end']}) {s['reason']} :: {s['text']!r}")
        fail = True
    if r["new_missing_tenders"]:
        print(f"\n[FAIL] 出现**新增**「字段挂在不存在的公告上」: {r['new_missing_tenders']}")
        for s in r["new_missing_samples"]:
            print(f"   t={s['tender']} {s['field']} {s['reason']} :: {s['text']!r}")
        fail = True
    if r["new_flattened"]:
        print(f"\n[FAIL] 出现**新增**「多值字段被压平」（真值被 is_current 掩盖）: {r['new_flattened']}")
        print("   成因：同 (tender_id, field_name) 多行却全标 present，绕过 multi_value 保护。")
        print("   处置：核对原文后跑 python scripts/fix_multi_value_status.py 订正。")
        fail = True

    if args.json:
        Path(args.json).write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")

    if not fail:
        print("\n[OK] 前后端对齐：接口链路结构完整，无新增不可定位证据。")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
