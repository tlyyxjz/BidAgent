"""探查 bidagent.db 的真实 schema，供 verify_award 核对列名。

用法: python scripts/inspect_schema.py [db_path]
"""
import sqlite3
import sys
from pathlib import Path

DB = Path(sys.argv[1]) if len(sys.argv) > 1 else (
    Path(__file__).resolve().parent.parent / "data" / "bidagent.db"
)
conn = sqlite3.connect(str(DB))
cur = conn.cursor()

print("== tables ==")
for (name,) in cur.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
):
    print(name)

for (name,) in cur.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
):
    print(f"\n== schema of {name} ==")
    for row in cur.execute(f"PRAGMA table_info({name})"):
        print("  ", row[1], row[2])
    cnt = cur.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
    print(f"  rows: {cnt}")

# tenders 关键列抽查
print("\n== tenders sample ==")
cols = [r[1] for r in cur.execute("PRAGMA table_info(tenders)")]
print("tenders cols:", cols)
if "bid_number" in cols:
    cur.execute(
        "SELECT bid_number, win_amount, tender_org, win_company "
        "FROM tenders WHERE bid_number IS NOT NULL AND bid_number != '' LIMIT 5"
    )
    for row in cur.fetchall():
        print("  ", row)

print("\n== notice_participants sample ==")
cols2 = [r[1] for r in cur.execute("PRAGMA table_info(notice_participants)")]
print("notice_participants cols:", cols2)
for row in cur.execute(
    "SELECT participant_role, COUNT(*) FROM notice_participants GROUP BY participant_role"
):
    print("  role count:", row)

# winner 样例：能否用 notice_id 关联到 tenders.id
print("\n== winner sample (join test) ==")
try:
    for row in cur.execute(
        "SELECT p.notice_id, p.normalized_name, p.participant_role, t.bid_number, t.win_amount "
        "FROM notice_participants p JOIN tenders t ON p.notice_id = t.id "
        "WHERE p.participant_role='winner' LIMIT 5"
    ):
        print("  ", row)
except Exception as e:
    print("  join failed:", e)

# 检查是否有 win_amount 且 bid_number 都有的公告（好用来做真样例）
print("\n== notices with both bid_number and win_amount and winner ==")
try:
    cur.execute(
        "SELECT COUNT(*) FROM tenders t "
        "WHERE t.bid_number IS NOT NULL AND t.bid_number != '' "
        "AND t.win_amount IS NOT NULL AND t.win_amount != ''"
    )
    print("  tenders with bid_number+win_amount:", cur.fetchone()[0])
    cur.execute(
        "SELECT COUNT(*) FROM tenders t "
        "JOIN notice_participants p ON p.notice_id = t.id AND p.participant_role='winner'"
    )
    print("  tenders with winner join:", cur.fetchone()[0])
except Exception as e:
    print("  check failed:", e)

conn.close()
