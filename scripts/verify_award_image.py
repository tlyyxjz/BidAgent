"""中标通知书图片核验器（OCR 上传版，复赛交互爆点）。

输入一张中标通知书图片（截图/扫描件），EasyOCR 识别文字 →
用确定性解析器抽取 编号/金额/中标人/采购人 → 复用 verify_award 核验。

原则不变：OCR 只生成候选，核验仍由确定性程序完成；识别不出就明说，
绝不编造。OCR 定位为图像像素坐标（与原文字符位置不同，报告里如实区分）。

用法：
    python scripts/verify_award_image.py --image 通知书.png [--db ...] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.processors.ccgp_field_parser import (  # noqa: E402
    parse_bid_number,
    parse_tender_org,
    parse_win_amount,
)
from verify_award import (  # noqa: E402
    _extract_winners_from_text,
    verify_award,
)

DEFAULT_DB = REPO / "data" / "bidagent.db"


def sort_ocr_lines(detail: list) -> list:
    """EasyOCR detail 结果按阅读顺序排序（行分组：y 接近的为一行，行内按 x）。

    全程转 float，避免 numpy 标量参与布尔判断的歧义。
    """
    items = [d for d in detail if d and len(d) >= 3 and d[1]]
    if not items:
        return []
    items.sort(key=lambda d: (float(d[0][0][1]), float(d[0][0][0])))
    lines: list[tuple[float, list]] = []
    for d in items:
        y = float(d[0][0][1])
        if lines and abs(y - lines[-1][0]) < 15:
            lines[-1][1].append(d)
        else:
            lines.append((y, [d]))
    out = []
    for _y, group in lines:
        group.sort(key=lambda d: float(d[0][0][0]))
        out.append(group)
    return out


def ocr_to_text(detail: list) -> tuple[str, list]:
    """detail -> (全文, 按行的 [(行文本, [bbox,...])])。"""
    lines = sort_ocr_lines(detail)
    line_texts = [" ".join(d[1] for d in line) for line in lines]
    return "\n".join(line_texts), list(zip(line_texts, lines))


def extract_fields(ocr_text: str) -> dict:
    """从 OCR 文本抽取通知书字段（全确定性正则，无 LLM）。"""
    winners = _extract_winners_from_text(ocr_text)
    amount_raw = None
    m = re.search(
        r"中标(?:[（(]成交[）)])?\s*(?:金额\s*)?[:：\s]*"
        r"([\d.,]+\s*(?:[（(]?\s*(?:万元|亿元|元|万|亿)\s*[）)]?)?)",
        ocr_text,
    )
    if m:
        amount_raw = re.sub(r"\s+", "", m.group(1))
    return {
        "bid_number": parse_bid_number(ocr_text),
        "win_amount_raw": amount_raw,
        "win_amount": parse_win_amount(ocr_text),
        "winner": winners[0] if winners else None,
        "purchaser": parse_tender_org(ocr_text),
    }


def verify_image(image_path: str, db_path=None) -> dict:
    """图片核验全流程。返回 {fields, ocr_text, ocr_bboxes, result, warnings}。"""
    import easyocr

    warnings = []
    reader = easyocr.Reader(["ch_sim", "en"], gpu=True, verbose=False)
    try:
        detail = reader.readtext(image_path, detail=1)
    except Exception as exc:  # noqa: BLE001 —— 文件缺失/损坏/OCR 引擎异常
        return {
            "fields": {}, "ocr_text": "", "ocr_bboxes": [],
            "result": {"verdict": "suspicious", "reason": f"图片读取或识别失败：{type(exc).__name__}"},
            "warnings": [f"OCR 异常：{type(exc).__name__}"],
        }
    if not detail:
        return {
            "fields": {}, "ocr_text": "", "ocr_bboxes": [],
            "result": {"verdict": "suspicious", "reason": "图片未识别出任何文字"},
            "warnings": ["OCR 无结果"],
        }
    ocr_text, line_data = ocr_to_text(detail)
    ocr_bboxes = [
        {"text": t, "bbox": [list(map(int, p)) for p in d[0]]}
        for t, lines in line_data for d in lines
    ]
    fields = extract_fields(ocr_text)
    if not fields["bid_number"]:
        warnings.append("未识别出项目编号，无法核验")
    if not fields["winner"]:
        warnings.append("未识别出中标人")
    if not fields["win_amount_raw"]:
        warnings.append("未识别出中标金额")

    result = verify_award(
        fields["bid_number"] or "",
        amount=fields["win_amount_raw"] or "",
        winner=fields["winner"] or "",
        purchaser=fields["purchaser"] or "",
        db_path=db_path,
    )
    return {"fields": fields, "ocr_text": ocr_text, "ocr_bboxes": ocr_bboxes,
            "result": result, "warnings": warnings}


def main() -> None:
    parser = argparse.ArgumentParser(description="中标通知书图片核验")
    parser.add_argument("--image", required=True, help="通知书图片路径（png/jpg）")
    parser.add_argument("--db", default=None, help="DB 路径（默认 data/bidagent.db，可用环境变量 BIDAGENT_DB 覆盖）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    if not Path(args.image).exists():
        print(f"图片不存在: {args.image}")
        sys.exit(1)
    db = args.db or os.environ.get("BIDAGENT_DB") or DEFAULT_DB

    out = verify_image(args.image, db)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return

    mark = {"verified": "真", "suspicious": "存疑", "fake": "伪造"}[out["result"]["verdict"]]
    print("=" * 60)
    print(f"OCR 识别字段：")
    for k in ("bid_number", "win_amount_raw", "winner", "purchaser"):
        print(f"  {k}: {out['fields'].get(k)}")
    if out["warnings"]:
        print(f"识别警告：{'；'.join(out['warnings'])}")
    print(f"核验结论：{mark}（{out['result']['verdict']}）")
    print(f"理由：{out['result']['reason']}")
    if out["result"].get("evidence", {}).get("project_name"):
        print(f"匹配公告：{out['result']['evidence']['project_name']}")
    print("（注意：以上字段来自 OCR，识别错误可能导致结论偏差；结论仅供参考，正式核验请核对原图）")
    print("=" * 60)


if __name__ == "__main__":
    main()
