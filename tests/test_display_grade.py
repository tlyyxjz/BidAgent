"""W3-07 display_grade + 选择性输出 单元测试.

覆盖：
  - compute_display_grade 组合规则 (≥6 个)
  - filter_by_strategy 四个策略 (≥4 个)
  - 迁移脚本幂等与空数据库
  合计 ≥8 个，实际编写更多以满足覆盖率要求.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.api.output_strategies import filter_by_strategy
from app.llm.extraction_schemas import ExtractionResult, FieldExtraction
from app.processors.display_grade import (
    GRADE_HIGH,
    GRADE_LOW,
    GRADE_REVIEW,
    SupportLevel,
    compute_display_grade,
)


# ========== compute_display_grade ==========

def test_high__strong_direct_official_original():
    """HIGH: DIRECT + official_original"""
    assert compute_display_grade(
        support_level=SupportLevel.DIRECT,
        source_role="official_original",
        cross_verified=True,
    ) == GRADE_HIGH


def test_high__strong_equivalent_official_original_no_cross():
    """HIGH: EQUIVALENT + official_original，无需交叉验证"""
    assert compute_display_grade(
        support_level="equivalent",
        source_role="official_original",
        cross_verified=False,
    ) == GRADE_HIGH


def test_review__medium_inferred():
    """REVIEW: support_level=MEDIUM (INFERRED)"""
    assert compute_display_grade(
        support_level=SupportLevel.INFERRED,
        source_role="official_original",
    ) == GRADE_REVIEW


def test_review__strong_but_official_repost():
    """REVIEW: STRONG + source_role=official_repost"""
    assert compute_display_grade(
        support_level="direct",
        source_role="official_repost",
        cross_verified=True,
    ) == GRADE_REVIEW


def test_review__strong_commercial_repost():
    """REVIEW: STRONG + source_role=commercial_repost"""
    assert compute_display_grade(
        support_level=SupportLevel.EQUIVALENT,
        source_role="commercial_repost",
    ) == GRADE_REVIEW


def test_low__weak_unsupported():
    """LOW: WEAK (unsupported)"""
    assert compute_display_grade(
        support_level=SupportLevel.UNSUPPORTED,
        source_role="official_original",
    ) == GRADE_LOW


def test_low__weak_contradicted():
    """LOW: WEAK (contradicted)"""
    assert compute_display_grade(
        support_level="contradicted",
        source_role="official_original",
    ) == GRADE_LOW


def test_low__field_status_absent():
    """LOW: field_status=absent，即使其它条件全部符合"""
    assert compute_display_grade(
        support_level="direct",
        source_role="official_original",
        cross_verified=True,
        field_status="absent",
    ) == GRADE_LOW


def test_low__field_status_ambiguous():
    """LOW: field_status=ambiguous"""
    assert compute_display_grade(
        support_level="direct",
        source_role="official_original",
        field_status="ambiguous",
    ) == GRADE_LOW


def test_low__source_unknown():
    """LOW: source_role=unknown"""
    assert compute_display_grade(
        support_level="direct",
        source_role="unknown",
    ) == GRADE_LOW


def test_case_insensitive():
    """大小写不敏感（support_level 与 source_role 都转小写比较）"""
    assert compute_display_grade("DIRECT", "official_original") == GRADE_HIGH
    assert compute_display_grade("Direct", "OFFICIAL_ORIGINAL") == GRADE_HIGH
    assert compute_display_grade("Direct", "UNKNOWN") == GRADE_LOW


# ========== filter_by_strategy ==========

def _sample_fields():
    return [
        # high
        {"field_name": "f1", "display_grade": GRADE_HIGH, "support_level": "direct"},
        # review + strong (default 保留)
        {"field_name": "f2", "display_grade": GRADE_REVIEW, "support_level": "equivalent"},
        # review + medium (default 过滤)
        {"field_name": "f3", "display_grade": GRADE_REVIEW, "support_level": "inferred"},
        # low
        {"field_name": "f4", "display_grade": GRADE_LOW, "support_level": "unsupported"},
    ]


def test_strategy_strict_only_high():
    fields = _sample_fields()
    res = filter_by_strategy(fields, "strict")
    assert [f["field_name"] for f in res] == ["f1"]


def test_strategy_default_high_and_strong_review():
    fields = _sample_fields()
    res = filter_by_strategy(fields, "default")
    names = [f["field_name"] for f in res]
    # f1(high) + f2(review & strong=direct/equivalent)；f3(inferred),f4(low) 过滤
    assert names == ["f1", "f2"]


def test_strategy_loose_high_and_all_review():
    fields = _sample_fields()
    res = filter_by_strategy(fields, "loose")
    names = [f["field_name"] for f in res]
    assert names == ["f1", "f2", "f3"]  # high + all review (no low)


def test_strategy_audit_includes_low():
    fields = _sample_fields()
    res = filter_by_strategy(fields, "audit")
    names = [f["field_name"] for f in res]
    assert names == ["f1", "f2", "f3", "f4"]  # 全部


def test_strategy_invalid_raises():
    with pytest.raises(ValueError):
        filter_by_strategy([], "foo")


def test_filter_accepts_orm_objects():
    """兼容传入 FieldExtraction（带属性的对象）"""
    f1 = FieldExtraction(field_name="f1", field_status="present",
                         support_level="direct",
                         display_grade=GRADE_HIGH)
    f2 = FieldExtraction(field_name="f2", field_status="absent",
                         support_level="direct",
                         display_grade=GRADE_LOW)
    res = filter_by_strategy([f1, f2], "strict")
    assert [x.field_name for x in res] == ["f1"]


# ========== 接入 extractor 流水线：_populate_display_grades ==========

def test_populate_display_grades_in_extraction_result():
    from app.llm.extractor import _populate_display_grades
    r = ExtractionResult(
        model_id="test",
        prompt_hash="h",
        fields=[
            FieldExtraction(field_name="a", field_status="present",
                            support_level="direct"),  # high
            FieldExtraction(field_name="b", field_status="present",
                            support_level="inferred"),  # review
            FieldExtraction(field_name="c", field_status="absent",
                            support_level="direct"),  # low
        ],
    )
    _populate_display_grades(r, source_role="official_original")
    grades = [f.display_grade for f in r.fields]
    assert grades == [GRADE_HIGH, GRADE_REVIEW, GRADE_LOW]


# ========== 迁移脚本：幂等 + 空数据库 ==========

async def _run_mig_test():
    from scripts.migrate_add_display_grade import migrate_extracted_fields
    from sqlalchemy.ext.asyncio import create_async_engine
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    # drop=True: 先强制删除列 -> 再添加；验证幂等（drop 导致首遍 migrated，二遍 skipped）
    r1 = await migrate_extracted_fields(engine, drop=False)
    r2 = await migrate_extracted_fields(engine, drop=False)
    await engine.dispose()
    return r1, r2


def test_migration_idempotent():
    """迁移幂等：两次运行都无错误，第二次全部 skipped."""
    r1, r2 = asyncio.run(_run_mig_test())
    assert not r1["errors"], f"first run errors: {r1['errors']}"
    assert not r2["errors"], f"second run errors: {r2['errors']}"
    assert r2["idempotent_run2"] is True
    # 第二次运行：两个新列全部 skipped
    assert set(r2["skipped"]) == {"display_grade", "cross_verified"}


def test_migration_on_empty_db():
    """空数据库（先不 create_all）也能正常完成."""
    async def _t():
        from scripts.migrate_add_display_grade import migrate_extracted_fields
        from sqlalchemy.ext.asyncio import create_async_engine
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        r = await migrate_extracted_fields(engine)
        await engine.dispose()
        return r
    r = asyncio.run(_t())
    assert not r["errors"]
    # 列 display_grade / cross_verified 已 either migrated or skipped
    assert r["table"] is True  # Base.metadata.create_all 会创建


# ========== 覆盖率补全：_sl_value(sl=None) + output_strategies edge cases ==========

def test_display_grade__support_level_none():
    """补 display_grade.py line 47：support_level=None -> UNSUPPORTED -> LOW"""
    # sl is None 会走到 _sl_value 第1分支
    grade = compute_display_grade(support_level=None, source_role="official_original")
    assert grade == GRADE_LOW


def test_output_strategies__plain_tuple_fallback():
    """补 output_strategies.py 33 & 41：输入的 field 既不是对象也不是 dict（例如一个字符串/元组）
    -> _g 走 fallback=REVIEW, _sl 走 fallback="" """
    weird = [
        ("just", "a", "tuple", 42),  # 非对象非dict
    ]
    res = filter_by_strategy(weird, "audit")
    # audit 全留
    assert len(res) == 1
    # default: field.grade 默认为 REVIEW, sl="" 不是strong
    res2 = filter_by_strategy(weird, "default")
    assert res2 == []  # REVIEW + sl not strong -> default 不保留 (只有 strict留high, default留high+REVIEW+strong)
    # loose：high + all review
    res3 = filter_by_strategy(weird, "loose")
    assert len(res3) == 1  # REVIEW 保留


def test_output_strategies__support_level_none():
    """补 output_strategies.py 38：hasattr 对象且 support_level=None，
    且 strategy=default + grade=REVIEW → 真正触发 _sl 调用 str(field.support_level or "") """

    class O:
        def __init__(self, grade, sl):
            self.display_grade = grade
            self.support_level = sl

    # grade=REVIEW + sl=None → default 不保留 (_sl 返回 "" 不在 _STRENGTH_HIGH)
    assert len(filter_by_strategy([O(GRADE_REVIEW, None)], "default")) == 0
    # grade=REVIEW + sl=None → loose 保留
    assert len(filter_by_strategy([O(GRADE_REVIEW, None)], "loose")) == 1
    # grade=REVIEW + sl="direct" → default 保留 (_sl="direct" ∈ STRONG)
    assert len(filter_by_strategy([O(GRADE_REVIEW, "direct")], "default")) == 1


def test_output_strategies__dict_grade_none():
    """补 output_strategies.py 32：dict 里 display_grade=None -> or GRADE_REVIEW"""
    weird = [{"display_grade": None, "support_level": "direct", "n": "f1"}]
    r = filter_by_strategy(weird, "default")
    # grade=None -> REVIEW ; sl=direct -> STRONG strong_support("direct") -> True
    # default: high 或 (REVIEW and strong) -> 保留
    assert len(r) == 1


def test_output_strategies__dict_missing_support_level():
    """补 output_strategies.py 38 右半：dict 没提供 support_level key（或为空）。
    field.get("support_level", "") or "" → "" → str.lower() → "" """
    d1 = {"display_grade": GRADE_HIGH}  # 无 support_level key
    d2 = {"display_grade": GRADE_REVIEW, "support_level": None}  # 显式 None
    # audit 保留全部
    assert len(filter_by_strategy([d1], "audit")) == 1
    assert len(filter_by_strategy([d2], "audit")) == 1
    # d1 是 HIGH，default 也保留
    assert len(filter_by_strategy([d1], "default")) == 1
    # d2 是 REVIEW 但 sl=None → "" → not strong → default 不保留；loose 才保留
    assert len(filter_by_strategy([d2], "default")) == 0
    assert len(filter_by_strategy([d2], "loose")) == 1

