# -*- coding: utf-8 -*-
"""nv1 守卫：金标审计修正（2026-08-05）不回退。

审计修正的 13 篇文档（27 个字段）必须保持：
- 带 audit_note 追溯标记
- 修正字段 status=present 且值可在原文定位
防止后续金标批量操作误覆盖。
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GOLD_PATH = ROOT / "tests" / "fixtures" / "gold" / "gold_dataset_v4.json"
RAW_DIRS = {"w3": "_w3_raw", "w4": "_w4_raw", "w5": "_w5_raw"}

# document_id -> 修正为 present 的字段
AUDITED = {
    "w4_award_007": ["project_identifier", "purchaser_name", "winner_name",
                     "amount", "publish_date"],
    "w4_tender_030": ["publish_date", "purchaser_name", "project_identifier",
                      "amount", "bid_deadline"],
    "w4_tender_097": ["publish_date", "purchaser_name", "project_identifier",
                      "amount", "bid_deadline"],
    "w5_award_034": ["project_identifier", "purchaser_name", "publish_date"],
    "w3_correction_043": ["bid_deadline"],
    "w5_tender_022": ["publish_date"],
    "w5_tender_029": ["publish_date"],
    "w5_tender_034": ["publish_date"],
    "w5_tender_064": ["publish_date"],
    "w5_tender_090": ["publish_date"],
    "w5_tender_094": ["publish_date"],
    "w5_tender_096": ["publish_date"],
    "w5_tender_098": ["publish_date"],
}


@pytest.fixture(scope="module")
def gold_index():
    data = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    return {i["document_id"]: i for i in data["annotations"]
            if isinstance(i, dict) and "document_id" in i}


def _field_spec(item: dict, fn: str) -> dict:
    """兼容 dict/list 两种 fields 形态。"""
    spec = item["fields"]
    if isinstance(spec, dict):
        return spec[fn]
    for f in spec:
        if isinstance(f, dict) and f.get("field_name") == fn:
            return f
    raise KeyError(fn)


def _status_of(f: dict) -> str:
    return f.get("status") or f.get("gold_status")


def _values_of(f: dict) -> list:
    vals = f.get("values") or []
    out = []
    for v in vals:
        if isinstance(v, dict):
            out.append(v.get("raw_value") or "")
        elif isinstance(v, str):
            out.append(v)
    return out


class TestNv1GoldAudit:
    """金标审计修正守卫。"""

    def test_all_audited_docs_have_note(self, gold_index):
        for did in AUDITED:
            item = gold_index[did]
            assert "nv1-audit" in item.get("audit_note", ""), \
                f"{did} 缺少审计追溯标记"

    def test_audited_fields_present_with_value(self, gold_index):
        for did, fields in AUDITED.items():
            item = gold_index[did]
            for fn in fields:
                f = _field_spec(item, fn)
                status = _status_of(f)
                assert status == "present", f"{did}.{fn} 应为 present，实际 {status}"
                assert any(_values_of(f)), f"{did}.{fn} present 但无值"

    def test_audited_values_locatable_in_raw(self, gold_index):
        """修正值必须能在原文定位（证据可重放）。"""
        for did, fields in AUDITED.items():
            item = gold_index[did]
            fname = item.get("file") or f"{did}.txt"
            raw = (ROOT / RAW_DIRS[did.split("_")[0]] / fname).read_text(encoding="utf-8")
            for fn in fields:
                val = _values_of(_field_spec(item, fn))[0]
                assert val in raw, f"{did}.{fn} 值 [{val}] 无法在原文定位"
