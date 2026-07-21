"""废标风险预警引擎测试。"""
from __future__ import annotations

import pytest

from app.processors.risk_engine import (
    RiskReport,
    _analyze,
    _match_rule,
    analyze_risk,
    analyze_risk_engine,
)


def test_empty_text():
    """空文本应返回零分和默认摘要。"""
    report = _analyze("", "", None)

    assert report.risk_score == 0
    assert report.summary == "未检测到明显风险。"
    assert report.risk_items == []


def test_exclusive_must_have():
    """'必须具备' 应命中排他性规则，得 20 分。"""
    # 注意：输入不含"资质"等其他规则关键词，避免叠加计分
    report = _analyze("测试项目", "投标人必须具备相关条件", None)

    assert report.risk_score == 20
    assert any(item["risk_type"] == "exclusive" for item in report.risk_items)


def test_exclusive_original_factory():
    """'原厂' 应命中排他性规则，得 20 分。"""
    report = _analyze("测试项目", "需提供原厂授权书", None)

    assert report.risk_score == 20
    assert any(item["clause"] == "原厂" for item in report.risk_items)


def test_payment_risk():
    """'履约保证金' 应命中付款风险，得 12 分。"""
    report = _analyze("测试项目", "履约保证金比例10%", None)

    assert report.risk_score == 12
    assert any(item["risk_type"] == "payment" for item in report.risk_items)


def test_deadline_risk():
    """'交货期' 应命中交货期风险，得 12 分。"""
    report = _analyze("测试项目", "交货期30天内", None)

    assert report.risk_score == 12
    assert any(item["risk_type"] == "deadline" for item in report.risk_items)


def test_qualification_iso():
    """'ISO' 应命中资质门槛，得 8 分。"""
    report = _analyze("测试项目", "需提供ISO9001认证", None)

    assert report.risk_score == 8
    assert any(item["clause"] == "ISO" for item in report.risk_items)


def test_qualification_cmmi():
    """'CMMI' 应命中资质门槛，得 8 分。"""
    report = _analyze("测试项目", "需CMMI3级认证", None)

    assert report.risk_score == 8
    assert any(item["clause"] == "CMMI" for item in report.risk_items)


def test_combined_rules_all_mode():
    """联合体+分包同时出现才命中冲突规则（AND），得 15 分。"""
    report = _analyze("测试项目", "允许联合体投标，禁止分包", None)

    assert report.risk_score == 15
    assert any(item["clause"] == "联合体" for item in report.risk_items)


def test_all_mode_not_triggered_when_single_keyword():
    """AND 规则只出现一个关键词不应命中。"""
    report = _analyze("测试项目", "本项目允许联合体投标", None)

    assert not any(item["clause"] == "联合体" for item in report.risk_items)


def test_or_rule_payment_period():
    """付款周期/付款期限 OR 规则，任一出现即命中。"""
    report1 = _analyze("项目", "付款周期90天", None)
    report2 = _analyze("项目", "付款期限90天", None)

    assert report1.risk_score == 15
    assert report2.risk_score == 15


def test_or_rule_deadline():
    """交货期/交付期/供货期 OR 规则，三个任一出现即命中。"""
    for keyword in ["交货期", "交付期", "供货期"]:
        report = _analyze("项目", f"{keyword}30天", None)
        assert report.risk_score == 12, f"{keyword} 应命中"


def test_star_rule():
    """星号规则：文本含 ★ 或 * 命中，得 20 分。"""
    report = _analyze("测试项目", "技术参数★项1，★项2", None)

    assert report.risk_score == 20
    assert any(item["clause"] == "★" for item in report.risk_items)


def test_star_rule_asterisk_not_triggered():
    """m-1 修复：星号规则不应被 * 触发（避免 HTML/Markdown 误报）。"""
    report = _analyze("测试项目", "参数*项1，列表*项2", None)

    assert not any(item["clause"] == "★" for item in report.risk_items)
    assert report.risk_score == 0


def test_risk_level_high():
    """score >= 60 为高风险。"""
    report = _analyze("项目", "必须具备 原厂 指定品牌 唯一授权 独有", None)

    assert report.risk_score >= 60
    assert report._level() == "高风险"


def test_risk_level_medium():
    """30 <= score < 60 为中风险。"""
    report = _analyze("项目", "必须具备 履约保证金", None)

    assert 30 <= report.risk_score < 60
    assert report._level() == "中风险"


def test_risk_level_low():
    """score < 30 为低风险。"""
    report = _analyze("项目", "安全生产", None)

    assert report.risk_score < 30
    assert report._level() == "低风险"


def test_score_capped_at_100():
    """多规则命中时分数封顶 100。"""
    text = "必须具备 必须拥有 独有 唯一授权 原厂 指定品牌 ISO CMMI"
    report = _analyze("项目", text, None)

    assert report.risk_score == 100


def test_qualification_gaps_with_iso():
    """有 ISO 但无资质清单时应有资质缺口。"""
    report = _analyze("项目", "需ISO9001认证", "需ISO9001认证")

    assert len(report.qualification_gaps) > 0
    assert any("ISO" in gap for gap in report.qualification_gaps)


def test_qualification_gaps_empty_when_complete():
    """资质+许可证齐全时无缺口。"""
    report = _analyze("项目", "需资质或许可证", "需资质或许可证")

    # "资质" 和 "许可证" 都在 full_text 中，不触发"未明确列出"缺口
    assert not any("未明确" in gap for gap in report.qualification_gaps)


def test_dedup_same_rule_not_double_counted():
    """同一规则不重复计分。"""
    report = _analyze("项目", "必须具备 必须具备 必须具备", None)

    # 只计一次 20 分
    assert report.risk_score == 20
    assert len([i for i in report.risk_items if i["clause"] == "必须具备"]) == 1


def test_summary_contains_risk_keywords():
    """摘要应包含风险关键词。"""
    report = _analyze("项目", "必须具备 原厂", None)

    assert "高风险" in report.summary or "排他" in report.summary


def test_match_rule_any_mode():
    """_match_rule any 模式：任一关键词命中。"""
    assert _match_rule(["A", "B"], "any", "包含A") is True
    assert _match_rule(["A", "B"], "any", "包含B") is True
    assert _match_rule(["A", "B"], "any", "无关键词") is False


def test_match_rule_all_mode():
    """_match_rule all 模式：全部关键词命中。"""
    assert _match_rule(["A", "B"], "all", "包含A和B") is True
    assert _match_rule(["A", "B"], "all", "只有A") is False


def test_match_rule_star_mode():
    """_match_rule star 模式：任一星号字符命中。"""
    assert _match_rule(["★", "*"], "star", "参数★") is True
    assert _match_rule(["★", "*"], "star", "参数*") is True
    assert _match_rule(["★", "*"], "star", "无星号") is False


def test_project_name_included_in_analysis():
    """项目名称应参与规则匹配。"""
    # 项目名含"必须具备"，content 不含其他规则关键词
    report = _analyze("必须具备相关条件的项目", "普通内容", None)

    assert report.risk_score == 20


def test_qualification_field_participates():
    """资质字段应参与规则匹配。"""
    report = _analyze("项目", "普通内容", "需ISO9001认证")

    assert report.risk_score == 8


def test_to_dict_format():
    """RiskReport.to_dict 应返回完整字典。"""
    report = _analyze("项目", "必须具备", None)
    result = report.to_dict()

    assert result["version"] == "v2"
    # M-3 修复：engine 改为 rule_based_v1（纯规则引擎）
    assert result["engine"] == "rule_based_v1"
    assert "risk_score" in result
    assert "risk_level" in result
    assert "risk_items" in result
    assert "qualification_gaps" in result
    assert "created_at" in result
    # M-1 修复：新增完整统计字段
    assert "total_risk_items" in result
    assert "total_risk_score" in result


@pytest.mark.asyncio
async def test_analyze_risk_async_public_api():
    """异步接口应返回标准字典格式。"""
    result = await analyze_risk(
        "测试项目",
        "必须具备XXX资质",
        "需ISO9001认证",
        tender_id=123,
    )

    assert result["version"] == "v2"
    assert result["tender_id"] == 123
    assert result["project_name"] == "测试项目"
    # 命中：必须具备(20) + 资质(8) + ISO(8) = 36
    assert result["risk_score"] == 36
    assert result["risk_level"] == "中风险"
    # M-3 修复：engine 改为 rule_based_v1
    assert result["engine"] == "rule_based_v1"
    assert len(result["risk_items"]) == 3
    assert len(result["qualification_gaps"]) > 0
    # M-1 修复：完整统计字段
    assert result["total_risk_items"] == 3
    assert result["total_risk_score"] == 36


@pytest.mark.asyncio
async def test_analyze_risk_engine_alias():
    """analyze_risk_engine 应为 analyze_risk 的别名。"""
    assert analyze_risk_engine is analyze_risk

    result = await analyze_risk_engine("项目", "必须具备", None)
    assert result["risk_score"] == 20


@pytest.mark.asyncio
async def test_analyze_risk_empty_input():
    """空输入应返回零分。"""
    result = await analyze_risk("", "", None)

    assert result["risk_score"] == 0
    assert result["summary"] == "未检测到明显风险。"


def test_risk_report_dataclass_defaults():
    """RiskReport 默认值应正确。"""
    report = RiskReport()

    assert report.tender_id is None
    assert report.project_name == ""
    assert report.risk_score == 0.0
    assert report.risk_items == []
    assert report.qualification_gaps == []
    # M-1 新增字段默认值
    assert report.total_risk_items == 0
    assert report.total_risk_score == 0.0


# ============================================================
# m-5 反面用例测试（否定语境 / 星号误报 / 截断一致性 / 性能）
# ============================================================


def test_m2_negation_not_required_original_factory():
    """M-2 修复：'不要求原厂授权' 不应命中原厂规则。"""
    report = _analyze("项目", "本项目不要求原厂授权", None)

    assert not any(item["clause"] == "原厂" for item in report.risk_items)
    assert report.risk_score == 0


def test_m2_negation_no_iso_required():
    """M-2 修复：'无需ISO认证' 不应命中ISO规则。"""
    report = _analyze("项目", "本项目无需ISO认证", None)

    assert not any(item["clause"] == "ISO" for item in report.risk_items)
    assert report.risk_score == 0


def test_m2_negation_no_qualification_required():
    """M-2 修复：'不要求资质' 不应命中资质规则。"""
    report = _analyze("项目", "本项目不要求资质或许可证", None)

    assert not any(item["clause"] == "资质" for item in report.risk_items)
    assert report.risk_score == 0


def test_m2_negation_not_accept_consortium():
    """M-2 修复：'不接受联合体投标' 不应命中联合体规则。

    注：AND 规则下，'不接受联合体' + '禁止分包' 不应触发冲突规则
    """
    report = _analyze("项目", "不接受联合体投标，禁止分包", None)

    # 联合体被否定，但'禁止分包'是真实风险（不过'分包'单独不被规则覆盖，需联合体同时存在）
    # AND 规则要求两者都非否定才命中
    assert not any(item["clause"] == "联合体" for item in report.risk_items)


def test_m2_negation_partial_any_rule_still_matches():
    """M-2 修复：ANY 规则中部分关键词被否定，仍有非否定关键词命中则照常命中。

    '付款周期' 不被否定，'付款期限' 被否定 → 仍应命中付款周期规则
    """
    report = _analyze("项目", "付款周期90天，无需付款期限限制", None)

    assert any(item["clause"] == "付款周期" for item in report.risk_items)
    assert report.risk_score == 15


def test_m2_negation_no_cash_required():
    """M-2 修复：'不接受现金保证金' 不应命中现金规则。"""
    report = _analyze("项目", "不接受现金保证金", None)

    assert not any(item["clause"] == "现金" for item in report.risk_items)


def test_m1_truncation_consistency():
    """M-1 修复：超过10条规则时，total_risk_items 应反映完整数量。"""
    # 命中超过 10 条规则
    text = (
        "必须具备 必须拥有 独有 唯一授权 原厂 指定品牌 "
        "付款周期 履约保证金 交货期 ISO CMMI 安全生产 "
        "本地 ★ 否决"
    )
    report = _analyze("项目", text, None)
    result = report.to_dict()

    # 展示项最多 10 条
    assert len(result["risk_items"]) <= 10
    # 但完整统计不截断
    assert result["total_risk_items"] > 10
    # 分数一致（不截断）
    assert result["risk_score"] == result["total_risk_score"]


def test_m1_truncation_total_fields_populated():
    """M-1 修复：total_risk_items 和 total_risk_score 应正确填充。"""
    report = _analyze("项目", "必须具备 ISO 履约保证金", None)
    result = report.to_dict()

    # 命中 3 条规则
    assert result["total_risk_items"] == 3
    # 20 + 8 + 12 = 40
    assert result["total_risk_score"] == 40
    assert result["risk_score"] == 40


def test_m3_engine_field_is_rule_based():
    """M-3 修复：engine 字段应为 rule_based_v1。"""
    report = _analyze("项目", "必须具备", None)
    result = report.to_dict()

    assert result["engine"] == "rule_based_v1"
    # 不应再出现 deepseek 名字
    assert "deepseek" not in result["engine"]


def test_long_text_performance():
    """m-5 反面用例：超长文本性能测试。"""
    import time as _time

    # 构造 5000 字文本，含多个规则关键词
    base = "本项目必须具备相关资质，需ISO9001认证，履约保证金10%，交货期30天。"
    long_text = base * 50  # 约 5000 字

    start = _time.time()
    report = _analyze("项目", long_text, None)
    elapsed = _time.time() - start

    # 应在 1 秒内完成
    assert elapsed < 1.0, f"超长文本分析耗时 {elapsed:.3f}s"
    # 应命中规则
    assert report.risk_score > 0
