# -*- coding: utf-8 -*-
"""守卫测试：一键可信重放 scripts/demo_trust_replay.py。

口径：
- 证据切片重放必须严格相等（core_content[start:end) ≡ 证据文本），
  不符即拒绝（宁可少给、不可编造）
- 0 证据不算失败（无证据可重放），存证哈希缺失/不一致才算失败
- 自动选公告优先"有跨源佐证且证据全部可重放"
- 报告 HTML 必须包含五步关键区块且字段值已转义
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO))

import pytest  # noqa: E402

import demo_trust_replay as dap  # noqa: E402


def _build_db(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE tenders (
            id INTEGER PRIMARY KEY, project_name TEXT, bid_number TEXT,
            notice_type TEXT, source_platform TEXT, source_url TEXT,
            core_content TEXT, content_sha256 TEXT, created_at TEXT,
            simhash INTEGER);
        CREATE TABLE extracted_fields (
            id INTEGER PRIMARY KEY, tender_id INTEGER, field_name TEXT,
            field_status TEXT, raw_value TEXT, support_level TEXT,
            is_current INTEGER DEFAULT 1);
        CREATE TABLE evidence (
            id INTEGER PRIMARY KEY, tender_id INTEGER, evidence_text TEXT,
            raw_start INTEGER, raw_end INTEGER, match_method TEXT,
            confidence REAL, verified INTEGER);
        CREATE TABLE field_evidence_links (
            id INTEGER PRIMARY KEY, field_id INTEGER, evidence_id INTEGER,
            evidence_role TEXT, sequence INTEGER);
    """)
    conn.commit()


def _add_tender(conn, tid, platform, bid_number, title, core,
                sha=None) -> None:
    sha = sha if sha is not None else dap.sha256_hex(core)
    conn.execute(
        "INSERT INTO tenders (id, project_name, bid_number, notice_type, "
        "source_platform, source_url, core_content, content_sha256, "
        "created_at, simhash) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (tid, title, bid_number, "中标公告", platform,
         f"http://example.test/{tid}", core, sha, "2026-08-05 12:00:00", None))


def _add_evidence(conn, tid, field_name, value, ev_text, start, end,
                  fid=None, eid=None) -> None:
    fid = fid or (100 + tid)
    eid = eid or (200 + tid)
    conn.execute(
        "INSERT INTO extracted_fields (id, tender_id, field_name, "
        "field_status, raw_value, support_level, is_current) "
        "VALUES (?,?,?,?,?,?,1)",
        (fid, tid, field_name, "present", value, "direct"))
    conn.execute(
        "INSERT INTO evidence (id, tender_id, evidence_text, raw_start, "
        "raw_end, match_method, confidence, verified) VALUES (?,?,?,?,?,?,?,?)",
        (eid, tid, ev_text, start, end, "exact", 0.95, 1))
    conn.execute(
        "INSERT INTO field_evidence_links (field_id, evidence_id, "
        "evidence_role, sequence) VALUES (?,?,?,?)",
        (fid, eid, "value", 0))
    conn.commit()


@pytest.fixture()
def memdb(tmp_path):
    path = tmp_path / "demo.db"
    conn = sqlite3.connect(str(path))
    _build_db(conn)
    yield path, conn
    conn.close()


# ---------------------------------------------------------------------------
# 证据重放
# ---------------------------------------------------------------------------

def test_replay_all_pass_and_hash_ok(memdb):
    path, conn = memdb
    core = "项目编号：ABC-123，预算金额：100.00 万元，采购单位东南大学。"
    _add_tender(conn, 1, "ccgp", "ABC-123", "测试项目A", core)
    s = core.index("预算金额：100.00 万元")
    _add_evidence(conn, 1, "amount", "100.00 万元",
                  "预算金额：100.00 万元", s, s + len("预算金额：100.00 万元"))
    conn.commit()

    r = dap.run_demo(1, path)
    assert r["ok"] is True
    assert r["ev_ok"] == 1 and len(r["evidences"]) == 1
    assert r["evidences"][0]["replay_ok"] is True
    assert r["hash_ok"] is True


def test_slice_mismatch_is_rejected(memdb):
    """切片不符必须判失败并给出原因（宁可少给、不可编造）。"""
    path, conn = memdb
    core = "原文已被清洗，历史证据偏移失效。"
    _add_tender(conn, 2, "ccgp", "XYZ-9", "测试项目B", core)
    _add_evidence(conn, 2, "amount", "999 万元", "预算金额：999 万元", 0, 11)
    conn.commit()

    r = dap.run_demo(2, path)
    assert r["ok"] is False
    ev = r["evidences"][0]
    assert ev["replay_ok"] is False
    assert "fail_reason" in ev and ev["fail_reason"]
    html_out = dap.render_report(r)
    assert "重放失败" in html_out


def test_zero_evidence_is_not_failure(memdb):
    path, conn = memdb
    core = "只有原文、没有证据的公告。"
    _add_tender(conn, 3, "tianjin", "T-1", "测试项目C", core)
    conn.commit()

    r = dap.run_demo(3, path)
    assert r["ok"] is True
    assert r["evidences"] == []
    assert "库内无存证证据" in dap.render_report(r)


def test_hash_mismatch_detected(memdb):
    path, conn = memdb
    core = "存证后原文被改动。"
    _add_tender(conn, 4, "ccgp", "H-1", "测试项目D", core, sha="0" * 64)
    conn.commit()

    r = dap.run_demo(4, path)
    assert r["hash_ok"] is False
    assert r["ok"] is False


# ---------------------------------------------------------------------------
# 自动选择
# ---------------------------------------------------------------------------

def test_pick_prefers_corroborated_and_replayable(memdb):
    path, conn = memdb
    # A（id=1）：证据可重放 + 有跨源孪生（同编号不同平台）→ 应优先选中
    core_a = "项目编号：SAME-1。预算金额：50 万元。"
    _add_tender(conn, 1, "tianjin", "SAME-1", "跨源项目", core_a)
    s = core_a.index("预算金额：50 万元")
    _add_evidence(conn, 1, "amount", "50 万元",
                  "预算金额：50 万元", s, s + len("预算金额：50 万元"),
                  fid=101, eid=201)
    _add_tender(conn, 9, "ccgp", "SAME-1", "跨源项目", core_a)

    # B（id=2）：证据更多但切片全部失效 → 不应被选中
    core_b = "清洗后的文本。"
    _add_tender(conn, 2, "ccgp", "LONE-2", "失效项目", core_b)
    _add_evidence(conn, 2, "amount", "1 万元", "预算金额：1 万元", 0, 9,
                  fid=102, eid=202)
    _add_evidence(conn, 2, "purchaser_name", "某单位", "采购单位某单位",
                  40, 47, fid=103, eid=203)
    conn.commit()

    cur = conn.cursor()
    refs = dap.load_all_refs(cur)
    assert dap.pick_default_id(cur, refs) == 1


# ---------------------------------------------------------------------------
# 报告渲染
# ---------------------------------------------------------------------------

def test_render_report_contains_five_steps(memdb):
    path, conn = memdb
    core = "项目编号：R-1，采购单位<测试大学>。"
    _add_tender(conn, 5, "ccgp", "R-1", "渲染项目 <script>", core)
    s = core.index("采购单位")
    _add_evidence(conn, 5, "purchaser_name", "<测试大学>",
                  "采购单位<测试大学>", s, s + len("采购单位<测试大学>"))
    conn.commit()

    r = dap.run_demo(5, path)
    out = dap.render_report(r)
    for marker in ("一键自动演示报告", "判定", "证据 · 现场重放",
                   "佐证 · 跨源交叉验证", "存证 · SHA-256"):
        assert marker in out
    # 字段值必须转义，防止注入
    assert "<测试大学>" not in out
    assert "&lt;测试大学&gt;" in out
    assert "<script>" not in out
