"""OCR 图片核验器测试：行排序/字段抽取单测 + 真 OCR 端到端（合成通知书）。"""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO))

import verify_award_image as vai  # noqa: E402

DB = os.environ.get("BIDAGENT_DB") or str(REPO / "data" / "bidagent.db")
HAS_DB = Path(DB).exists()
HAS_EASYOCR = importlib.util.find_spec("easyocr") is not None
HAS_FONT = Path(r"C:\Windows\Fonts\msyh.ttc").exists()
requires_ocr = pytest.mark.skipif(
    not (HAS_EASYOCR and HAS_DB and HAS_FONT),
    reason="easyocr/DB/字体缺失，跳过真 OCR 端到端",
)


# ---------------------------------------------------------------- 单测

def _mk_item(x, y, w, h, text):
    return [[[x, y], [x + w, y], [x + w, y + h], [x, y + h]], text, 0.99]


def test_sort_ocr_lines_groups_and_orders():
    detail = [
        _mk_item(0, 100, 200, 20, "第二行"),
        _mk_item(0, 0, 200, 20, "第一行"),
        _mk_item(150, 105, 100, 20, "同行右边"),
    ]
    lines = vai.sort_ocr_lines(detail)
    assert len(lines) == 2
    assert [d[1] for d in lines[0]] == ["第一行"]
    assert [d[1] for d in lines[1]] == ["第二行", "同行右边"]


def test_sort_ocr_lines_empty():
    assert vai.sort_ocr_lines([]) == []
    assert vai.sort_ocr_lines([["", None, 0.5]]) == []


def test_extract_fields_halfwidth_ocr_text():
    text = (
        "项目编号: GHHX2026000062\n"
        "供应商名称:  青岛盛信合创电子技术有限公司\n"
        "中标(成交)  金额:  22.5320000 (万元)\n"
        "四、采购人信息\n"
        "名称:  中国电子口岸数据中心青岛分中心\n"
    )
    f = vai.extract_fields(text)
    assert f["bid_number"] == "GHHX2026000062"
    assert f["win_amount_raw"] == "22.5320000(万元)"
    assert f["winner"] == "青岛盛信合创电子技术有限公司"
    assert f["purchaser"] == "中国电子口岸数据中心青岛分中心"


def test_extract_fields_fullwidth_still_works():
    text = "中标（成交）金额：63.8600000（万元）"
    f = vai.extract_fields(text)
    assert f["win_amount_raw"] == "63.8600000（万元）"


def test_extract_fields_missing_amount_honest():
    f = vai.extract_fields("只有标题没有内容")
    assert f["bid_number"] is None
    assert f["win_amount_raw"] is None


# ---------------------------------------------------------------- 真 OCR 端到端

def _make_notice_image(path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    lines = [
        "中标通知书",
        "一、项目编号：GHHX2026000062",
        "三、中标（成交）信息",
        "供应商名称：青岛盛信合创电子技术有限公司",
        "中标（成交）金额：22.5320000（万元）",
        "四、采购人信息",
        "名 称：中国电子口岸数据中心青岛分中心",
    ]
    img = Image.new("RGB", (1600, 800), "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 44)
    y = 60
    for ln in lines:
        d.text((80, y), ln, font=font, fill="black")
        y += 85
    img.save(path)


@requires_ocr
def test_verify_image_fake_notice_verified():
    """Pillow 画假通知书 → 真 EasyOCR → 全字段抽取 → 核验为真。"""
    img = Path(r"D:\Lenovo\Documents\fake_notice_test.png")
    _make_notice_image(img)
    try:
        out = vai.verify_image(str(img), DB)
        assert out["result"]["verdict"] == "verified"
        assert out["fields"]["bid_number"] == "GHHX2026000062"
        assert out["fields"]["winner"] == "青岛盛信合创电子技术有限公司"
        assert out["fields"]["purchaser"] == "中国电子口岸数据中心青岛分中心"
        assert out["fields"]["win_amount"] is not None
        assert out["warnings"] == []
    finally:
        img.unlink(missing_ok=True)


@requires_ocr
def test_verify_image_nonexistent_file_returns_suspicious():
    out = vai.verify_image(r"D:\Lenovo\Documents\not_exist_abc.png", DB)
    assert out["result"]["verdict"] == "suspicious"
