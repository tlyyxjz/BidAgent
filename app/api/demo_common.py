"""W3 Demo 共享常量与辅助加载函数。

提供：
- _GOLD_RAW_DIR / _GOLD_ANNOT_DIR：本地黄金样本目录（W2 产物）
- FIELD_LABELS：字段中文标签
- _load_annotation / _load_raw：按 doc_id 加载标注 / 原文
"""

from __future__ import annotations

import json
from pathlib import Path

_GOLD_RAW_DIR = Path(
    r"C:\Users\Lenovo\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent"
    r"\work-mode-projects\6a57291a0778ce48bfe693d2\_w2_raw"
)
_GOLD_ANNOT_DIR = Path(
    r"C:\Users\Lenovo\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent"
    r"\work-mode-projects\6a57291a0778ce48bfe693d2\_w2_annotations"
)

FIELD_LABELS = {
    "project_identifier": "项目编号",
    "purchaser_name": "采购人",
    "winner_name": "中标人",
    "amount": "金额",
    "publish_date": "发布日期",
    "bid_deadline": "投标截止日期",
}


def _load_annotation(doc_id: str) -> dict | None:
    if not _GOLD_ANNOT_DIR.exists():
        return None
    for f in _GOLD_ANNOT_DIR.glob("annotation_*.json"):
        name = f.stem[len("annotation_"):]
        if doc_id == name or doc_id.startswith(name) or name.startswith(doc_id):
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("document_id") == doc_id:
                return data
        except Exception:
            continue
    return None


def _load_raw(doc_id: str) -> str | None:
    if not _GOLD_RAW_DIR.exists():
        return None
    for f in _GOLD_RAW_DIR.glob("*.txt"):
        stem = f.stem
        if doc_id == stem or doc_id.startswith(stem) or stem.startswith(doc_id):
            return f.read_text(encoding="utf-8")
    return None
