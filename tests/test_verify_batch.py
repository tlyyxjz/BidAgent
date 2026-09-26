# -*- coding: utf-8 -*-
"""批量核验测试：表头映射 + 逐条判定 + 输出 CSV。"""
import csv
import io
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import verify_batch as vb  # noqa: E402

DB = os.environ.get("BIDAGENT_DB") or str(REPO / "data" / "bidagent.db")
HAS_DB = Path(DB).exists()
requires_db = pytest.mark.skipif(not HAS_DB, reason="bidagent.db 不存在")


def test_map_header_cn_and_en():
    assert vb.map_header("项目编号") == "bid_number"
    assert vb.map_header("中标金额") == "amount"
    assert vb.map_header("中标人") == "winner"
    assert vb.map_header("采购人") == "purchaser"
    assert vb.map_header("bid_number") == "bid_number"
    assert vb.map_header("成交供应商") == "winner"
    assert vb.map_header("不认识的列") is None


@requires_db
def test_process_rows_three_verdicts():
    rows = [
        {"bid_number": "GHHX2026000062", "amount": "22.532万元",
         "winner": "青岛盛信合创电子技术有限公司", "purchaser": "中国电子口岸数据中心青岛分中心"},
        {"bid_number": "GHHX2026000062", "amount": "500万元",
         "winner": "青岛盛信合创电子技术有限公司"},
        {"bid_number": "XXTEST20260001", "amount": "100万元"},
    ]
    out = vb.process_rows(rows, DB)
    assert [r["结论"] for r in out] == ["真", "存疑", "伪造"]
    assert out[0]["匹配公告"]
    assert out[0]["来源URL"].startswith("http")


@requires_db
def test_process_rows_missing_bid_number_is_fake():
    out = vb.process_rows([{"bid_number": ""}], DB)
    assert out[0]["结论"] == "伪造"


def test_csv_roundtrip(tmp_path):
    """表头映射 + 读写 CSV（用 monkeypatch 掉 verify 避免依赖 DB）。

    用 pytest 的 tmp_path 而非硬编码本机路径：原先写死 D:\\Lenovo\\Documents，
    在 CI（ubuntu）上父目录不存在，write_text 直接 FileNotFoundError。
    """
    import verify_batch as vb_mod

    csv_in = tmp_path / "_tmp_batch.csv"
    csv_in.write_text(
        "项目编号,中标金额,中标人,采购人\nGHHX2026000062,22.532万元,某公司,某单位\n",
        encoding="utf-8-sig",
    )
    try:
        with open(csv_in, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames
            mapped = {h: vb.map_header(h) for h in headers}
            rows = [{mapped[h]: v for h, v in row.items() if mapped.get(h)} for row in reader]

        orig = vb_mod.verify_award

        def fake(bid_number, amount="", winner="", purchaser="", db_path=None):
            return {"verdict": "verified", "reason": "ok",
                    "evidence": {"project_name": "测试公告", "source_url": "http://x"}}

        vb_mod.verify_award = fake
        try:
            out = vb.process_rows(rows, "unused")
        finally:
            vb_mod.verify_award = orig

        assert out[0]["结论"] == "真"
        assert out[0]["bid_number"] == "GHHX2026000062"
    finally:
        csv_in.unlink(missing_ok=True)
