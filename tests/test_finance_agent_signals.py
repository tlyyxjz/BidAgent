"""finance_agent 修复回归测试（P0-2：观察信号调用签名）。"""
import pytest

from app.agents import finance_agent as fa_mod


class _FakeTender:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _t(company, amount=100.0, day="2026-07-01"):
    return _FakeTender(
        win_company=company,
        win_amount=amount,
        publish_time=day,
        project_name=f"{company}项目",
        tender_org="某采购单位",
        location="上海",
        source_url="https://example.com/x",
        source_platform="ccgp",
    )


@pytest.mark.asyncio
async def test_finance_agent_empty_state():
    state = await fa_mod.run({})
    assert state["observation_signals"] == {}
    assert state["finance_summary"] == {"reason": "无可用公告数据"}


@pytest.mark.asyncio
async def test_finance_agent_produces_signals():
    """修复前：analyze_observation_signals 签名不匹配 → 信号恒空。"""
    tenders = [_t("甲公司"), _t("甲公司"), _t("乙公司", amount=200.0)]
    state = await fa_mod.run({"quality_tenders": tenders})
    assert "甲公司" in state["observation_signals"]
    assert "乙公司" in state["observation_signals"]
    org = state["observation_signals"]["甲公司"]
    # 六个 MVP 信号全部产出
    assert len(org["signals"]) == 6
    # 报告扁平键（英文）兼容 docx_sections
    flat = state["finance_summary"]["observation_signals"]
    assert "award_activity" in flat
    assert state["finance_summary"]["primary_organization"] == "甲公司"


@pytest.mark.asyncio
async def test_finance_agent_no_win_company():
    tenders = [_FakeTender(win_company=None, project_name="无中标人")]
    state = await fa_mod.run({"processed_tenders": tenders})
    assert state["observation_signals"] == {}


@pytest.mark.asyncio
async def test_finance_agent_dict_tenders():
    """兼容 dict 形态的公告数据。"""
    state = await fa_mod.run({
        "quality_tenders": [{
            "win_company": "丙公司", "win_amount": 50.0,
            "publish_time": "2026-07-02", "project_name": "P",
            "tender_org": "O", "location": "北京",
            "source_url": "", "source_platform": "ggzy",
        }]
    })
    assert "丙公司" in state["observation_signals"]
