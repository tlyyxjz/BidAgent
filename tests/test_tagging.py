"""实时打标器测试。"""
import pytest

from app.tagging import (
    INDUSTRIES,
    NOTICE_TYPES,
    _sanitize_tags,
    tag_many,
    tag_many_concurrent,
    tag_notice,
)


def test_label_enums():
    """标签枚举完整、无遗漏。"""
    assert INDUSTRIES == ["IT", "医疗", "建筑", "教育", "能源", "交通", "其他"]
    assert NOTICE_TYPES == ["招标", "中标", "更正", "其他"]


def test_sanitize_rejects_invalid_enum():
    """P1 修复：LLM 返回枚举外的幻觉标签，归一化到兜底值。"""
    tags = _sanitize_tags({"industry": "金融", "notice_type": "随便什么"})
    assert tags == {"industry": "其他", "region": "未知", "notice_type": "其他"}


def test_sanitize_keeps_valid_values():
    """合法标签原样保留。"""
    tags = _sanitize_tags({"industry": "IT", "region": "北京", "notice_type": "中标"})
    assert tags == {"industry": "IT", "region": "北京", "notice_type": "中标"}


def test_sanitize_non_dict_input():
    """P3/P4 修复：LLM 返回非 dict（如字符串），归一化到兜底。"""
    assert _sanitize_tags('"ok"') == {"industry": "其他", "region": "未知", "notice_type": "其他"}
    assert _sanitize_tags(None) == {"industry": "其他", "region": "未知", "notice_type": "其他"}


def test_sanitize_region_not_string():
    """R7 修复：region 为 dict/list 时，不 str() 成 Python repr，归一为"未知"。"""
    tags = _sanitize_tags({"industry": "IT", "region": {"a": 1}})
    assert tags["region"] == "未知"


async def test_tag_notice_fallback_on_error(monkeypatch):
    """本地+云端都出错时，返回兜底标签，不崩主链路。"""

    async def boom(model, title, content, timeout=120.0):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.tagging._tag_via_gateway", boom)
    tags = await tag_notice("某公告标题")
    assert tags == {"industry": "其他", "region": "未知", "notice_type": "其他"}


async def test_tag_notice_ollama_first_glm_fallback(monkeypatch):
    """本地全兜底 → 降级云端 GLM。"""

    calls = []

    async def fake(model, title, content, timeout=120.0):
        calls.append(model)
        if model == "qwen2.5:7b":
            return {"industry": "其他", "region": "未知", "notice_type": "其他"}
        return {"industry": "IT", "region": "北京", "notice_type": "中标"}

    monkeypatch.setattr("app.tagging._tag_via_gateway", fake)
    tags = await tag_notice("某公告标题")
    assert tags["industry"] == "IT"
    assert calls == ["qwen2.5:7b", "glm-4-flash"]


async def test_tag_notice_ollama_success_no_glm(monkeypatch):
    """本地成功 → 不调云端。"""

    calls = []

    async def fake(model, title, content, timeout=120.0):
        calls.append(model)
        return {"industry": "医疗", "region": "山东", "notice_type": "中标"}

    monkeypatch.setattr("app.tagging._tag_via_gateway", fake)
    tags = await tag_notice("某公告标题")
    assert tags["industry"] == "医疗"
    assert calls == ["qwen2.5:7b"]


async def test_tag_many_adds_fields(monkeypatch):
    """批量打标给每条公告追加三个标签字段。"""

    async def fake(model, title, content, timeout=120.0):
        return {"industry": "IT", "region": "北京", "notice_type": "中标"}

    monkeypatch.setattr("app.tagging._tag_via_gateway", fake)
    notices = [{"title": "服务器采购"}, {"project_name": "充电桩建设"}]
    result = await tag_many(notices)
    assert len(result) == 2
    assert result[0]["industry"] == "IT"
    assert result[0]["region"] == "北京"
    assert result[0]["notice_type"] == "中标"
    # 原字段保留
    assert result[1]["project_name"] == "充电桩建设"


async def test_tag_many_skips_non_dict(monkeypatch):
    """P2 修复：批量里混入非 dict（脏数据），跳过不崩整批。"""

    async def fake(model, title, content, timeout=120.0):
        return {"industry": "IT", "region": "北京", "notice_type": "中标"}

    monkeypatch.setattr("app.tagging._tag_via_gateway", fake)
    notices = [{"title": "正常公告"}, "脏字符串"]
    result = await tag_many(notices)
    assert len(result) == 1
    assert result[0]["title"] == "正常公告"


async def test_tag_many_concurrent(monkeypatch):
    """并发批量打标：结果完整、保序、跳过非 dict。"""

    async def fake(model, title, content, timeout=120.0):
        return {"industry": "IT", "region": "北京", "notice_type": "中标"}

    monkeypatch.setattr("app.tagging._tag_via_gateway", fake)
    notices = [{"title": "a"}, {"title": "b"}, {"title": "c"}]
    result = await tag_many_concurrent(notices, concurrency=2)
    assert [r["title"] for r in result] == ["a", "b", "c"]
    assert all(r["industry"] == "IT" for r in result)
