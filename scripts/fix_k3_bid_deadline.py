"""修复K3标注: 补bid_deadline字段 + 移除project_name(降为附加字段).

问题根因:
- K3用旧版提示词标注(含project_name, 缺bid_deadline)
- 我已修复提示词(commit a352250)对齐v4.1第七章7.1节, 但K3未用新版

修复策略:
1. tender: 从原文抽取投标截止时间, 补bid_deadline字段(present)
2. award: 无投标截止的标not_applicable
3. correction: 有则present, 无则not_applicable
4. project_name: 从核心字段移除(保留在附加字段, 不计入6类核心评测)

不修改:
- 现有字段值和证据spans
- document_id/file/notice_type等元数据
- annotation_version(追加修复标记)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLD_FILE = ROOT / "tests" / "fixtures" / "gold" / "k3_annotations_batch2.json"
RAW_DIR = ROOT / "_w3_raw"

# 投标截止时间正则 (按优先级)
DEADLINE_PATTERNS = [
    re.compile(r"投标截止时间[：:]\s*(20\d{2}[\-/年]\d{1,2}[\-/月]\d{1,2}[^。\n]*点[^。\n]*分[^。\n]*)"),
    re.compile(r"截止时间[：:]\s*(20\d{2}[\-/年]\d{1,2}[\-/月]\d{1,2}[^。\n]*点[^。\n]*分[^。\n]*)"),
    re.compile(r"开标时间[：:]\s*(20\d{2}[\-/年]\d{1,2}[\-/月]\d{1,2}[^。\n]*点[^。\n]*分[^。\n]*)"),
    re.compile(r"递交.*截止[：:]\s*(20\d{2}[\-/年]\d{1,2}[\-/月]\d{1,2}[^。\n]*)"),
    # 兜底: 日期+时间+开标
    re.compile(r"(20\d{2}年\d{1,2}月\d{1,2}日\s*\d{1,2}[:：]\d{2}[^。\n]*)(?:开标|截止)"),
]

# 日期+时间提取 (用于兜底)
DATETIME_RE = re.compile(r"(20\d{2}年\d{1,2}月\d{1,2}日\s*\d{1,2}[:：]\d{2})")


def extract_deadline(content: str) -> tuple[str | None, int | None, int | None]:
    """从原文抽取投标截止时间.

    Returns:
        (截止时间文本, start偏移, end偏移) — 找不到返回(None, None, None)
    """
    for pattern in DEADLINE_PATTERNS:
        m = pattern.search(content)
        if m:
            # 找到完整匹配, 但需要精确定位日期时间部分
            full_match = m.group(0)
            # 提取日期时间部分
            dt_match = DATETIME_RE.search(full_match)
            if dt_match:
                dt_text = dt_match.group(1)
                # 在原文中找这个日期时间的精确位置
                pos = content.find(dt_text)
                if pos >= 0:
                    return dt_text, pos, pos + len(dt_text)
            # 兜底: 用完整匹配
            start = m.start()
            end = m.end()
            return full_match, start, end
    return None, None, None


def build_deadline_field(content: str) -> dict:
    """构建bid_deadline字段."""
    deadline_text, start, end = extract_deadline(content)
    if deadline_text and start is not None and end is not None:
        # 验证切片
        actual = content[start:end]
        if actual == deadline_text:
            return {
                "field_name": "bid_deadline",
                "gold_status": "present",
                "values": [
                    {
                        "raw_value": deadline_text,
                        "acceptable_evidence_spans": [
                            {"start": start, "end": end, "text": deadline_text}
                        ],
                    }
                ],
            }
        else:
            print(f"  WARN: 切片不匹配 expected={deadline_text[:30]} actual={actual[:30]}")
            return {
                "field_name": "bid_deadline",
                "gold_status": "present",
                "values": [
                    {"raw_value": deadline_text, "acceptable_evidence_spans": []}
                ],
            }
    # 无投标截止时间
    return {
        "field_name": "bid_deadline",
        "gold_status": "not_applicable",
        "values": [],
    }


def main() -> None:
    data = json.loads(GOLD_FILE.read_text(encoding="utf-8"))

    print(f"修复K3标注: 补bid_deadline + 移除project_name")
    print(f"原始记录数: {len(data)}")

    fixed_count = 0
    project_name_removed = 0
    deadline_present = 0
    deadline_not_applicable = 0

    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        # 跳过元数据记录
        if item.get("_is_meta"):
            continue

        doc_id = item.get("document_id", "?")
        file_name = item.get("file", f"{doc_id}.txt")
        notice_type = item.get("notice_type", "other")

        # 加载原文
        raw_path = RAW_DIR / file_name
        if not raw_path.exists():
            print(f"  WARN: 原文不存在 {file_name}, 跳过")
            continue
        text = raw_path.read_text(encoding="utf-8")
        lines = text.split("\n", 4)
        content = lines[4] if len(lines) > 4 else ""

        # 1. 移除project_name (从fields数组)
        original_fields = item.get("fields", [])
        new_fields = []
        project_name_field = None
        for f in original_fields:
            if isinstance(f, dict):
                if f.get("field_name") == "project_name":
                    project_name_field = f
                    project_name_removed += 1
                    continue  # 移除
            new_fields.append(f)

        # 2. 补bid_deadline字段
        deadline_field = build_deadline_field(content)
        new_fields.append(deadline_field)

        if deadline_field["gold_status"] == "present":
            deadline_present += 1
        else:
            deadline_not_applicable += 1

        # 3. 更新fields
        item["fields"] = new_fields

        # 4. 把project_name存到附加字段(不丢数据)
        if project_name_field:
            item.setdefault("extra_fields", [])
            item["extra_fields"].append(project_name_field)

        # 5. 更新annotation_version
        item["annotation_version"] = "k3-w3-01-v1-fixed"
        item["fix_note"] = "补bid_deadline, project_name降为extra_fields"

        fixed_count += 1

    # 更新元数据
    for item in data:
        if isinstance(item, dict) and item.get("_is_meta"):
            item["annotation_version"] = "k3-w3-01-v1-fixed"
            item["fix_note"] = "补bid_deadline, project_name降为extra_fields"
            item["fix_count"] = fixed_count
            break

    # 保存
    GOLD_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n修复完成:")
    print(f"  修复篇数: {fixed_count}")
    print(f"  project_name移除: {project_name_removed}")
    print(f"  bid_deadline present: {deadline_present}")
    print(f"  bid_deadline not_applicable: {deadline_not_applicable}")
    print(f"  annotation_version: k3-w3-01-v1-fixed")
    print(f"  文件: {GOLD_FILE}")


if __name__ == "__main__":
    main()
