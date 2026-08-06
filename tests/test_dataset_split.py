"""v4.1 §10.1 数据集划分完整性测试。"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPLIT = ROOT / "tests" / "fixtures" / "gold" / "dataset_split.json"
V4 = ROOT / "tests" / "fixtures" / "gold" / "gold_dataset_v4.json"
FROZEN = ROOT / "tests" / "fixtures" / "gold" / "gold_frozen_v1.json"


def _load():
    return json.loads(SPLIT.read_text(encoding="utf-8"))


def test_split_disjoint_and_complete():
    s = _load()
    t, c, d = set(s["test"]), set(s["calibration"]), set(s["dev"])
    assert not (t & c) and not (t & d) and not (c & d)
    v4 = json.loads(V4.read_text(encoding="utf-8"))
    all_ids = {
        a["document_id"] for a in v4["annotations"]
        if isinstance(a, dict) and a.get("document_id") and not a.get("_is_meta")
    }
    assert t | c | d == all_ids


def test_test_set_matches_frozen():
    s = _load()
    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
    frozen_ids = {a["document_id"] for a in frozen["annotations"] if a.get("document_id")}
    # test 集 = 冻结集 ∩ v4 合集
    assert set(s["test"]) <= frozen_ids


def test_calibration_size_in_v41_range():
    """v4.1 §10.1: 校准集 80~100 篇。"""
    s = _load()
    assert 80 <= len(s["calibration"]) <= 100
