# -*- coding: utf-8 -*-
"""订正「多值字段被标成 present 而被压平」的 extracted_fields 行。

背景（2026-10-08 实查）：
  写入端 app/processors/evidence_writes.py:111-113 已有正确设计 ——
      多值字段（field_status == "multi_value"）**不触发** _deprecate_old_versions；
  而抽取规范 app/llm/extractor_prompts.py 也明确要求：
      · amount（多标段场景）→ 多标段/多中标人的金额「作为 multi_value 输出」
      · winner_name → 非联合体的多个并列中标人「保持 multi_value 逐条输出」

  ⇒ 但库里存在「同一 (tender_id, field_name) 有多行、却全被标成 present」的组，
    于是每写一行就把前一行 is_current 翻成 0，**只剩最后一行是当前值**，真值被压平。

  全库实查（data/bidagent.db）：is_current=0 的字段行**共 6 行**，全部在 tender_id=39：
      winner_name ×3 + amount ×3（field_status 均为 present）。
  对照组（同场景、标对了的）：
      tid=12 / 29 / 32 / 2 等同字段多行组，field_status 均为 multi_value，
      且 sum(is_current) == count(*) ⇒ 全部保住。

  ⇒ 这是**抽取器对个别公告的标注偏离**（prompt 遵守度问题），
    **不是语义未定、也不是设计缺陷** ⇒ 订正方式是数据订正，无需改读取端。

⛔ 明确不做的事：**不在读取端加 `is_current == 1` 过滤**。
   加了会让 tid=39 丢掉 3 个真实中标人与 3 个真实金额。

安全设计：
  · 默认 **不改库**：只体检 + 打印「将改成什么」，并导出 JSON 留档。
  · 加 --apply 才写入；写入前**先整库备份**到 data/backups/。
  · 只改 field_status / is_current / lot_id 三列，**绝不动 raw_value 等值列**。

用法：
    python scripts/fix_multi_value_status.py                 # 体检（默认，不改库）
    python scripts/fix_multi_value_status.py --apply         # 备份后写入
    python scripts/fix_multi_value_status.py --apply --no-backup
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "bidagent.db"
BACKUP_DIR = ROOT / "data" / "backups"
REPORT_DIR = ROOT / "data" / "quarantine"

# ---------------------------------------------------------------------------
# 已核验的 lot_id 补齐映射（**逐条对着原文核过才写进来**）
#
# 依据 tender_id=39 的 tenders.core_content：
#   「三、中标（成交）信息」 供应商/金额 顺序 =
#       甘肃地质工程实验室有限责任公司 46.96万 → 北京瀚禹信息科技有限公司 415万
#       → 兰州大学 114万 → 北京林淼生态环境技术有限公司 92万
#   「四、主要标的信息」 表内 序号/供应商名称/服务范围(包号) =
#       1 甘肃地质工程实验室 → A包 ／ 2 北京瀚禹 → B包
#       3 兰州大学 → C包 ／ 4 北京林淼 → D包
#   而 extracted_fields 里 amount 行的 lot_id 已是 A包/B包/C包/D包，顺序一致。
#   ⇒ 三重一致（中标信息顺序 / 主要标的表 / amount.lot_id）才敢补，不靠 id 猜。
# ---------------------------------------------------------------------------
VERIFIED_WINNER_LOT: dict[int, dict[str, str]] = {
    39: {
        "甘肃地质工程实验室有限责任公司": "A包",
        "北京瀚禹信息科技有限公司": "B包",
        "兰州大学": "C包",
        "北京林淼生态环境技术有限公司": "D包",
    },
}

BAD_STATUS = "present"
GOOD_STATUS = "multi_value"


def find_flattened_groups(con: sqlite3.Connection) -> list[dict]:
    """找出「同 (tender_id, field_name) 多行、但被标成 present 导致压平」的组。

    判据（三条同时满足才算命中，避免误伤正常的版本迭代数据）：
      1) 该 (tender_id, field_name) 行数 > 1；
      2) 组内 field_status **全部**为 present（即没被标成 multi_value）；
      3) 组内 is_current 之和 < 行数（即确实发生了压平）。
    """
    cur = con.cursor()
    groups = cur.execute(
        """select tender_id, field_name, count(*) as n
           from extracted_fields
           group by tender_id, field_name
           having count(*) > 1
           order by tender_id, field_name"""
    ).fetchall()

    hits: list[dict] = []
    for tid, fname, n in groups:
        rows = cur.execute(
            """select id, field_status, is_current, lot_id, raw_value, value_count
               from extracted_fields
               where tender_id = ? and field_name = ?
               order by id""",
            (tid, fname),
        ).fetchall()
        statuses = {r[1] for r in rows}
        kept = sum(1 for r in rows if r[2])
        if statuses == {BAD_STATUS} and kept < n:
            hits.append(
                {
                    "tender_id": tid,
                    "field_name": fname,
                    "row_count": n,
                    "current_count": kept,
                    "flattened": n - kept,
                    "rows": [
                        {
                            "id": r[0],
                            "field_status": r[1],
                            "is_current": bool(r[2]),
                            "lot_id": r[3],
                            "raw_value": r[4],
                            "value_count": r[5],
                        }
                        for r in rows
                    ],
                }
            )
    return hits


def plan_lot_fill(hits: list[dict]) -> list[dict]:
    """按 VERIFIED_WINNER_LOT 生成 lot_id 补齐计划（只针对已核验的组）。"""
    plan: list[dict] = []
    for g in hits:
        mapping = VERIFIED_WINNER_LOT.get(g["tender_id"])
        if not mapping or g["field_name"] != "winner_name":
            continue
        missing = [r for r in g["rows"] if r["lot_id"] is None]
        if not missing:
            continue
        # 安全校验：组内 raw_value 必须与已核验映射完全对得上，否则整组跳过并告警
        values = [r["raw_value"] for r in g["rows"]]
        if set(values) != set(mapping):
            print(
                f"  [跳过 lot_id 补齐] tender={g['tender_id']} {g['field_name']}："
                f"组内值与已核验映射不一致，需人工复核"
            )
            print(f"    组内: {sorted(values)}")
            print(f"    映射: {sorted(mapping)}")
            continue
        for r in missing:
            plan.append(
                {
                    "id": r["id"],
                    "lot_id": mapping[r["raw_value"]],
                    "raw_value": r["raw_value"],
                }
            )
    return plan


def backup_db() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = BACKUP_DIR / f"bidagent_before_multivalue_{stamp}.db"
    shutil.copy2(DB, dst)
    return dst


def main() -> int:
    ap = argparse.ArgumentParser(description="订正被压平的多值字段（默认体检，不改库）")
    ap.add_argument("--apply", action="store_true", help="执行写入（默认只体检）")
    ap.add_argument("--no-backup", action="store_true", help="跳过整库备份（不推荐）")
    args = ap.parse_args()

    if not DB.exists():
        print(f"[ERROR] 找不到数据库：{DB}")
        return 1

    con = sqlite3.connect(DB)
    hits = find_flattened_groups(con)
    lot_plan = plan_lot_fill(hits)

    print("=" * 62)
    print("多值字段被压平 —— 体检" + ("（--apply 将写入）" if args.apply else "（不改库）"))
    print("=" * 62)
    total_rows = sum(g["row_count"] for g in hits)
    total_flat = sum(g["flattened"] for g in hits)
    print(f"命中组数            : {len(hits)}")
    print(f"涉及行数            : {total_rows}")
    print(f"被压平（会恢复）    : {total_flat}")
    print(f"lot_id 待补齐行数   : {len(lot_plan)}")
    print()
    for g in hits:
        print(
            f"· tender={g['tender_id']} {g['field_name']}："
            f"{g['row_count']} 行 → 当前仅 {g['current_count']} 行 is_current=1"
            f"（恢复 {g['flattened']} 行）"
        )
        for r in g["rows"]:
            mark = "  " if r["is_current"] else "✗ "
            print(
                f"    {mark}id={r['id']:<5} is_current={int(r['is_current'])} "
                f"lot_id={str(r['lot_id']):<5} {r['raw_value']}"
            )
    if lot_plan:
        print()
        print("lot_id 补齐计划（依据原文「四、主要标的信息」表）：")
        for p in lot_plan:
            print(f"    id={p['id']}  {p['raw_value']} → lot_id={p['lot_id']}")

    # 导出体检报告（无论是否 apply 都留档）
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report = REPORT_DIR / f"flattened_fields_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    report.write_text(
        json.dumps(
            {"db": str(DB), "groups": hits, "lot_fill_plan": lot_plan},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print()
    print(f"已导出体检报告 → {report}")

    if not args.apply:
        if hits:
            print()
            print("（体检模式，未修改数据库。加 --apply 执行订正。）")
        else:
            print()
            print("[OK] 未发现被压平的多值字段。")
        con.close()
        return 0

    if not hits:
        print()
        print("[OK] 无需订正。")
        con.close()
        return 0

    if not args.no_backup:
        dst = backup_db()
        print(f"已备份整库 → {dst}")

    cur = con.cursor()
    changed_status = 0
    for g in hits:
        ids = [r["id"] for r in g["rows"]]
        cur.executemany(
            "update extracted_fields set field_status = ?, is_current = 1 where id = ?",
            [(GOOD_STATUS, i) for i in ids],
        )
        changed_status += len(ids)
    changed_lot = 0
    for p in lot_plan:
        cur.execute(
            "update extracted_fields set lot_id = ? where id = ? and lot_id is null",
            (p["lot_id"], p["id"]),
        )
        changed_lot += cur.rowcount
    con.commit()

    # 回读验证：写库后重新体检，必须归零
    remaining = find_flattened_groups(con)
    print()
    print(f"已订正 field_status/is_current : {changed_status} 行")
    print(f"已补齐 lot_id                  : {changed_lot} 行")
    print(f"回读复检：剩余命中组           : {len(remaining)}  (应为 0)")
    con.close()

    if remaining:
        print("[FAIL] 回读仍有命中，请人工复核。")
        return 2
    print("[OK] 订正完成。建议接着跑：python scripts/check_front_back_alignment.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
