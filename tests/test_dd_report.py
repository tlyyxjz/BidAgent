"""批量尽调报告器测试：纯函数 + DB 集成。"""
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import dd_report as dr  # noqa: E402

DB = os.environ.get("BIDAGENT_DB") or str(REPO / "data" / "bidagent.db")
HAS_DB = Path(DB).exists()
requires_db = pytest.mark.skipif(not HAS_DB, reason="bidagent.db 不存在，跳过集成测试")


# ---------------------------------------------------------------- 纯函数

def test_risk_flags_concentration_and_single_year():
    stats = {
        "total_wins": 3,
        "top1_share_pct": 100.0,
        "customers": [{"purchaser": "某采购人"}],
        "yearly_trend": {"2026": 3},
    }
    flags = dr.risk_flags(stats)
    assert any(f["signal"] == "客户集中度高" for f in flags)
    assert any(f["signal"] == "数据周期短" for f in flags)


def test_risk_flags_empty():
    assert dr.risk_flags({"total_wins": 0}) == []


def test_render_html_contains_sections_and_numbers():
    data = {
        "company": "测试公司", "generated_at": "2026-08-17 12:00:00",
        "stats": {
            "total_wins": 2, "total_amount": 600000.0, "avg_amount": 300000.0,
            "max_amount": 400000.0, "min_amount": 200000.0,
            "yearly_trend": {"2026": 2}, "locations": {"湖南": 2},
            "top1_share_pct": 66.7,
            "top_customers": [{"purchaser": "A单位", "count": 2, "amount": 400000.0}],
        },
        "wins": [{
            "project_name": "某项目中标公告", "bid_number": "X-1", "amount": 400000.0,
            "purchaser": "A单位", "publish_time": "2026-08-05 10:00:00",
            "source_url": "http://example.gov.cn/x", "evidence": "供应商名称：测试公司",
        }],
        "risks": [{"signal": "客户集中度高", "fact": "第一大采购人占 66.7%"}],
        "empty": False,
    }
    h = dr.render_html(data)
    assert "一、中标画像摘要" in h
    assert "二、客观信号" in h
    assert "三、逐条中标核验" in h
    assert "600,000" in h
    assert "供应商名称：测试公司" in h
    assert "不构成投资建议" in h


def test_render_html_empty_company_honest():
    data = {
        "company": "不存在公司", "generated_at": "2026-08-17",
        "stats": {"total_wins": 0, "top1_share_pct": None, "top_customers": []},
        "wins": [], "risks": [], "empty": True,
    }
    h = dr.render_html(data)
    assert "未找到该企业的中标记录" in h
    assert "如实报告" in h


def test_render_html_escapes_company_name():
    data = {
        "company": "<script>alert(1)</script>", "generated_at": "2026-08-17",
        "stats": {"total_wins": 0, "top1_share_pct": None, "top_customers": []},
        "wins": [], "risks": [], "empty": True,
    }
    h = dr.render_html(data)
    assert "<script>alert(1)</script>" not in h
    assert "&lt;script&gt;" in h


# ---------------------------------------------------------------- DB 集成

@requires_db
def test_build_report_multi_win_company():
    data = dr.build_report_data("湖南创益蔚来进出口有限公司", DB)
    assert data["stats"]["total_wins"] >= 3
    assert data["stats"]["top1_share_pct"] == 100.0
    assert any(r["signal"] == "客户集中度高" for r in data["risks"])
    for w in data["wins"]:
        assert w["evidence"] and "供应商名称" in w["evidence"]
        assert w["source_url"].startswith("http")


@requires_db
def test_build_report_unknown_company_empty():
    data = dr.build_report_data("不存在的某某公司XYZ", DB)
    assert data["empty"] is True
    assert data["wins"] == []


@requires_db
def test_render_html_contains_charts():
    """图表是**可选增强**：装了 matplotlib 就必须真出图。

    dd_report.render_html 里图表生成被 try/except 包着（失败不阻塞报告），
    所以 matplotlib 属于可选依赖 —— 缺它不该让本测试变成 error，故用
    importorskip。CI 显式装 matplotlib（requirements-report.txt）后本用例会
    **真跑**而非跳过。
    """
    pytest.importorskip("matplotlib", reason="matplotlib 为可选图表依赖")
    data = dr.build_report_data("湖南创益蔚来进出口有限公司", DB)
    h = dr.render_html(data)
    assert "data:image/png;base64," in h
    assert "<img" in h


@requires_db
def test_render_html_survives_chart_failure(monkeypatch):
    """降级契约：图表后端挂掉时，报告主体仍必须完整。

    与上一条互补 —— 上一条只在装了 matplotlib 时验「有图」，这一条不依赖任何
    可选依赖，任何环境都跑，专门守住 render_html 的 try/except 降级行为。
    """
    data = dr.build_report_data("湖南创益蔚来进出口有限公司", DB)

    def _boom(_data):
        raise RuntimeError("chart backend unavailable")

    monkeypatch.setattr(dr, "chart_images", _boom)
    h = dr.render_html(data)
    assert "尽调数据包" in h                    # 报告主体仍在
    assert "data:image/png;base64," not in h   # 图确实缺席


@requires_db
def test_render_pdf_generates_file(tmp_path):
    """PDF 导出（reportlab 为可选依赖，CI 装了故真跑）；输出走 tmp_path 而非写死本机路径。"""
    pytest.importorskip("reportlab", reason="reportlab 为可选 PDF 导出依赖")
    from dd_pdf import render_pdf

    data = dr.build_report_data("湖南创益蔚来进出口有限公司", DB)
    out = render_pdf(data, tmp_path / "_dd_test.pdf")
    try:
        head = out.read_bytes()[:5]
        assert head == b"%PDF-"
        assert out.stat().st_size > 1000
    finally:
        out.unlink(missing_ok=True)
