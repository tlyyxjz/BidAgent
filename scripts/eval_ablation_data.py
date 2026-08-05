"""W2-08/W4 消融实验数据加载（金标 + 原文）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent

WORK_DIR = Path(r"C:\Users\Lenovo\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\work-mode-projects\6a57291a0778ce48bfe693d2")
RAW_DIR = WORK_DIR / "_w2_raw"
ANNOT_DIR = WORK_DIR / "_w2_annotations"

# W3 数据源路径
BIDAGENT_ROOT = Path(r"C:\Users\Lenovo\Desktop\BidAgent")
W3_RAW_DIR = BIDAGENT_ROOT / "_w3_raw"
W3_GOLD_PATH = BIDAGENT_ROOT / "tests" / "fixtures" / "gold" / "k3_annotations_batch2.json"
W3_OUTPUT_DIR = BIDAGENT_ROOT / "_w3_outputs"

from scripts.eval_ablation_types import GoldDoc, GoldField


def load_gold_all_w3() -> list[GoldDoc]:
    """从 W3 gold JSON (k3_annotations_batch2.json) 加载全部金标。

    JSON 结构: 顶部一个 _is_meta 头, 其余 99 项为公告金标。
    """
    with open(W3_GOLD_PATH, encoding="utf-8") as f:
        data = json.load(f)
    docs = []
    for item in data:
        if not isinstance(item, dict) or item.get("_is_meta"):
            continue
        fields = [
            GoldField(
                field_name=f["field_name"],
                gold_status=f["gold_status"],
                values=f.get("values", []),
            )
            for f in item.get("fields", [])
        ]
        docs.append(GoldDoc(
            document_id=item["document_id"],
            file=item.get("file", ""),
            fields=fields,
        ))
    return docs


_GOLD_CACHE_W3: dict[str, GoldDoc] = {}


def _load_w3_gold_cache() -> dict[str, GoldDoc]:
    """惰性加载 W3 gold 到缓存 (按 document_id 索引)。"""
    if not _GOLD_CACHE_W3:
        for gd in load_gold_all_w3():
            _GOLD_CACHE_W3[gd.document_id] = gd
    return _GOLD_CACHE_W3


def load_gold_doc(doc_prefix: str, source: str = "w2") -> Optional[GoldDoc]:
    """加载金标。

    source="w2" 从 _w2_annotations 目录按 prefix glob 查找单独 JSON 文件;
    source="w3" 从 k3_annotations_batch2.json 按 document_id 直接匹配
    (W3 的 doc_prefix 就是 document_id, 如 w3_tender_001)。
    """
    if source == "w3":
        cache = _load_w3_gold_cache()
        return cache.get(doc_prefix)
    matches = list(ANNOT_DIR.glob(f"annotation_{doc_prefix}*.json"))
    if not matches:
        return None
    with open(matches[0], encoding="utf-8") as f:
        data = json.load(f)
    return GoldDoc(
        document_id=data["document_id"],
        file=matches[0].name,
        fields=[
            GoldField(
                field_name=f["field_name"],
                gold_status=f["gold_status"],
                values=f.get("values", []),
            )
            for f in data["fields"]
        ],
    )


def load_raw_text(doc_prefix: str, source: str = "w2") -> Optional[str]:
    """加载原文。source="w3" 从 _w3_raw, 否则从 _w2_raw。"""
    if source == "w3":
        p = W3_RAW_DIR / f"{doc_prefix}.txt"
    else:
        p = RAW_DIR / f"{doc_prefix}.txt"
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")
