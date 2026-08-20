# -*- coding: utf-8 -*-
"""大件五（证据再加固）守卫测试：SHA-256 存证 + 生命周期链 + 重放命令。

分两层：
- 临时库单测（tmp_path，不依赖真实库）：存证写入/回填幂等/重放核验/链条构建
- 真实库集成（data/bidagent.db，存在才跑）：尽调单证据链含存证哈希与重放命令
"""
import hashlib
import sqlite3
from pathlib import Path

import pytest

from scripts.backfill_evidence_hashes import backfill, sha256_hex
from scripts.lifecycle_chain import build_lifecycle_chain
from scripts.replay_evidence import replay
from scripts.receivable_due_diligence import run_due_diligence

REPO = Path(__file__).resolve().parent.parent
REAL_DB = REPO / "data" / "bidagent.db"
requires_db = pytest.mark.skipif(not REAL_DB.exists(), reason="bidagent.db 不存在")

_CREATE = """
CREATE TABLE tenders (
    id INTEGER PRIMARY KEY,
    project_name TEXT,
    bid_number TEXT,
    win_amount NUMERIC(15,2),
    tender_org TEXT,
    win_company TEXT,
    notice_type TEXT,
    source_url TEXT,
    publish_time TEXT,
    core_content TEXT,
    source_raw_text TEXT,
    content_sha256 CHAR(64),
    raw_text_sha256 CHAR(64),
    created_at TEXT DEFAULT '2026-08-20 10:00:00'
)
"""


@pytest.fixture()
def temp_db(tmp_path):
    db = tmp_path / "mini.db"
    conn = sqlite3.connect(str(db))
    conn.execute(_CREATE)
    conn.commit()
    conn.close()
    return db


def _insert(db, rid, **kw):
    cols = ["id"] + list(kw.keys())
    conn = sqlite3.connect(str(db))
    conn.execute(
        f"INSERT INTO tenders ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' * len(cols))})",
        [rid] + list(kw.values()),
    )
    conn.commit()
    conn.close()


# ========== 1. 入库即存证（_build_tender） ==========


def test_build_tender_stores_sha256():
    from app.processors.tender_utils import _build_tender

    item = {
        "project_name": "存证测试项目",
        "core_content": "供应商名称：存证测试有限公司",
        "source_raw_text": "页面原文：供应商名称：存证测试有限公司",
        "notice_type": "award",
    }
    t = _build_tender(item, "http://example.gov.cn/x", "ccgp", None)
    assert t.content_sha256 == hashlib.sha256(
        "供应商名称：存证测试有限公司".encode("utf-8")
    ).hexdigest()
    assert t.raw_text_sha256 == hashlib.sha256(
        "页面原文：供应商名称：存证测试有限公司".encode("utf-8")
    ).hexdigest()


def test_build_tender_empty_content_no_hash():
    from app.processors.tender_utils import _build_tender

    t = _build_tender({"project_name": "空正文项目"}, "http://e.gov.cn", "ccgp", None)
    assert t.content_sha256 is None
    assert t.raw_text_sha256 is None


# ========== 2. 存量回填（幂等 + 空文本不落证） ==========


def test_backfill_fills_and_is_idempotent(temp_db):
    _insert(temp_db, 1, core_content="正文A", source_raw_text="原文A")
    _insert(temp_db, 2, core_content="", source_raw_text=None)

    stats = backfill(temp_db)
    assert stats["rows_touched"] == 1  # 空文本行不触碰
    assert stats["filled_content"] == 1

    conn = sqlite3.connect(str(temp_db))
    h1, r1 = conn.execute(
        "SELECT content_sha256, raw_text_sha256 FROM tenders WHERE id=1"
    ).fetchone()
    h2, r2 = conn.execute(
        "SELECT content_sha256, raw_text_sha256 FROM tenders WHERE id=2"
    ).fetchone()
    conn.close()
    assert h1 == sha256_hex("正文A") and r1 == sha256_hex("原文A")
    assert h2 is None and r2 is None  # 空文本不存证

    stats2 = backfill(temp_db)
    assert stats2["rows_touched"] == 0  # 幂等


# ========== 3. 重放核验（verified / tampered / no_evidence） ==========


def test_replay_verified(temp_db):
    _insert(temp_db, 1, core_content="正文A", source_raw_text="原文A")
    backfill(temp_db)
    result = replay(1, temp_db)
    assert result["status"] == "verified"
    assert len(result["checks"]) == 2


def test_replay_detects_tampering(temp_db):
    _insert(temp_db, 1, core_content="正文A", source_raw_text="原文A")
    backfill(temp_db)
    conn = sqlite3.connect(str(temp_db))
    conn.execute("UPDATE tenders SET core_content = '被篡改的正文' WHERE id=1")
    conn.commit()
    conn.close()
    result = replay(1, temp_db)
    assert result["status"] == "tampered"
    assert any(not c["match"] for c in result["checks"])


def test_replay_no_evidence_and_not_found(temp_db):
    _insert(temp_db, 1, core_content="", source_raw_text="")
    assert replay(1, temp_db)["status"] == "no_evidence"
    assert replay(999, temp_db)["status"] == "not_found"


# ========== 4. 生命周期链 ==========


def test_lifecycle_chain_orders_stages_and_reports_gaps(temp_db):
    _insert(temp_db, 1, project_name="链条项目中标公告", bid_number="CHAIN-001",
            notice_type="award", win_company="链条中标公司",
            publish_time="2026-08-02", core_content="中标正文")
    _insert(temp_db, 2, project_name="链条项目招标公告", bid_number="CHAIN-001",
            notice_type="tender", publish_time="2026-07-20", core_content="招标正文")
    backfill(temp_db)

    result = build_lifecycle_chain("chain-001", temp_db)  # 大小写归一
    assert result["status"] == "chained"
    assert [s["notice_type"] for s in result["stages"]] == ["tender", "award"]
    assert all(s["content_sha256"] for s in result["stages"])
    assert all("replay_evidence" in s["replay_cmd"] for s in result["stages"])
    gap_text = " ".join(result["gaps"])
    assert "更正" in gap_text and "合同" in gap_text
    assert "存证 SHA-256" in result["report_text"]
    assert "重放命令" in result["report_text"]


def test_lifecycle_chain_not_found(temp_db):
    result = build_lifecycle_chain("NO-SUCH-001", temp_db)
    assert result["status"] == "not_found"
    assert "无法证伪" in result["report_text"]


# ========== 5. 尽调单证据链升级（存证哈希 + 重放命令） ==========


def test_due_diligence_report_carries_evidence_cert(temp_db):
    raw = "供应商名称：存证链测试有限公司 中标金额：10万元"
    _insert(temp_db, 1, project_name="存证链测试项目中标公告",
            bid_number="CERT-2026-001", notice_type="award",
            win_company="存证链测试有限公司", win_amount=100000,
            tender_org="存证链采购人", source_url="http://e.gov.cn/cert",
            publish_time="2026-08-01", core_content=raw, source_raw_text=raw)
    backfill(temp_db)

    r = run_due_diligence(
        winner="存证链测试有限公司", amount="10万元",
        bid_number="CERT-2026-001", db_path=temp_db,
    )
    assert r["verdict"] == "verified"
    assert "存证 SHA-256" in r["report_text"]
    assert "重放命令" in r["report_text"]
    assert r["evidence_cert"]["content_sha256"] == sha256_hex(raw)


# ========== 6. 真实库集成（存在才跑） ==========


@requires_db
def test_real_db_due_diligence_report_has_cert():
    # 前置：真实库已完成迁移+回填（大件五收口动作）
    r = run_due_diligence(
        winner="青岛盛信合创电子技术有限公司",
        amount="22.532万元",
        bid_number="GHHX2026000062",
    )
    assert r["verdict"] == "verified"
    assert "存证 SHA-256" in r["report_text"]
    assert "重放命令" in r["report_text"]


@requires_db
def test_real_db_lifecycle_chain_multi_stage():
    # 库内真实多阶段编号（摸底确认存在 2 个阶段）
    result = build_lifecycle_chain("CFZCKQS-X-H-260035")
    assert result["status"] == "chained"
    assert len(result["stages"]) >= 2


# ========== 7. 演示 API 端点 ==========


@pytest.fixture(scope="module")
def api_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.demo_api import router

    app = FastAPI()
    app.include_router(router)
    assert any(
        r.path == "/api/demo/lifecycle-chain" for r in app.routes
    ), "demo_api 未挂接 lifecycle-chain 端点"
    return TestClient(app)


def test_api_lifecycle_chain_requires_number(api_client):
    resp = api_client.post("/api/demo/lifecycle-chain", json={})
    assert resp.status_code == 400


@requires_db
def test_api_lifecycle_chain_real_number(api_client):
    resp = api_client.post(
        "/api/demo/lifecycle-chain", json={"bid_number": "CFZCKQS-X-H-260035"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "chained"
    assert "重放命令" in data["report_text"]
