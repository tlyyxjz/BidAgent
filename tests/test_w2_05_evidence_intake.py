"""W2-05 22 篇全量入库验证集成测试。

加载 _w2_annotations/ 下全部 22 篇金标，验证入库前的数据完整性。
"""
import json
from pathlib import Path

import pytest

ANNOTATIONS_DIR = Path(__file__).parent.parent / "_w2_annotations"
RAW_DIR = Path(__file__).parent.parent / "_w2_raw"


def _load_all_gold_files():
    """加载全部金标文件。

    文件命名有两种约定：
      - 旧版: annotation_<prefix>_A.json (annotator 后缀)
      - 新版: annotation_<prefix>_<hash>.json (hash 后缀)
    两者 annotator_id 均为 "A"，统一用 annotation_*.json 匹配。
    """
    files = sorted(ANNOTATIONS_DIR.glob("annotation_*.json"))
    assert len(files) >= 22, f"金标文件数 {len(files)} < 22"
    return files


def _strip_hash_suffix(doc_id):
    """剥离 doc_id 末尾的哈希/时间戳后缀，找到对应的原文文件名。

    doc_id 形如:
      - "02_tender_002" (无后缀，直接匹配)
      - "award_05_43a28fb1c8af" (8+ 位 hex 哈希)
      - "correction_05_a87cdaff0797_1784972968145" (hex 哈希 + 13 位时间戳)
    原文文件分别为 02_tender_002.txt / award_05.txt / correction_05.txt
    """
    raw_path = RAW_DIR / f"{doc_id}.txt"
    if raw_path.exists():
        return raw_path
    parts = doc_id.split("_")
    while len(parts) > 1:
        last = parts[-1]
        is_hex_hash = len(last) >= 8 and all(
            c in "0123456789abcdef" for c in last.lower()
        )
        is_timestamp = last.isdigit() and len(last) >= 13
        if is_hex_hash or is_timestamp:
            parts.pop()
            candidate = "_".join(parts)
            raw_path = RAW_DIR / f"{candidate}.txt"
            if raw_path.exists():
                return raw_path
        else:
            break
    return None


def _load_raw_text(doc_id):
    """加载原文。"""
    raw_path = _strip_hash_suffix(doc_id)
    if raw_path is None or not raw_path.exists():
        pytest.skip(f"原文不存在: {doc_id}")
    return raw_path.read_text(encoding="utf-8")


class TestW205GoldIntake:
    """W2-05 22 篇金标全量入库验证。"""

    def test_22_gold_files_loaded(self):
        """验证 22 篇金标全部可加载。"""
        files = _load_all_gold_files()
        assert len(files) == 22, f"期望 22 篇，实际 {len(files)}"

    @pytest.mark.parametrize("gold_file", _load_all_gold_files())
    def test_gold_file_integrity(self, gold_file):
        """逐篇验证金标完整性。"""
        with open(gold_file, encoding="utf-8") as f:
            gold = json.load(f)

        # document_id 与文件名一致
        doc_id = gold["document_id"]
        assert doc_id in gold_file.name

        # 至少 5 个核心字段
        field_names = {f["field_name"] for f in gold["fields"]}
        required = {"project_identifier", "purchaser_name", "winner_name", "amount", "publish_date"}
        assert required.issubset(field_names), f"{doc_id} 缺少字段: {required - field_names}"

        # 验证证据偏移量
        txt = _load_raw_text(doc_id)
        for field in gold["fields"]:
            for value in field.get("values", []):
                for span in value.get("acceptable_evidence_spans", []):
                    assert span["start"] < span["end"], f"{doc_id}.{field['field_name']} span start>=end"
                    assert span["text"], f"{doc_id}.{field['field_name']} span text 空"
                    # 验证偏移量与原文一致
                    actual = txt[span["start"]:span["end"]]
                    assert actual == span["text"], (
                        f"{doc_id}.{field['field_name']} 偏移量错误: "
                        f"期望 {span['text']!r} 实际 {actual!r}"
                    )

    def test_amount_budget_values(self):
        """验证 amount 字段 budget 值的 amount_type。"""
        files = _load_all_gold_files()
        budget_count = 0
        for gold_file in files:
            with open(gold_file, encoding="utf-8") as f:
                gold = json.load(f)
            for field in gold["fields"]:
                if field["field_name"] != "amount":
                    continue
                for value in field.get("values", []):
                    if value.get("amount_type") == "budget":
                        budget_count += 1
                        assert value["raw_value"], "budget 值 raw_value 空"
        # 至少 3 篇有 budget（04/05/06_award）
        assert budget_count >= 3, f"budget 值数 {budget_count} < 3"

    def test_total_fields_and_evidences(self):
        """汇总断言：22 篇 132+ 字段 193+ 证据。"""
        files = _load_all_gold_files()
        total_fields = 0
        total_evidences = 0
        for gold_file in files:
            with open(gold_file, encoding="utf-8") as f:
                gold = json.load(f)
            total_fields += len(gold["fields"])
            for field in gold["fields"]:
                for value in field.get("values", []):
                    total_evidences += len(value.get("acceptable_evidence_spans", []))
        assert total_fields >= 132, f"总字段数 {total_fields} < 132"
        assert total_evidences >= 193, f"总证据数 {total_evidences} < 193"
