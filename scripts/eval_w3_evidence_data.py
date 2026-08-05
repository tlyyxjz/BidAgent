"""W3-03 证据定位指标评测数据加载。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent

RAW_DIR = ROOT / "_w3_raw"
GOLD_PATH = ROOT / "tests" / "fixtures" / "gold" / "k3_annotations_batch2.json"
OUTPUT_DIR = ROOT / "_w3_outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

from scripts.eval_w3_evidence_types import (
    GoldDoc,
    GoldEvidenceSpan,
    GoldField,
    IOU_THRESHOLD,
)


def load_gold_all() -> list[GoldDoc]:
    """加载K3标注的全部90篇金标。"""
    with open(GOLD_PATH, encoding="utf-8") as f:
        data = json.load(f)
    docs = []
    for item in data:
        if not isinstance(item, dict) or item.get("_is_meta"):
            continue
        fields = []
        for f in item.get("fields", []):
            evidences = []
            for v in f.get("values", []):
                for span in v.get("acceptable_evidence_spans", []):
                    evidences.append(GoldEvidenceSpan(
                        start=span["start"], end=span["end"],
                        text=span["text"],
                    ))
            fields.append(GoldField(
                field_name=f["field_name"],
                gold_status=f["gold_status"],
                evidences=evidences,
            ))
        docs.append(GoldDoc(
            document_id=item["document_id"],
            file=item.get("file", ""),
            notice_type=item.get("notice_type", "unknown"),
            fields=fields,
        ))
    return docs


def load_raw_text(doc_id: str) -> Optional[str]:
    p = RAW_DIR / f"{doc_id}.txt"
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


def get_body_offset(raw_text: str) -> int:
    """获取"## "标记位置（金标spans基准）。"""
    return raw_text.find("## ")
